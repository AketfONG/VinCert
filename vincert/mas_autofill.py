"""EAMS / Maximo browser automation via Playwright (persistent login session)."""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import CertificateFields

# Production (正式环境)
EAMS_HOME_PROD = (
    "https://eams.manage.mas.mtr.bj.cn/maximo/oslc/graphite/"
    "manage-shell/index.html#/main"
)
EAMS_PORTAL_PROD = "https://eams.home.mas.mtr.bj.cn/"
EAMS_LOGIN_PROD = "https://auth.mas.mtr.bj.cn/login/"

# UAT / testing (测试环境) — same Maximo flow, different hosts
EAMS_HOME_UAT = (
    "https://eams.manage.masuat.apps.ocpuat.mtr.bj.cn/maximo/oslc/graphite/"
    "manage-shell/index.html#/main"
)
EAMS_PORTAL_UAT = "https://eams.home.masuat.apps.ocpuat.mtr.bj.cn/"
EAMS_LOGIN_UAT = "https://auth.masuat.apps.ocpuat.mtr.bj.cn/login/"

# Back-compat aliases (default = production)
EAMS_HOME = EAMS_HOME_PROD
EAMS_LOGIN_URL = EAMS_LOGIN_PROD
MAS_HOME = EAMS_HOME
SHELL_IFRAME = "#manage-shell_Iframe"
UPLOAD_IFRAME = "#upload_iframe"

# Default profile lives next to the project so login cookies persist across runs.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_USER_DATA_DIR = PROJECT_ROOT / "mas_browser_data"
UAT_USER_DATA_DIR = PROJECT_ROOT / "mas_browser_data_uat"
CREDENTIALS_PATH = PROJECT_ROOT / "eams_credentials.json"
EXPORTS_DIR = PROJECT_ROOT / "exports"
FAILED_ITEMS_DIR = PROJECT_ROOT / "failed_items"
# How long autofill may wait for login / shell to appear (manual login OK).
DEFAULT_LOGIN_WAIT_SECONDS = 120

# Keep Playwright + persistent context alive after a run so Chromium stays open.
_keepalive_lock = threading.Lock()
_keepalive_playwright = None
_keepalive_context = None

StatusFn = Callable[[str], None]


def _default_downloads_dir() -> Path:
    """Prefer the OS Downloads folder so Excel opens like a normal browser save."""
    home = Path.home()
    for candidate in (home / "Downloads", home / "下载"):
        try:
            if candidate.is_dir():
                return candidate
        except Exception:  # noqa: BLE001
            continue
    fallback = PROJECT_ROOT / "browser_downloads"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _unique_download_target(directory: Path, filename: str) -> Path:
    safe = Path(filename or "download.bin").name
    target = directory / safe
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    n = 1
    while True:
        candidate = directory / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _wire_browser_downloads(context, *, status: StatusFn | None = None) -> Path:
    """Save Playwright-intercepted downloads as complete files with real names.

    Playwright routes attachments through a temp GUID path; opening that too
    early (or via the download shelf) often yields a corrupt .xlsx. We wait for
    completion and ``save_as`` into the user Downloads folder.
    """
    dest = _default_downloads_dir()
    dest.mkdir(parents=True, exist_ok=True)

    def _on_download(download) -> None:
        try:
            failure = download.failure()
            if failure:
                _status(status, f"下载失败：{failure}")
                return
            name = download.suggested_filename or "download.bin"
            target = _unique_download_target(dest, name)
            download.save_as(str(target))
            # Basic sanity: Excel is a ZIP (xlsx) or OLE (xls); HTML error pages are not.
            try:
                size = target.stat().st_size
            except Exception:  # noqa: BLE001
                size = -1
            if size < 64:
                _status(status, f"下载异常（文件过小 {size}B）：{target.name}")
                return
            head = b""
            try:
                with target.open("rb") as fh:
                    head = fh.read(8)
            except Exception:  # noqa: BLE001
                pass
            lower = name.lower()
            if lower.endswith((".xlsx", ".xlsm", ".xls")) and not (
                head.startswith(b"PK")
                or head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
            ):
                _status(
                    status,
                    f"下载内容不像 Excel（可能是网页错误页）：{target.name}",
                )
                return
            _status(status, f"已保存下载：{target.name} → {dest}")
        except Exception as exc:  # noqa: BLE001
            _status(status, f"下载保存失败：{exc}")

    def _bind_page(page) -> None:
        page.on("download", _on_download)

    context.on("page", lambda page: _bind_page(page))
    for page in list(context.pages or []):
        _bind_page(page)
    return dest


@dataclass(frozen=True)
class EamsEnvironment:
    """Resolved EAMS hosts + persistent Chromium profile for one environment."""

    key: str
    label: str
    home: str
    portal: str
    login: str
    user_data_dir: Path
    post_login_url_glob: str


def resolve_eams_environment(*, testing: bool = False) -> EamsEnvironment:
    """Return production or UAT endpoints (and matching browser profile)."""
    if testing:
        return EamsEnvironment(
            key="uat",
            label="测试环境",
            home=EAMS_HOME_UAT,
            portal=EAMS_PORTAL_UAT,
            login=EAMS_LOGIN_UAT,
            user_data_dir=UAT_USER_DATA_DIR,
            post_login_url_glob="**/eams.manage.masuat.apps.ocpuat.mtr.bj.cn/**",
        )
    return EamsEnvironment(
        key="prod",
        label="正式环境",
        home=EAMS_HOME_PROD,
        portal=EAMS_PORTAL_PROD,
        login=EAMS_LOGIN_PROD,
        user_data_dir=DEFAULT_USER_DATA_DIR,
        post_login_url_glob="**/eams.manage.mas.mtr.bj.cn/**",
    )


@dataclass
class AutofillItem:
    """One approved certificate to push into MAS."""

    fields: CertificateFields
    pdf_path: str | None = None


@dataclass
class AutofillReport:
    imported_excel: bool = False
    filled: int = 0
    uploaded: int = 0
    errors: list[str] | None = None
    # PDF paths that failed checks (mismatch / 需要确认结果 / missing PDF / upload)
    # and should be copied to the configured failed-items folder.
    quarantine_paths: list[str] | None = None
    cancelled: bool = False

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []
        if self.quarantine_paths is None:
            self.quarantine_paths = []


class AutofillItemError(Exception):
    """One certificate failed; optionally quarantine its PDF."""

    def __init__(self, message: str, *, quarantine: bool = False):
        super().__init__(message)
        self.quarantine = quarantine


class AutofillCancelled(Exception):
    """User requested exit from the autofill run."""


class AutofillBatchImportError(Exception):
    """EAMS batch Excel import failed (e.g. EXCEL导入错误)."""


class AutofillControl:
    """Cross-thread pause / resume / exit signals for ``run_mas_autofill``."""

    def __init__(self) -> None:
        self._pause = threading.Event()
        self._cancel = threading.Event()
        self._context = None
        self._browser = None
        self._browser_pid: int | None = None
        self._user_data_dir: Path | None = None
        self._context_lock = threading.Lock()

    def bind_context(self, context, *, user_data_dir: Path | None = None) -> None:
        with self._context_lock:
            self._context = context
            if user_data_dir is not None:
                self._user_data_dir = Path(user_data_dir)
            browser = None
            pid = None
            try:
                browser = getattr(context, "browser", None)
            except Exception:  # noqa: BLE001
                browser = None
            # Persistent contexts often expose browser=None; dig into impl / pages.
            if browser is None:
                try:
                    impl = getattr(context, "_impl_obj", None)
                    browser = getattr(impl, "_browser", None) or getattr(impl, "browser", None)
                except Exception:  # noqa: BLE001
                    browser = None
            if browser is not None:
                try:
                    proc = getattr(browser, "process", None)
                    if proc is not None:
                        pid = int(proc.pid)
                except Exception:  # noqa: BLE001
                    pid = None
            self._browser = browser
            self._browser_pid = pid

    def clear_context(self) -> None:
        with self._context_lock:
            self._context = None
            self._browser = None
            self._browser_pid = None

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def is_paused(self) -> bool:
        return self._pause.is_set()

    def request_exit(self) -> None:
        """Signal cancel. Does not close the browser — session stays open for the user."""
        self._cancel.set()
        self._pause.clear()  # unblock any pause wait
        # Intentionally do not close / kill Chromium: autofill must leave the
        # window open after stop, Excel error, or normal completion.

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def checkpoint(self, status: StatusFn | None = None) -> None:
        """Block while paused; raise ``AutofillCancelled`` if exit was requested."""
        while self._pause.is_set():
            if self._cancel.is_set():
                raise AutofillCancelled("用户退出自动填写")
            time.sleep(0.12)
        if self._cancel.is_set():
            raise AutofillCancelled("用户退出自动填写")


def _kill_process(pid: int) -> None:
    """Best-effort kill of a Chromium process (unblocks hung Playwright waits)."""
    try:
        import os
        import signal

        os.kill(pid, signal.SIGTERM)
    except Exception:  # noqa: BLE001
        pass
    if sys.platform == "win32":
        try:
            import subprocess

            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
            )
        except Exception:  # noqa: BLE001
            pass
    else:
        try:
            import os
            import signal

            os.kill(pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass


def _kill_chromium_for_profile(user_data_dir: Path) -> None:
    """Kill Chromium processes whose command line includes this user-data-dir."""
    marker = str(Path(user_data_dir).resolve())
    if not marker:
        return
    try:
        import subprocess
    except Exception:  # noqa: BLE001
        return

    if sys.platform == "win32":
        # Escape single quotes for PowerShell single-quoted string.
        safe = marker.replace("'", "''")
        script = (
            "$m = '{0}'; "
            "Get-CimInstance Win32_Process | "
            "Where-Object {{ "
            "  $_.Name -match '^(chrome|chromium|msedge)\\.exe$' -and "
            "  $_.CommandLine -and ($_.CommandLine -like ('*' + $m + '*')) "
            "}} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
        ).format(safe)
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                check=False,
                capture_output=True,
                timeout=8,
            )
        except Exception:  # noqa: BLE001
            pass
        return

    try:
        subprocess.run(
            ["pkill", "-f", marker],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        pass


def close_keepalive_browser() -> None:
    """Close any autofill Chromium left open from a previous run."""
    global _keepalive_playwright, _keepalive_context
    with _keepalive_lock:
        ctx = _keepalive_context
        pw = _keepalive_playwright
        _keepalive_context = None
        _keepalive_playwright = None
    if ctx is not None:
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
    if pw is not None:
        try:
            pw.stop()
        except Exception:  # noqa: BLE001
            pass


def _retain_keepalive(playwright, context) -> None:
    """Replace any prior keepalive session with the current browser."""
    global _keepalive_playwright, _keepalive_context
    with _keepalive_lock:
        old_ctx = _keepalive_context
        old_pw = _keepalive_playwright
        _keepalive_context = context
        _keepalive_playwright = playwright
    if old_ctx is not None and old_ctx is not context:
        try:
            old_ctx.close()
        except Exception:  # noqa: BLE001
            pass
    if old_pw is not None and old_pw is not playwright:
        try:
            old_pw.stop()
        except Exception:  # noqa: BLE001
            pass


COMPARE_FIELD_LABELS = {
    "serial_num": "计量器具编号",
    "manufacturer": "制造厂",
}


def _norm_compare(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def _status(cb: StatusFn | None, message: str) -> None:
    if cb:
        cb(message)


def _click(status: StatusFn | None, locator, label: str, *, timeout: float = 8000):
    """Click a Playwright locator and report the UI action."""
    _status(status, f"点击「{label}」")
    locator.click(timeout=timeout)


def _shell(page):
    return page.locator(SHELL_IFRAME).content_frame


def _upload_frame(shell):
    return shell.locator(UPLOAD_IFRAME).content_frame


def _fill_textbox(
    frame,
    name: str,
    value: str,
    *,
    timeout: float = 8000,
    status: StatusFn | None = None,
) -> None:
    if not value:
        return
    box = frame.get_by_role("textbox", name=name)
    _click(status, box, name, timeout=timeout)
    box.fill(value, timeout=timeout)
    box.press("Tab")


def _open_maximo_dropdown(
    frame,
    name: str,
    *,
    timeout: float = 8000,
    status: StatusFn | None = None,
) -> None:
    """Open a Maximo combobox via field click and/or 下拉映像."""
    # Prefer the explicit dropdown glyph used by Maximo look-ups.
    try:
        arrow = frame.get_by_label(name).get_by_role("img", name="下拉映像")
        if arrow.count() > 0 and arrow.first.is_visible():
            _click(status, arrow.first, f"{name}·下拉", timeout=timeout)
            return
    except Exception:  # noqa: BLE001
        pass
    _click(status, frame.get_by_role("combobox", name=name), name, timeout=timeout)


def _dropdown_value_candidates(value: str) -> list[str]:
    """Return likely option labels for a certificate field value."""
    raw = (value or "").strip()
    if not raw:
        return []
    aliases = {
        "检定": ["检定", "Verification", "verification"],
        "校准": ["校准", "Calibration", "calibration"],
        "校验": ["校验", "校准/校验", "Check", "check"],
    }
    out: list[str] = []
    for token in [raw, *aliases.get(raw, [])]:
        if token and token not in out:
            out.append(token)
    # Also try without common trailing punctuation / whitespace variants.
    compact = "".join(raw.split())
    if compact and compact not in out:
        out.append(compact)
    return out


def _click_dropdown_option(
    frame,
    value: str,
    *,
    timeout: float = 8000,
    status: StatusFn | None = None,
    page=None,
) -> bool:
    """Try to click an open Maximo dropdown option. Returns True on success."""
    candidates = _dropdown_value_candidates(value)
    # Let the popup paint.
    time.sleep(0.28)

    scopes = [frame]
    root = page
    if root is None:
        root = getattr(frame, "page", None)
    if root is not None and root is not frame:
        scopes.append(root)

    for scope in scopes:
        for candidate in candidates:
            getters = (
                lambda c=candidate, s=scope: s.get_by_role(
                    "menuitem", name=c, exact=True
                ),
                lambda c=candidate, s=scope: s.get_by_role(
                    "option", name=c, exact=True
                ),
                lambda c=candidate, s=scope: s.get_by_text(c, exact=True),
                lambda c=candidate, s=scope: s.get_by_role("menuitem", name=c),
                lambda c=candidate, s=scope: s.get_by_text(c, exact=False),
            )
            for getter in getters:
                try:
                    loc = getter()
                    if loc.count() <= 0:
                        continue
                    node = loc.first
                    try:
                        node.wait_for(state="visible", timeout=min(2500, timeout))
                    except Exception:  # noqa: BLE001
                        pass
                    _click(status, node, candidate, timeout=timeout)
                    return True
                except Exception:  # noqa: BLE001
                    continue
    return False


def _select_combobox(
    frame,
    name: str,
    value: str,
    *,
    timeout: float = 8000,
    status: StatusFn | None = None,
    page=None,
) -> None:
    if not value:
        return
    target = (value or "").strip()
    _open_maximo_dropdown(frame, name, timeout=timeout, status=status)
    if _click_dropdown_option(
        frame, target, timeout=timeout, status=status, page=page
    ):
        return

    # Type-to-filter fallback (editable Maximo comboboxes).
    try:
        box = frame.get_by_role("combobox", name=name)
        _click(status, box, name, timeout=timeout)
        box.fill(target, timeout=timeout)
        if _click_dropdown_option(
            frame, target, timeout=timeout, status=status, page=page
        ):
            return
        box.press("Enter")
        time.sleep(0.2)
        # If the combobox retained our value, treat as success.
        try:
            current = (box.input_value(timeout=1500) or "").strip()
            if target in current or current in target:
                return
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass

    # One more open+click attempt after typing.
    try:
        _open_maximo_dropdown(frame, name, timeout=timeout, status=status)
        if _click_dropdown_option(
            frame, target, timeout=timeout, status=status, page=page
        ):
            return
    except Exception:  # noqa: BLE001
        pass

    raise RuntimeError(f"无法选择下拉项：{name} = {value}")


def ensure_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "未安装 Playwright。请运行：\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        ) from exc
    return sync_playwright


def load_credentials(path: Path | None = None) -> tuple[str, str]:
    """Load saved EAMS username/password from a local JSON file."""
    cred_path = Path(path or CREDENTIALS_PATH)
    if not cred_path.exists():
        return "", ""
    try:
        data = json.loads(cred_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "", ""
    return str(data.get("username") or ""), str(data.get("password") or "")


def save_credentials(username: str, password: str, path: Path | None = None) -> Path:
    """Persist EAMS credentials locally (gitignored)."""
    cred_path = Path(path or CREDENTIALS_PATH)
    cred_path.write_text(
        json.dumps(
            {"username": username, "password": password},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return cred_path


def _already_logged_in(page, *, timeout_ms: int = 3000) -> bool:
    try:
        page.locator(SHELL_IFRAME).wait_for(state="attached", timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001
        return False


def _manage_chooser_visible(page) -> bool:
    """True when the post-auth app chooser (Available Manage / 启动) is showing."""
    checks = (
        ("link", "Available Manage", False),
        ("link", "启动", True),
        ("button", "close", True),
    )
    for role, name, exact in checks:
        try:
            loc = page.get_by_role(role, name=name, exact=exact)
            if loc.count() > 0 and bool(loc.first.is_visible()):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _login_form_visible(page) -> bool:
    """True when the MAS username field is ready for autofill."""
    try:
        box = page.get_by_role("textbox", name="输入用户名")
        return box.count() > 0 and bool(box.first.is_visible())
    except Exception:  # noqa: BLE001
        return False


def _fill_username_field(page, username: str) -> None:
    # UAT/prod MAS auth portal uses accessible name「输入用户名」(codegen).
    for name in ("输入用户名", "用户名", "账号", "Username", "User name", "user"):
        try:
            box = page.get_by_role("textbox", name=name)
            box.click(timeout=2500)
            box.fill(username, timeout=2500)
            return
        except Exception:  # noqa: BLE001
            continue
    for selector in (
        'input[name="username"]',
        'input[id="username"]',
        'input[name="userName"]',
        'input[type="text"]',
        'input[type="email"]',
    ):
        loc = page.locator(selector)
        try:
            if loc.count() > 0:
                loc.first.click(timeout=2500)
                loc.first.fill(username, timeout=2500)
                return
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("未找到用户名输入框")


def _fill_password_field(page, password: str) -> None:
    # UAT/prod MAS auth portal uses accessible name「输入密码」(codegen).
    for name in ("输入密码", "密码", "Password"):
        try:
            box = page.get_by_role("textbox", name=name)
            box.fill(password, timeout=4000)
            return
        except Exception:  # noqa: BLE001
            continue
    for name in ("输入密码", "密码", "Password"):
        try:
            page.get_by_label(name).fill(password, timeout=4000)
            return
        except Exception:  # noqa: BLE001
            continue
    loc = page.locator('input[type="password"]')
    if loc.count() == 0:
        raise RuntimeError("未找到密码输入框")
    loc.first.fill(password, timeout=4000)


def _click_auth_primary_button(
    page,
    *,
    label: str,
    status: StatusFn | None = None,
    timeout: float = 5000,
) -> None:
    """Click the MAS auth primary button (codegen: get_by_test_id('Button'))."""
    # Recorded UAT flow uses data-testid="Button" for both 继续 and 登录.
    try:
        btn = page.get_by_test_id("Button")
        _status(status, f"点击「{label}」")
        btn.click(timeout=timeout)
        return
    except Exception:  # noqa: BLE001
        pass

    for name in (
        label,
        "继续",
        "Continue",
        "下一步",
        "Next",
        "登录",
        "登 录",
        "Sign in",
        "Login",
        "提交",
    ):
        try:
            _click(status, page.get_by_role("button", name=name), name, timeout=timeout)
            return
        except Exception:  # noqa: BLE001
            continue

    submit = page.locator('button[type="submit"], input[type="submit"]')
    if submit.count() == 0:
        raise RuntimeError(f"未找到按钮：{label}")
    _click(status, submit.first, label, timeout=timeout)


def _fill_eams_login_form(
    page, username: str, password: str, *, status: StatusFn | None = None
) -> None:
    """MAS auth (UAT/prod): 输入用户名 → Button → 输入密码 → Button."""
    _status(status, "填写用户名…")
    _fill_username_field(page, username)

    _click_auth_primary_button(page, label="继续", status=status)

    # Password step appears after 继续 / primary Button.
    page.wait_for_timeout(500)
    try:
        page.get_by_role("textbox", name="输入密码").wait_for(
            state="visible", timeout=10_000
        )
    except Exception:  # noqa: BLE001
        try:
            page.locator('input[type="password"]').first.wait_for(
                state="visible", timeout=5000
            )
        except Exception:  # noqa: BLE001
            pass

    _status(status, "填写密码…")
    _fill_password_field(page, password)

    _click_auth_primary_button(page, label="登录", status=status)


def _post_login_enter_manage(
    page,
    *,
    status: StatusFn | None = None,
    control: AutofillControl | None = None,
) -> None:
    """App chooser: close → Available Manage → dismiss dialog → 启动.

    Also used on consecutive runs when the session skips password and lands
    directly on the Manage chooser page.
    """

    def check():
        if control is not None:
            control.checkpoint(status)

    check()
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(500)

    check()
    _status(status, "关闭提示…")
    close_btn = page.get_by_role("button", name="close", exact=True)
    try:
        close_btn.wait_for(state="visible", timeout=8_000)
        _click(status, close_btn, "close", timeout=8_000)
    except Exception:  # noqa: BLE001
        _status(status, "未找到 close，继续…")

    check()
    _status(status, "打开 Available Manage…")
    manage = page.get_by_role("link", name="Available Manage")
    manage.wait_for(state="visible", timeout=20_000)
    _click(status, manage, "Available Manage", timeout=12_000)

    check()
    page.once("dialog", lambda dialog: dialog.dismiss())
    _status(status, "点击启动…")
    launch = page.get_by_role("link", name="启动", exact=True)
    launch.wait_for(state="visible", timeout=20_000)
    _click(status, launch, "启动", timeout=12_000)
    page.wait_for_timeout(500)


def _wait_for_manage_shell(
    page,
    target: EamsEnvironment,
    *,
    login_wait_seconds: int,
    status: StatusFn | None = None,
    control: AutofillControl | None = None,
) -> None:
    """Poll until Maximo shell is up after Manage entry (or existing session)."""

    def check():
        if control is not None:
            control.checkpoint(status)

    deadline = time.time() + max(login_wait_seconds, 30)
    _status(status, "等待进入 EAMS…")
    navigated_home = False
    while True:
        check()
        try:
            page.wait_for_selector(SHELL_IFRAME, timeout=2000)
            _status(status, f"EAMS 登录成功（{target.label}）")
            return
        except AutofillCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            if control is not None and control.cancelled():
                raise AutofillCancelled("用户退出自动填写") from exc

        check()
        try:
            page.wait_for_url(target.post_login_url_glob, timeout=1500)
            if not navigated_home:
                check()
                page.goto(target.home, wait_until="domcontentloaded")
                navigated_home = True
            continue
        except AutofillCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            if control is not None and control.cancelled():
                raise AutofillCancelled("用户退出自动填写") from exc

        if time.time() >= deadline:
            break

    check()
    try:
        page.goto(target.home, wait_until="domcontentloaded")
        page.wait_for_selector(SHELL_IFRAME, timeout=8000)
        _status(status, f"EAMS 登录成功（{target.label}）")
        return
    except AutofillCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        if control is not None and control.cancelled():
            raise AutofillCancelled("用户退出自动填写") from exc
        raise TimeoutError(
            f"登录超时（{target.label}）：未进入 EAMS 主界面。"
            "请确认账号密码或在浏览器中手动登录后重试。"
        ) from exc


def login_eams(
    page,
    username: str,
    password: str,
    *,
    login_wait_seconds: int = DEFAULT_LOGIN_WAIT_SECONDS,
    status: StatusFn | None = None,
    env: EamsEnvironment | None = None,
    control: AutofillControl | None = None,
) -> None:
    """Open EAMS auth portal and autofill credentials when needed.

    Consecutive runs may skip the password form and land on the Manage
    chooser — that path still runs close → Available Manage → 启动.
    """
    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        raise ValueError("请先在设置中填写 EAMS 用户名和密码")

    def check():
        if control is not None:
            control.checkpoint(status)

    target = env or resolve_eams_environment(testing=False)

    check()
    _status(status, f"打开 EAMS 登录页（{target.label}）…")
    try:
        page.goto(target.login, wait_until="domcontentloaded")
    except Exception:  # noqa: BLE001
        try:
            page.goto(target.portal, wait_until="domcontentloaded")
        except Exception:  # noqa: BLE001
            page.goto(target.home, wait_until="domcontentloaded")

    check()
    if _already_logged_in(page, timeout_ms=2500):
        _status(status, "已检测到登录会话（Manage 已打开）")
        return

    # Session cookie present: auth redirects to app chooser (no password).
    if _manage_chooser_visible(page):
        _status(status, "已登录，进入 Available Manage…")
        try:
            _post_login_enter_manage(page, status=status, control=control)
        except AutofillCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            _status(status, f"进入 Manage 步骤未完成：{exc}")
        _wait_for_manage_shell(
            page,
            target,
            login_wait_seconds=login_wait_seconds,
            status=status,
            control=control,
        )
        return

    # Wait briefly for either the login form or the Manage chooser.
    try:
        page.get_by_role("textbox", name="输入用户名").wait_for(
            state="visible", timeout=8_000
        )
    except Exception:  # noqa: BLE001
        pass

    check()
    if _already_logged_in(page, timeout_ms=1000):
        _status(status, "已检测到登录会话（Manage 已打开）")
        return

    if not _login_form_visible(page) and not _manage_chooser_visible(page):
        # Auth may send us elsewhere — try the EAMS portal chooser next.
        try:
            check()
            _status(status, f"打开 EAMS 门户（{target.label}）…")
            page.goto(target.portal, wait_until="domcontentloaded")
        except Exception:  # noqa: BLE001
            pass

    check()
    if _already_logged_in(page, timeout_ms=1500):
        _status(status, "已检测到登录会话（Manage 已打开）")
        return

    if _manage_chooser_visible(page):
        _status(status, "已登录，进入 Available Manage…")
        try:
            _post_login_enter_manage(page, status=status, control=control)
        except AutofillCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            _status(status, f"进入 Manage 步骤未完成：{exc}")
        _wait_for_manage_shell(
            page,
            target,
            login_wait_seconds=login_wait_seconds,
            status=status,
            control=control,
        )
        return

    if not _login_form_visible(page):
        # Last chance: home may already be the shell, or show login redirect.
        try:
            check()
            page.goto(target.home, wait_until="domcontentloaded")
            if _already_logged_in(page, timeout_ms=2000):
                _status(status, "已检测到登录会话（Manage 已打开）")
                return
            if _manage_chooser_visible(page):
                _status(status, "已登录，进入 Available Manage…")
                _post_login_enter_manage(page, status=status, control=control)
                _wait_for_manage_shell(
                    page,
                    target,
                    login_wait_seconds=login_wait_seconds,
                    status=status,
                    control=control,
                )
                return
            page.goto(target.login, wait_until="domcontentloaded")
            page.get_by_role("textbox", name="输入用户名").wait_for(
                state="visible", timeout=8000
            )
        except AutofillCancelled:
            raise
        except Exception:  # noqa: BLE001
            pass

    if _manage_chooser_visible(page) and not _login_form_visible(page):
        _status(status, "已登录，进入 Available Manage…")
        try:
            _post_login_enter_manage(page, status=status, control=control)
        except AutofillCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            _status(status, f"进入 Manage 步骤未完成：{exc}")
        _wait_for_manage_shell(
            page,
            target,
            login_wait_seconds=login_wait_seconds,
            status=status,
            control=control,
        )
        return

    _status(status, "正在自动填写登录信息…")
    check()
    _fill_eams_login_form(page, username, password, status=status)
    check()

    try:
        _post_login_enter_manage(page, status=status, control=control)
    except AutofillCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        _status(status, f"进入 Manage 步骤未完成：{exc}")

    _wait_for_manage_shell(
        page,
        target,
        login_wait_seconds=login_wait_seconds,
        status=status,
        control=control,
    )


def next_export_path(prefix: str = "vincert_batch") -> Path:
    """Timestamped Excel path under the project ``exports/`` folder."""
    from datetime import datetime

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return EXPORTS_DIR / f"{prefix}_{stamp}.xlsx"


def write_batch_excel(rows: list[list[str]], headers: list[str], path: Path) -> Path:
    """Write the MAS batch-import workbook (名称/编号/型号/计量单位/计量日期/计量类型)."""
    from openpyxl import Workbook  # pyright: ignore[reportMissingModuleSource]

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "VinCert"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    for idx, header in enumerate(headers, start=1):
        max_len = len(str(header))
        for row in rows:
            if idx - 1 < len(row):
                max_len = max(max_len, len(str(row[idx - 1])))
        sheet.column_dimensions[chr(64 + idx)].width = min(max_len + 4, 36)
    workbook.save(path)
    return path


def _set_browser_window_bounds(page, bounds: tuple[int, int, int, int]) -> None:
    """Place the Chromium window via CDP bounds (no Win+Arrow snap)."""
    left, top, width, height = bounds
    width = max(400, int(width))
    height = max(400, int(height))
    session = page.context.new_cdp_session(page)
    info = session.send("Browser.getWindowForTarget")
    session.send(
        "Browser.setWindowBounds",
        {
            "windowId": info["windowId"],
            "bounds": {
                "left": int(left),
                "top": int(top),
                "width": width,
                "height": height,
                "windowState": "normal",
            },
        },
    )


def _step_pause(
    gate: AutofillControl,
    seconds: float,
    *,
    status: StatusFn | None = None,
) -> None:
    """Wait between major autofill steps while still honoring pause/exit."""
    if seconds <= 0:
        gate.checkpoint(status)
        return
    gate.checkpoint(status)
    deadline = time.time() + float(seconds)
    while True:
        gate.checkpoint(status)
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(0.12, remaining))


def run_mas_autofill(
    items: list[AutofillItem],
    *,
    excel_path: Path | None = None,
    excel_headers: list[str] | None = None,
    excel_rows: list[list[str]] | None = None,
    user_data_dir: Path | None = None,
    username: str = "",
    password: str = "",
    headless: bool = False,
    slow_mo: int = 100,
    batch_import: bool = True,
    fill_details: bool = True,
    upload_pdf: bool = True,
    submit_workflow: bool = False,
    login_wait_seconds: int = DEFAULT_LOGIN_WAIT_SECONDS,
    status: StatusFn | None = None,
    control: AutofillControl | None = None,
    testing: bool = False,
    window_bounds: tuple[int, int, int, int] | None = None,
    step_delay_sec: float = 1.0,
    shared_context=None,
    shared_page=None,
) -> AutofillReport:
    """
    Drive EAMS 计量器具结果录入 using a persistent Chromium profile.

    If username/password are provided, the login form is autofilled when needed.
    Pass ``control`` to support pause / continue / exit from the UI.
    Set ``testing=True`` to use the UAT hosts and a separate browser profile.
    ``window_bounds`` snaps the browser like the PDF preview (left=app, right=browser).
    ``step_delay_sec`` pauses between major automation steps (default 1s).
    Pass ``shared_context`` + ``shared_page`` to reuse the PDF preview Chromium
    window (EAMS runs in its own tab and is not closed afterward).
    """
    if not items and not (batch_import and excel_rows):
        raise ValueError("没有可自动填写的条目")

    report = AutofillReport()
    env = resolve_eams_environment(testing=testing)
    profile = Path(user_data_dir or env.user_data_dir)
    profile.mkdir(parents=True, exist_ok=True)
    gate = control or AutofillControl()
    delay = max(0.0, float(step_delay_sec))
    owns_browser = shared_context is None or shared_page is None

    def check():
        gate.checkpoint(status)

    def pause_step(label: str | None = None):
        if label:
            _status(status, label)
        _step_pause(gate, delay, status=status)

    playwright = None
    context = None
    page = None
    try:
        check()
        if owns_browser:
            sync_playwright = ensure_playwright()
            launch_args: list[str] = []
            if window_bounds is not None:
                left, top, width, height = window_bounds
                launch_args.extend(
                    [
                        f"--window-position={int(left)},{int(top)}",
                        f"--window-size={max(400, int(width))},{max(400, int(height))}",
                    ]
                )
            # A prior kept-alive session must be closed before reusing the profile dir.
            close_keepalive_browser()
            playwright = sync_playwright().start()
            _status(status, f"正在启动浏览器（{env.label}）…")
            pw_dl = PROJECT_ROOT / "browser_downloads" / "playwright_tmp"
            pw_dl.mkdir(parents=True, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=headless,
                slow_mo=slow_mo,
                no_viewport=True,
                accept_downloads=True,
                downloads_path=str(pw_dl),
                args=launch_args or None,
            )
            page = context.pages[0] if context.pages else context.new_page()
            if window_bounds is not None:
                try:
                    _set_browser_window_bounds(page, window_bounds)
                except Exception:  # noqa: BLE001
                    pass
        else:
            context = shared_context
            page = shared_page
            _status(status, f"使用共享浏览器（{env.label}）…")
            if window_bounds is not None:
                try:
                    _set_browser_window_bounds(page, window_bounds)
                except Exception:  # noqa: BLE001
                    pass

        downloads_dir = _wire_browser_downloads(context, status=status)
        _status(status, f"浏览器下载将保存到：{downloads_dir}")
        gate.bind_context(context, user_data_dir=profile)

        check()
        pause_step()
        if username and password:
            login_eams(
                page,
                username,
                password,
                login_wait_seconds=login_wait_seconds,
                status=status,
                env=env,
                control=gate,
            )
        else:
            _status(status, f"打开 EAMS 主页（{env.label}；如需登录请在浏览器中完成）…")
            page.goto(env.home, wait_until="domcontentloaded")
        check()
        pause_step()
        shell = _wait_for_shell(
            page,
            login_wait_seconds=login_wait_seconds,
            status=status,
            control=gate,
        )

        check()
        pause_step()
        _status(status, "进入「计量器具结果录入」…")
        _open_measure_app(shell, status=status)

        if batch_import and excel_path:
            check()
            pause_step()
            excel_path = Path(excel_path)
            if not excel_path.exists():
                if excel_rows is None or excel_headers is None:
                    raise FileNotFoundError(f"Excel 不存在且未提供数据行：{excel_path}")
                write_batch_excel(excel_rows, excel_headers, excel_path)
            _status(status, f"批量导入 Excel：{excel_path.name}")
            _batch_import_excel(
                shell,
                excel_path,
                status=status,
                control=gate,
                wait_seconds=login_wait_seconds,
            )
            report.imported_excel = True
            check()
            pause_step("批量导入已完成，开始逐份自动填写…")

        if fill_details or upload_pdf:
            # PDF upload is compulsory for each approved certificate.
            upload_pdf = True
            for i, item in enumerate(items, start=1):
                check()
                pause_step()
                serial = (item.fields.serial_num or "").strip()
                label = serial or item.fields.name or f"#{i}"
                pdf = Path(item.pdf_path) if item.pdf_path else None
                try:
                    if pdf is None or not pdf.is_file():
                        raise AutofillItemError(
                            "缺少对应 PDF，无法上传附件",
                            quarantine=True,
                        )
                    _status(status, f"填写第 {i}/{len(items)} 份：{label}")
                    check()
                    pause_step()
                    _open_record_by_serial(shell, serial, status=status)
                    check()
                    pause_step()
                    _verify_compare_fields(shell, item.fields, status=status)
                    check()
                    if _needs_result_confirm(shell, status=status):
                        raise AutofillItemError(
                            "网页勾选了「需要确认结果」，已移出自动填写队列",
                            quarantine=True,
                        )
                    if fill_details:
                        check()
                        pause_step()
                        _fill_record_fields(
                            shell, item.fields, status=status, page=page
                        )
                        report.filled += 1
                    check()
                    pause_step()
                    _upload_certificate_pdf(
                        shell, pdf, status=status, page=page
                    )
                    report.uploaded += 1
                    if submit_workflow:
                        check()
                        pause_step()
                        _submit_workflow(shell, status=status)
                except AutofillCancelled:
                    raise
                except AutofillItemError as exc:
                    if gate.cancelled():
                        raise AutofillCancelled("用户退出自动填写") from exc
                    msg = f"{label}: {exc}"
                    report.errors.append(msg)
                    _status(status, f"失败 — {msg}")
                    if exc.quarantine and item.pdf_path:
                        report.quarantine_paths.append(item.pdf_path)
                except Exception as exc:  # noqa: BLE001
                    if gate.cancelled():
                        raise AutofillCancelled("用户退出自动填写") from exc
                    msg = f"{label}: {exc}"
                    report.errors.append(msg)
                    _status(status, f"失败 — {msg}")
                    if item.pdf_path:
                        report.quarantine_paths.append(item.pdf_path)
    except AutofillCancelled as exc:
        report.cancelled = True
        _status(status, f"已退出 — {exc}")
    except AutofillBatchImportError as exc:
        report.errors.append(str(exc))
        _status(status, f"已停止 — {exc}")
    except Exception as exc:  # noqa: BLE001
        if gate.cancelled():
            report.cancelled = True
            _status(status, "已退出自动填写")
        else:
            if owns_browser and context is None and playwright is not None:
                try:
                    playwright.stop()
                except Exception:  # noqa: BLE001
                    pass
            raise
    finally:
        gate.clear_context()
        if owns_browser and context is not None and playwright is not None:
            _retain_keepalive(playwright, context)
            _status(status, "自动化结束（浏览器保持打开）")
        elif not owns_browser:
            _status(status, "自动化结束（共享浏览器保持打开）")

    return report

def open_eams_login_session(
    username: str,
    password: str,
    *,
    user_data_dir: Path | None = None,
    login_wait_seconds: int = DEFAULT_LOGIN_WAIT_SECONDS,
    status: StatusFn | None = None,
    testing: bool = False,
) -> None:
    """Launch browser, autofill EAMS login, then close after session is established."""
    sync_playwright = ensure_playwright()
    env = resolve_eams_environment(testing=testing)
    profile = Path(user_data_dir or env.user_data_dir)
    profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            slow_mo=100,
            no_viewport=True,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            login_eams(
                page,
                username,
                password,
                login_wait_seconds=login_wait_seconds,
                status=status,
                env=env,
            )
            _wait_for_shell(page, login_wait_seconds=login_wait_seconds, status=status)
            _status(status, "EAMS 登录完成，会话已保存")
        finally:
            context.close()


def _wait_for_shell(
    page,
    *,
    login_wait_seconds: int,
    status: StatusFn | None,
    control: AutofillControl | None = None,
):
    """Wait until the Maximo shell iframe is available (after optional login)."""
    deadline_ms = max(login_wait_seconds, 30) * 1000
    slice_ms = 2_000
    waited = 0
    prompted = False
    while True:
        if control is not None:
            control.checkpoint(status)
        try:
            page.wait_for_selector(SHELL_IFRAME, timeout=slice_ms)
            break
        except Exception as exc:
            if control is not None and control.cancelled():
                raise AutofillCancelled("用户退出自动填写") from exc
            waited += slice_ms
            if waited >= min(20_000, deadline_ms) and not prompted:
                _status(status, "未检测到已登录会话，请在浏览器中登录…")
                prompted = True
            if waited >= deadline_ms:
                raise TimeoutError("等待 EAMS 主界面超时") from exc

    shell = _shell(page)
    # Prefer the measure-entry link; fall back to any shell content.
    try:
        if control is not None:
            control.checkpoint(status)
        shell.get_by_role("link", name="计量器具结果录入", exact=True).wait_for(
            timeout=min(30_000, deadline_ms)
        )
    except Exception:
        if control is not None and control.cancelled():
            raise AutofillCancelled("用户退出自动填写")
        _status(status, "等待主界面菜单加载…")
        page.wait_for_timeout(2000)
    return shell


def _open_measure_app(shell, *, status: StatusFn | None = None) -> None:
    link = shell.get_by_role("link", name="计量器具结果录入", exact=True)
    _click(status, link, "计量器具结果录入")
    # App may already be open; menu item is under the app toolbar.
    menu = shell.get_by_role("menuitem", name="批量导入计量结果")
    try:
        menu.wait_for(state="visible", timeout=5000)
    except Exception:
        # Re-click app link if menu not visible yet.
        _click(status, link, "计量器具结果录入")
        menu.wait_for(state="visible", timeout=15_000)


def _frame_visible_text(frame, text: str) -> bool:
    """True if ``text`` is visible somewhere in the Maximo shell/frame."""
    if not text:
        return False
    try:
        loc = frame.get_by_text(text, exact=False)
        if loc.count() <= 0:
            return False
        return bool(loc.first.is_visible())
    except Exception:  # noqa: BLE001
        return False


def _batch_import_has_excel_error(shell) -> str | None:
    """Return the matched error token if EAMS shows an Excel import failure."""
    for token in ("EXCEL导入错误", "Excel导入错误", "excel导入错误"):
        if _frame_visible_text(shell, token):
            return token
    return None


def _batch_import_has_success(shell) -> bool:
    """True when EAMS shows the post-import success dialog."""
    for token in ("导入成功，请刷新查看", "导入成功", "请刷新查看"):
        if _frame_visible_text(shell, token):
            return True
    return False


def _batch_import_close_button(shell):
    """Locate the result-dialog 关闭 control (role first, then text)."""
    by_role = shell.get_by_role("button", name="关闭")
    try:
        if by_role.count() > 0:
            return by_role.first
    except Exception:  # noqa: BLE001
        pass
    by_text = shell.get_by_text("关闭", exact=True)
    try:
        if by_text.count() > 0:
            return by_text.first
    except Exception:  # noqa: BLE001
        pass
    return None


def _batch_import_dismiss_success(shell, *, status: StatusFn | None = None) -> None:
    """Click 关闭 on「导入成功，请刷新查看」and wait until that dialog is gone."""
    deadline = time.time() + 20
    last_err: Exception | None = None
    while time.time() < deadline:
        if not _batch_import_has_success(shell):
            return
        btn = _batch_import_close_button(shell)
        if btn is None:
            time.sleep(0.25)
            continue
        try:
            if not btn.is_visible() or not btn.is_enabled():
                time.sleep(0.25)
                continue
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
            continue
        try:
            _click(status, btn, "关闭", timeout=8_000)
        except AutofillCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.35)
            continue
        # Confirm the success banner/dialog actually left.
        gone_deadline = time.time() + 8
        while time.time() < gone_deadline:
            if not _batch_import_has_success(shell):
                return
            time.sleep(0.2)
        last_err = RuntimeError("已点击「关闭」，但导入成功提示仍在")
    if last_err is not None:
        raise RuntimeError(f"无法关闭导入成功提示：{last_err}") from last_err
    raise RuntimeError("无法关闭导入成功提示：未找到「关闭」")


def _batch_import_excel(
    shell,
    excel_path: Path,
    *,
    status: StatusFn | None = None,
    control: AutofillControl | None = None,
    wait_seconds: int = DEFAULT_LOGIN_WAIT_SECONDS,
) -> None:
    """Upload the batch Excel and wait until the EAMS import dialog finishes.

    On success, dismisses「导入成功，请刷新查看」via「关闭」before returning.
    Per-certificate autofill must not start until this returns. Raises
    ``AutofillBatchImportError`` if the UI shows ``EXCEL导入错误``.
    """

    def check():
        if control is not None:
            control.checkpoint(status)

    _click(status, shell.get_by_role("menuitem", name="批量导入计量结果"), "批量导入计量结果")
    check()
    upload = _upload_frame(shell)
    _status(status, f"选择文件：{excel_path.name}")
    upload.get_by_role("button", name="Choose File").set_input_files(str(excel_path))
    check()
    _click(status, shell.get_by_role("button", name="确定"), "确定")
    _status(status, "等待批量导入完成…")

    # Do not treat a pre-result「关闭」as done — wait for success or error text.
    deadline = time.time() + max(30, int(wait_seconds))

    while True:
        check()
        err = _batch_import_has_excel_error(shell)
        if err:
            raise AutofillBatchImportError(
                f"批量导入失败：检测到「{err}」，已停止自动填写"
            )

        if _batch_import_has_success(shell):
            _status(status, "导入成功，关闭提示…")
            check()
            _batch_import_dismiss_success(shell, status=status)
            _status(status, "批量导入已完成")
            return

        if time.time() >= deadline:
            raise TimeoutError(
                "批量导入超时：未出现「导入成功，请刷新查看」或错误提示"
            )

        time.sleep(0.35)


def _open_record_by_serial(
    shell, serial: str, *, status: StatusFn | None = None
) -> None:
    if not serial:
        raise AutofillItemError("缺少计量器具编号，无法定位网页记录", quarantine=True)
    # Prefer exact text match in the result list / table.
    target = shell.get_by_text(serial, exact=True)
    if target.count() == 0:
        # Fallback: contains match (some rows append units/status).
        target = shell.get_by_text(serial)
    if target.count() == 0:
        raise AutofillItemError(
            f"网页列表中未找到编号：{serial}",
            quarantine=True,
        )
    _click(status, target.first, serial)
    # Wait for a known detail field.
    shell.get_by_role("combobox", name="检验方式").wait_for(timeout=15_000)


def _read_labeled_value(shell, label: str) -> str:
    """Best-effort read of a Maximo field value by accessible name."""
    for role in ("textbox", "combobox", "searchbox"):
        try:
            loc = shell.get_by_role(role, name=label)
            if loc.count() == 0:
                continue
            target = loc.first
            for reader in (
                lambda: target.input_value(timeout=1500),
                lambda: target.get_attribute("value"),
                lambda: target.inner_text(timeout=1500),
            ):
                try:
                    raw = reader()
                    if raw is not None and str(raw).strip():
                        return str(raw).strip()
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            continue
    try:
        labeled = shell.get_by_label(label)
        if labeled.count() > 0:
            target = labeled.first
            for reader in (
                lambda: target.input_value(timeout=1500),
                lambda: target.get_attribute("value"),
                lambda: target.inner_text(timeout=1500),
            ):
                try:
                    raw = reader()
                    if raw is not None and str(raw).strip():
                        return str(raw).strip()
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        pass
    return ""


def _verify_compare_fields(
    shell, fields: CertificateFields, *, status: StatusFn | None = None
) -> None:
    """After open: webpage 比对字段 (excl. 计量器具名称) must match the certificate."""
    expected = fields.compare_fields()
    mismatches: list[str] = []
    for key, label in COMPARE_FIELD_LABELS.items():
        want = _norm_compare(expected.get(key, ""))
        if not want:
            continue
        got = _norm_compare(_read_labeled_value(shell, label))
        _status(status, f"比对「{label}」：证书={want or '—'} · 网页={got or '—'}")
        if not got:
            mismatches.append(f"{label}（网页为空）")
        elif got != want and want not in got and got not in want:
            mismatches.append(f"{label}（证书「{want}」≠ 网页「{got}」）")
    if mismatches:
        raise AutofillItemError(
            "比对字段不一致：" + "；".join(mismatches),
            quarantine=True,
        )
    _status(status, "比对字段一致")


def _needs_result_confirm(shell, *, status: StatusFn | None = None) -> bool:
    """True when the EAMS record has「需要确认结果」checked (any confirm code)."""
    candidates = []
    for name in ("需要确认结果", "需要确认"):
        try:
            candidates.append(shell.get_by_role("checkbox", name=name))
        except Exception:  # noqa: BLE001
            pass
        try:
            candidates.append(shell.get_by_label(name))
        except Exception:  # noqa: BLE001
            pass
        try:
            candidates.append(shell.get_by_text(name, exact=True))
        except Exception:  # noqa: BLE001
            pass

    for loc in candidates:
        try:
            if loc.count() == 0:
                continue
            node = loc.first
            for attr in ("aria-checked", "aria-pressed", "aria-selected"):
                try:
                    val = (node.get_attribute(attr) or "").strip().lower()
                    if val in {"true", "1", "mixed"}:
                        _status(status, "检测到「需要确认结果」已勾选")
                        return True
                except Exception:  # noqa: BLE001
                    pass
            try:
                if node.is_checked():
                    _status(status, "检测到「需要确认结果」已勾选")
                    return True
            except Exception:  # noqa: BLE001
                pass
            try:
                related = node.locator(
                    'xpath=ancestor-or-self::*[self::label or self::div][1]'
                    '//input[@type="checkbox"] | '
                    'xpath=following::input[@type="checkbox"][1]'
                )
                if related.count() > 0 and related.first.is_checked():
                    _status(status, "检测到「需要确认结果」已勾选")
                    return True
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            continue
    return False


def _fill_record_fields(
    shell,
    fields: CertificateFields,
    *,
    status: StatusFn | None = None,
    page=None,
) -> None:
    """Fill certificate type, check date, result info (and related dates/org)."""
    if fields.measurement_type:
        try:
            _select_combobox(
                shell,
                "检验方式",
                fields.measurement_type,
                status=status,
                page=page,
            )
        except Exception as exc:  # noqa: BLE001
            raise AutofillItemError(
                f"无法选择检验方式「{fields.measurement_type}」：{exc}",
                quarantine=True,
            ) from exc
    else:
        _status(status, "证书无检验方式，跳过类型选择")

    if not (fields.measurement_date or "").strip():
        raise AutofillItemError("缺少本次检测日期", quarantine=True)
    _fill_textbox(shell, "本次检测日期", fields.measurement_date, status=status)

    if fields.due_date:
        _fill_textbox(shell, "本次检测有效期至", fields.due_date, status=status)
    if fields.measurement_unit:
        _fill_textbox(shell, "检测机构", fields.measurement_unit, status=status)

    result = (fields.result_info or "").strip() or "合格"
    if "\n" not in result and len(result) <= 10:
        try:
            _click(
                status,
                shell.get_by_label("检验结果").get_by_role("img", name="下拉映像"),
                "检验结果",
                timeout=3000,
            )
            if not _click_dropdown_option(
                shell, result, timeout=3000, status=status, page=page
            ):
                _click(
                    status,
                    shell.get_by_role("menuitem", name=result, exact=True),
                    result,
                    timeout=3000,
                )
        except Exception:
            try:
                _select_combobox(
                    shell, "检验结果", result, status=status, page=page
                )
            except Exception:
                pass

    _fill_textbox(shell, "计量结果信息", result, status=status)


def _upload_certificate_pdf(
    shell, pdf_path: str | Path, *, status: StatusFn | None = None, page=None
) -> None:
    path = Path(pdf_path)
    if not path.is_file():
        raise AutofillItemError(f"PDF 不存在：{path}", quarantine=True)
    _click(status, shell.get_by_role("button", name="上传附件"), "上传附件")
    upload = _upload_frame(shell)
    _status(status, f"选择附件：{path.name}")
    upload.get_by_role("button", name="Choose File").set_input_files(str(path))
    try:
        _select_combobox(shell, "类型", "证书", status=status, page=page)
    except Exception as exc:  # noqa: BLE001
        try:
            current = _read_labeled_value(shell, "类型")
            if _norm_compare(current) != "证书":
                raise AutofillItemError(
                    f"无法选择附件类型「证书」：{exc}",
                    quarantine=True,
                ) from exc
        except AutofillItemError:
            raise
        except Exception:
            pass
    _click(status, shell.get_by_role("button", name="确定"), "确定")
    try:
        shell.get_by_role("button", name="上传附件").wait_for(state="visible", timeout=15_000)
    except Exception:
        pass
    _status(status, f"已上传证书附件：{path.name}")


def _submit_workflow(shell, *, status: StatusFn | None = None) -> None:
    _click(status, shell.get_by_alt_text("发送工作流"), "发送工作流")
    _click(status, shell.get_by_role("button", name="确定"), "确定")

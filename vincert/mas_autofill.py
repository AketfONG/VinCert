"""EAMS / Maximo browser automation via Playwright (persistent login session)."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import CertificateFields

EAMS_HOME = (
    "https://eams.manage.masuat.mtr.bj.cn/maximo/oslc/graphite/"
    "manage-shell/index.html#/main"
)
EAMS_LOGIN_URL = "https://auth.masuat.mtr.bj.cn/login/"
# Keep alias for older call sites.
MAS_HOME = EAMS_HOME
SHELL_IFRAME = "#manage-shell_Iframe"
UPLOAD_IFRAME = "#upload_iframe"

# Default profile lives next to the project so login cookies persist across runs.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_USER_DATA_DIR = PROJECT_ROOT / "mas_browser_data"
CREDENTIALS_PATH = PROJECT_ROOT / "eams_credentials.json"
EXPORTS_DIR = PROJECT_ROOT / "exports"
FAILED_ITEMS_DIR = PROJECT_ROOT / "failed_items"

StatusFn = Callable[[str], None]


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


class AutofillControl:
    """Cross-thread pause / resume / exit signals for ``run_mas_autofill``."""

    def __init__(self) -> None:
        self._pause = threading.Event()
        self._cancel = threading.Event()
        self._context = None
        self._context_lock = threading.Lock()

    def bind_context(self, context) -> None:
        with self._context_lock:
            self._context = context

    def clear_context(self) -> None:
        with self._context_lock:
            self._context = None

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def is_paused(self) -> bool:
        return self._pause.is_set()

    def request_exit(self) -> None:
        """Signal cancel and close the browser to unblock any Playwright wait."""
        self._cancel.set()
        self._pause.clear()  # unblock any pause wait
        with self._context_lock:
            ctx = self._context
            self._context = None
        if ctx is not None:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass

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


def _select_combobox(
    frame,
    name: str,
    value: str,
    *,
    timeout: float = 8000,
    status: StatusFn | None = None,
) -> None:
    if not value:
        return
    _click(status, frame.get_by_role("combobox", name=name), name, timeout=timeout)
    # Maximo lookup menus surface as menuitem / option / plain text.
    for getter in (
        lambda: frame.get_by_role("menuitem", name=value, exact=True),
        lambda: frame.get_by_role("option", name=value, exact=True),
        lambda: frame.get_by_text(value, exact=True),
    ):
        loc = getter()
        try:
            if loc.count() > 0:
                _click(status, loc.first, value, timeout=timeout)
                return
        except Exception:  # noqa: BLE001
            continue
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


def _fill_eams_login_form(
    page, username: str, password: str, *, status: StatusFn | None = None
) -> None:
    """Fill username/password on the MAS auth portal and submit."""
    user_filled = False
    for name in ("用户名", "账号", "Username", "User name", "user"):
        try:
            page.get_by_role("textbox", name=name).fill(username, timeout=1500)
            user_filled = True
            break
        except Exception:  # noqa: BLE001
            continue
    if not user_filled:
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
                    loc.first.fill(username, timeout=1500)
                    user_filled = True
                    break
            except Exception:  # noqa: BLE001
                continue
    if not user_filled:
        raise RuntimeError("未找到用户名输入框")

    pass_filled = False
    for name in ("密码", "Password"):
        try:
            page.get_by_label(name).fill(password, timeout=1500)
            pass_filled = True
            break
        except Exception:  # noqa: BLE001
            continue
    if not pass_filled:
        loc = page.locator('input[type="password"]')
        if loc.count() == 0:
            raise RuntimeError("未找到密码输入框")
        loc.first.fill(password, timeout=1500)

    clicked = False
    for name in ("登录", "登 录", "Sign in", "Login", "提交"):
        try:
            _click(status, page.get_by_role("button", name=name), name, timeout=1500)
            clicked = True
            break
        except Exception:  # noqa: BLE001
            continue
    if not clicked:
        submit = page.locator('button[type="submit"], input[type="submit"]')
        if submit.count() == 0:
            raise RuntimeError("未找到登录按钮")
        _click(status, submit.first, "提交登录", timeout=3000)


def login_eams(
    page,
    username: str,
    password: str,
    *,
    login_wait_seconds: int = 120,
    status: StatusFn | None = None,
) -> None:
    """Open EAMS and autofill credentials when a login form is shown."""
    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        raise ValueError("请先在设置中填写 EAMS 用户名和密码")

    _status(status, "打开 EAMS…")
    page.goto(EAMS_HOME, wait_until="domcontentloaded")
    if _already_logged_in(page, timeout_ms=5000):
        _status(status, "已检测到登录会话")
        return

    # Prefer dedicated auth portal; fall back to whatever redirect landed on.
    try:
        page.goto(EAMS_LOGIN_URL, wait_until="domcontentloaded")
    except Exception:  # noqa: BLE001
        pass

    if _already_logged_in(page, timeout_ms=2000):
        _status(status, "已检测到登录会话")
        return

    _status(status, "正在自动填写登录信息…")
    _fill_eams_login_form(page, username, password, status=status)

    deadline_ms = max(login_wait_seconds, 30) * 1000
    _status(status, "等待登录完成…")
    try:
        page.wait_for_url("**/eams.manage.mas.mtr.bj.cn/**", timeout=deadline_ms)
    except Exception:  # noqa: BLE001
        pass
    page.goto(EAMS_HOME, wait_until="domcontentloaded")
    page.wait_for_selector(SHELL_IFRAME, timeout=deadline_ms)
    _status(status, "EAMS 登录成功")


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
    slow_mo: int = 200,
    batch_import: bool = True,
    fill_details: bool = True,
    upload_pdf: bool = True,
    submit_workflow: bool = False,
    login_wait_seconds: int = 300,
    status: StatusFn | None = None,
    control: AutofillControl | None = None,
) -> AutofillReport:
    """
    Drive EAMS 计量器具结果录入 using a persistent Chromium profile.

    If username/password are provided, the login form is autofilled when needed.
    Pass ``control`` to support pause / continue / exit from the UI.
    """
    if not items and not (batch_import and excel_rows):
        raise ValueError("没有可自动填写的条目")

    sync_playwright = ensure_playwright()
    report = AutofillReport()
    profile = Path(user_data_dir or DEFAULT_USER_DATA_DIR)
    profile.mkdir(parents=True, exist_ok=True)
    gate = control or AutofillControl()

    def check():
        gate.checkpoint(status)

    with sync_playwright() as p:
        context = None
        try:
            check()
            _status(status, "正在启动浏览器…")
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=headless,
                slow_mo=slow_mo,
                viewport={"width": 1400, "height": 900},
                accept_downloads=True,
            )
            gate.bind_context(context)
            page = context.pages[0] if context.pages else context.new_page()
            check()
            if username and password:
                login_eams(
                    page,
                    username,
                    password,
                    login_wait_seconds=login_wait_seconds,
                    status=status,
                )
            else:
                _status(status, "打开 EAMS 主页（如需登录请在浏览器中完成）…")
                page.goto(EAMS_HOME, wait_until="domcontentloaded")
            check()
            shell = _wait_for_shell(page, login_wait_seconds=login_wait_seconds, status=status)

            check()
            _status(status, "进入「计量器具结果录入」…")
            _open_measure_app(shell, status=status)

            if batch_import and excel_path:
                check()
                excel_path = Path(excel_path)
                if not excel_path.exists():
                    if excel_rows is None or excel_headers is None:
                        raise FileNotFoundError(f"Excel 不存在且未提供数据行：{excel_path}")
                    write_batch_excel(excel_rows, excel_headers, excel_path)
                _status(status, f"批量导入 Excel：{excel_path.name}")
                _batch_import_excel(shell, excel_path, status=status)
                report.imported_excel = True

            if fill_details or upload_pdf:
                # PDF upload is compulsory for each approved certificate.
                upload_pdf = True
                for i, item in enumerate(items, start=1):
                    check()
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
                        _open_record_by_serial(shell, serial, status=status)
                        check()
                        _verify_compare_fields(shell, item.fields, status=status)
                        check()
                        if _needs_result_confirm(shell, status=status):
                            raise AutofillItemError(
                                "网页勾选了「需要确认结果」，已移出自动填写队列",
                                quarantine=True,
                            )
                        if fill_details:
                            check()
                            _fill_record_fields(shell, item.fields, status=status)
                            report.filled += 1
                        check()
                        _upload_certificate_pdf(shell, pdf, status=status)
                        report.uploaded += 1
                        if submit_workflow:
                            check()
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
        except Exception as exc:  # noqa: BLE001
            if gate.cancelled():
                report.cancelled = True
                _status(status, "已退出自动填写")
            else:
                raise
        finally:
            gate.clear_context()
            _status(status, "自动化结束，正在关闭浏览器会话…")
            if context is not None:
                try:
                    context.close()
                except Exception:  # noqa: BLE001
                    pass

    return report


def open_eams_login_session(
    username: str,
    password: str,
    *,
    user_data_dir: Path | None = None,
    login_wait_seconds: int = 180,
    status: StatusFn | None = None,
) -> None:
    """Launch browser, autofill EAMS login, then close after session is established."""
    sync_playwright = ensure_playwright()
    profile = Path(user_data_dir or DEFAULT_USER_DATA_DIR)
    profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            slow_mo=100,
            viewport={"width": 1400, "height": 900},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            login_eams(
                page,
                username,
                password,
                login_wait_seconds=login_wait_seconds,
                status=status,
            )
            _wait_for_shell(page, login_wait_seconds=login_wait_seconds, status=status)
            _status(status, "EAMS 登录完成，会话已保存")
        finally:
            context.close()


def _wait_for_shell(page, *, login_wait_seconds: int, status: StatusFn | None):
    """Wait until the Maximo shell iframe is available (after optional login)."""
    deadline_ms = max(login_wait_seconds, 30) * 1000
    try:
        page.wait_for_selector(SHELL_IFRAME, timeout=min(20_000, deadline_ms))
    except Exception:
        _status(status, "未检测到已登录会话，请在浏览器中登录…")
        page.wait_for_selector(SHELL_IFRAME, timeout=deadline_ms)

    shell = _shell(page)
    # Prefer the measure-entry link; fall back to any shell content.
    try:
        shell.get_by_role("link", name="计量器具结果录入", exact=True).wait_for(
            timeout=min(30_000, deadline_ms)
        )
    except Exception:
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


def _batch_import_excel(
    shell, excel_path: Path, *, status: StatusFn | None = None
) -> None:
    _click(status, shell.get_by_role("menuitem", name="批量导入计量结果"), "批量导入计量结果")
    upload = _upload_frame(shell)
    _status(status, f"选择文件：{excel_path.name}")
    upload.get_by_role("button", name="Choose File").set_input_files(str(excel_path))
    _click(status, shell.get_by_role("button", name="确定"), "确定")
    # Import dialog may show progress then need 关闭.
    try:
        _click(status, shell.get_by_role("button", name="关闭"), "关闭", timeout=30_000)
    except Exception:
        # Some builds only show 确定 again; ignore if already closed.
        pass


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
    shell, fields: CertificateFields, *, status: StatusFn | None = None
) -> None:
    """Fill certificate type, check date, result info (and related dates/org)."""
    if fields.measurement_type:
        try:
            _select_combobox(
                shell, "检验方式", fields.measurement_type, status=status
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
            _click(
                status,
                shell.get_by_role("menuitem", name=result, exact=True),
                result,
                timeout=3000,
            )
        except Exception:
            try:
                _select_combobox(shell, "检验结果", result, status=status)
            except Exception:
                pass

    _fill_textbox(shell, "计量结果信息", result, status=status)


def _upload_certificate_pdf(
    shell, pdf_path: str | Path, *, status: StatusFn | None = None
) -> None:
    path = Path(pdf_path)
    if not path.is_file():
        raise AutofillItemError(f"PDF 不存在：{path}", quarantine=True)
    _click(status, shell.get_by_role("button", name="上传附件"), "上传附件")
    upload = _upload_frame(shell)
    _status(status, f"选择附件：{path.name}")
    upload.get_by_role("button", name="Choose File").set_input_files(str(path))
    try:
        _select_combobox(shell, "类型", "证书", status=status)
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

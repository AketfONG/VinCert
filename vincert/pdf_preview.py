"""Shared Playwright Chromium: PDF preview tab + EAMS tab in one window."""

from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

StatusFn = Callable[[str], None]
ClosedFn = Callable[[], None]

T = TypeVar("T")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Fallback profile when caller does not pass an EAMS profile dir.
PDF_PREVIEW_USER_DATA_DIR = PROJECT_ROOT / "pdf_preview_browser_data"


def ensure_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # noqa: BLE001
        raise RuntimeError(
            "未安装 Playwright。请执行：\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        ) from exc
    return sync_playwright


def path_to_file_url(path: str | Path, *, hide_sidebar: bool = True) -> str:
    """file:// URL; hide Chrome PDF thumbnail/nav sidebar by default."""
    url = Path(path).expanduser().resolve().as_uri()
    if hide_sidebar:
        # PDF Open Parameters — Chrome's viewer honors navpanes / pagemode.
        url = f"{url}#navpanes=0&pagemode=none&toolbar=1"
    return url


class PdfPreviewController:
    """One Chromium window: PDF tab and optional EAMS tab stay independent.

    - ``show`` / ``close`` affect only the PDF tab.
    - ``run_on_browser`` runs work on the EAMS tab (same window) without
      closing the PDF tab.
    - Closing the PDF tab does not tear down EAMS; closing the last tab /
      window notifies ``on_closed`` so the app can fullscreen.
    """

    def __init__(
        self,
        *,
        on_closed: ClosedFn | None = None,
        status: StatusFn | None = None,
    ):
        self._on_closed = on_closed
        self._status = status
        self._cmds: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._alive = False
        self._current_path: str | None = None
        self._browser_open = False
        self._has_eams = False
        self._browser_pid: int | None = None
        self._profile_dir: str | None = None
        self._lock = threading.Lock()

    @property
    def current_path(self) -> str | None:
        with self._lock:
            return self._current_path

    @property
    def is_open(self) -> bool:
        """True while the shared Chromium window is alive."""
        with self._lock:
            return self._browser_open

    @property
    def has_pdf(self) -> bool:
        with self._lock:
            return self._current_path is not None

    @property
    def has_eams(self) -> bool:
        with self._lock:
            return self._has_eams

    def show(
        self,
        pdf_path: str,
        bounds: tuple[int, int, int, int],
        *,
        profile_dir: str | Path | None = None,
    ) -> None:
        path = str(Path(pdf_path).expanduser().resolve())
        if not Path(path).is_file():
            self._emit(f"PDF 不存在：{path}")
            return
        self._ensure_thread()
        profile = str(Path(profile_dir).resolve()) if profile_dir else None
        self._cmds.put(("show_pdf", path, bounds, profile))

    def focus(self) -> None:
        """Bring the Chromium window / PDF tab to the front."""
        if not self.is_open:
            return
        self._ensure_thread()
        self._cmds.put(("focus_pdf", None, None, None))

    def close(self) -> None:
        """Close only the PDF tab. EAMS tab / window stay if still open."""
        if not self._alive and self._thread is None:
            with self._lock:
                self._current_path = None
            return
        self._cmds.put(("close_pdf", None, None, None))

    def run_on_browser(
        self,
        fn: Callable[[Any, Any], T],
        *,
        bounds: tuple[int, int, int, int] | None = None,
        profile_dir: str | Path | None = None,
        accept_downloads: bool = True,
        slow_mo: int = 0,
    ) -> T:
        """Run ``fn(context, eams_page)`` on the Playwright thread.

        Reuses the shared window; opens/focuses an EAMS tab without closing PDF.
        """
        self._ensure_thread()
        done = threading.Event()
        box: dict[str, Any] = {"result": None, "error": None}
        profile = str(Path(profile_dir).resolve()) if profile_dir else None
        self._cmds.put(
            (
                "run",
                fn,
                done,
                box,
                {
                    "bounds": bounds,
                    "profile_dir": profile,
                    "accept_downloads": accept_downloads,
                    "slow_mo": int(slow_mo or 0),
                },
            )
        )
        while not done.wait(timeout=0.5):
            thread = self._thread
            if thread is None or not thread.is_alive():
                break
        if box["error"] is not None:
            raise box["error"]
        return box["result"]

    def shutdown(self) -> None:
        self._cmds.put(("shutdown", None, None, None))
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=8.0)
        self._thread = None
        self._alive = False
        with self._lock:
            self._current_path = None
            self._browser_open = False
            self._has_eams = False
            self._browser_pid = None
            self._profile_dir = None

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._alive = True
        self._thread = threading.Thread(
            target=self._run,
            name="vincert-shared-browser",
            daemon=True,
        )
        self._thread.start()

    def _emit(self, message: str) -> None:
        if self._status is not None:
            try:
                self._status(message)
            except Exception:  # noqa: BLE001
                pass

    def _set_state(
        self,
        *,
        browser_open: bool | None = None,
        pdf_path: str | None | object = ...,
        has_eams: bool | None = None,
        browser_pid: int | None | object = ...,
        profile_dir: str | None | object = ...,
    ) -> None:
        with self._lock:
            if browser_open is not None:
                self._browser_open = browser_open
            if pdf_path is not ...:
                self._current_path = pdf_path  # type: ignore[assignment]
            if has_eams is not None:
                self._has_eams = has_eams
            if browser_pid is not ...:
                self._browser_pid = browser_pid  # type: ignore[assignment]
            if profile_dir is not ...:
                self._profile_dir = profile_dir  # type: ignore[assignment]

    def _notify_browser_closed(self) -> None:
        self._set_state(
            browser_open=False,
            pdf_path=None,
            has_eams=False,
            browser_pid=None,
            profile_dir=None,
        )
        if self._on_closed is not None:
            try:
                self._on_closed()
            except Exception:  # noqa: BLE001
                pass

    def _browser_pid_locked(self) -> int | None:
        with self._lock:
            return self._browser_pid

    def _capture_context_pid(self, context) -> None:
        pid = None
        try:
            browser = getattr(context, "browser", None)
            proc = getattr(browser, "process", None) if browser is not None else None
            if proc is not None:
                pid = int(proc.pid)
        except Exception:  # noqa: BLE001
            pid = None
        self._set_state(browser_pid=pid)

    def _set_window_bounds(self, page, bounds: tuple[int, int, int, int]) -> None:
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

    def _snap_browser_right(self, *, title_hint: str | None = None) -> None:
        if sys.platform != "win32":
            return
        try:
            from .win_snap import snap_chrome

            snap_chrome(
                side="right",
                title_hint=title_hint,
                pid=self._browser_pid_locked(),
            )
        except Exception:  # noqa: BLE001
            pass

    def _bring_browser_to_front(self, *, title_hint: str | None = None) -> None:
        if sys.platform != "win32":
            return
        try:
            from .win_snap import bring_chrome_to_front

            bring_chrome_to_front(
                title_hint=title_hint,
                pid=self._browser_pid_locked(),
            )
        except Exception:  # noqa: BLE001
            pass

    def _launch_context(
        self,
        p,
        bounds: tuple[int, int, int, int] | None,
        *,
        profile_dir: Path,
        accept_downloads: bool,
        slow_mo: int,
    ):
        profile_dir.mkdir(parents=True, exist_ok=True)
        launch_args = [
            "--disable-infobars",
            "--new-window",
            "--disable-features=Translate,TranslateUI,OptimizationHints",
            "--disable-translate",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if bounds is not None:
            launch_args.extend(
                [
                    f"--window-position={int(bounds[0])},{int(bounds[1])}",
                    f"--window-size={max(400, int(bounds[2]))},{max(400, int(bounds[3]))}",
                ]
            )
        kwargs: dict[str, Any] = {
            "user_data_dir": str(profile_dir),
            "headless": False,
            "no_viewport": True,
            "accept_downloads": accept_downloads,
            "args": launch_args,
            "ignore_default_args": ["--enable-automation"],
        }
        if slow_mo > 0:
            kwargs["slow_mo"] = slow_mo
        if accept_downloads:
            pw_dl = PROJECT_ROOT / "browser_downloads" / "playwright_tmp"
            pw_dl.mkdir(parents=True, exist_ok=True)
            kwargs["downloads_path"] = str(pw_dl)
        context = p.chromium.launch_persistent_context(**kwargs)
        self._capture_context_pid(context)
        page = context.pages[0] if context.pages else context.new_page()
        return context, page

    def _apply_bounds(
        self,
        page,
        bounds: tuple[int, int, int, int] | None,
        *,
        title_hint: str | None = None,
        force: bool = False,
    ) -> None:
        if bounds is None:
            return
        try:
            # CDP bounds only — avoid Win+Right snap (animates / flickers).
            self._set_window_bounds(page, bounds)
        except Exception:  # noqa: BLE001
            if force:
                raise

    def _page_alive(self, page) -> bool:
        if page is None:
            return False
        try:
            _ = page.url
            return not page.is_closed()
        except Exception:  # noqa: BLE001
            return False

    def _context_alive(self, context) -> bool:
        if context is None:
            return False
        try:
            _ = context.pages
            return True
        except Exception:  # noqa: BLE001
            return False

    def _close_context(self, context) -> None:
        if context is None:
            return
        try:
            context.close()
        except Exception:  # noqa: BLE001
            pass

    def _run(self) -> None:
        try:
            sync_playwright = ensure_playwright()
        except RuntimeError as exc:
            self._emit(str(exc))
            self._alive = False
            self._notify_browser_closed()
            return

        context = None
        pdf_page = None
        eams_page = None
        profile_dir: Path | None = None

        def drop_browser() -> None:
            nonlocal context, pdf_page, eams_page, profile_dir
            self._close_context(context)
            context = None
            pdf_page = None
            eams_page = None
            profile_dir = None
            self._notify_browser_closed()

        def ensure_context(
            p,
            *,
            bounds: tuple[int, int, int, int] | None,
            wanted_profile: Path,
            accept_downloads: bool,
            slow_mo: int,
            title_hint: str | None = None,
        ):
            nonlocal context, pdf_page, eams_page, profile_dir
            if context is not None and not self._context_alive(context):
                context = None
                pdf_page = None
                eams_page = None
                profile_dir = None
                self._set_state(
                    browser_open=False,
                    pdf_path=None,
                    has_eams=False,
                    browser_pid=None,
                    profile_dir=None,
                )

            if context is not None and profile_dir is not None:
                if profile_dir.resolve() != wanted_profile.resolve():
                    # Switching EAMS env / profile requires a fresh Chromium.
                    self._emit("环境配置变更，正在重启浏览器…")
                    keep_pdf = None
                    with self._lock:
                        keep_pdf = self._current_path
                    self._close_context(context)
                    context = None
                    pdf_page = None
                    eams_page = None
                    profile_dir = None
                    # Re-open below; PDF path restored by caller if needed.
                    if keep_pdf:
                        self._set_state(pdf_path=keep_pdf)

            freshly = False
            if context is None:
                self._emit("正在打开浏览器…")
                context, first = self._launch_context(
                    p,
                    bounds,
                    profile_dir=wanted_profile,
                    accept_downloads=accept_downloads,
                    slow_mo=slow_mo,
                )
                profile_dir = wanted_profile
                pdf_page = None
                eams_page = None
                self._set_state(
                    browser_open=True,
                    has_eams=False,
                    profile_dir=str(wanted_profile),
                )
                freshly = True
                self._apply_bounds(first, bounds, title_hint=title_hint, force=False)
                return context, first, freshly

            # Context exists — optionally re-snap.
            anchor = None
            for cand in (eams_page, pdf_page):
                if self._page_alive(cand):
                    anchor = cand
                    break
            if anchor is None:
                anchor = context.pages[0] if context.pages else context.new_page()
            if bounds is not None:
                self._apply_bounds(anchor, bounds, title_hint=title_hint)
            return context, anchor, freshly

        try:
            with sync_playwright() as p:
                while self._alive:
                    try:
                        item = self._cmds.get(timeout=0.25)
                    except queue.Empty:
                        if context is not None and not self._context_alive(context):
                            drop_browser()
                        else:
                            # Drop stale page refs if user closed a tab manually.
                            if pdf_page is not None and not self._page_alive(pdf_page):
                                pdf_page = None
                                self._set_state(pdf_path=None)
                            if eams_page is not None and not self._page_alive(eams_page):
                                eams_page = None
                                self._set_state(has_eams=False)
                            if (
                                context is not None
                                and pdf_page is None
                                and eams_page is None
                                and not context.pages
                            ):
                                drop_browser()
                        continue

                    cmd = item[0] if item else None

                    if cmd == "shutdown":
                        break

                    if cmd == "close_pdf":
                        if self._page_alive(pdf_page):
                            try:
                                pdf_page.close()
                            except Exception:  # noqa: BLE001
                                pass
                        pdf_page = None
                        self._set_state(pdf_path=None)
                        if eams_page is not None and self._page_alive(eams_page):
                            # EAMS remains — keep window; do not fullscreen.
                            continue
                        # No EAMS tab — close the shared window.
                        drop_browser()
                        continue

                    if cmd == "focus_pdf":
                        hint = None
                        with self._lock:
                            if self._current_path:
                                hint = Path(self._current_path).name
                        if self._page_alive(pdf_page):
                            try:
                                pdf_page.bring_to_front()
                            except Exception:  # noqa: BLE001
                                pass
                        self._bring_browser_to_front(title_hint=hint)
                        continue

                    if cmd == "show_pdf":
                        _cmd, path, bounds, profile = item
                        try:
                            wanted = (
                                Path(profile)
                                if profile
                                else PDF_PREVIEW_USER_DATA_DIR
                            )
                            title_hint = Path(path).name
                            context, anchor, freshly = ensure_context(
                                p,
                                bounds=bounds,
                                wanted_profile=wanted,
                                accept_downloads=True,
                                slow_mo=0,
                                title_hint=title_hint,
                            )
                            if not self._page_alive(pdf_page):
                                # Prefer blank first page when we just launched
                                # and EAMS is not using it yet.
                                if (
                                    freshly
                                    and not self._page_alive(eams_page)
                                    and self._page_alive(anchor)
                                ):
                                    pdf_page = anchor
                                else:
                                    pdf_page = context.new_page()
                                    if freshly or bounds is not None:
                                        self._apply_bounds(
                                            pdf_page,
                                            bounds,
                                            title_hint=title_hint,
                                        )
                            assert pdf_page is not None
                            url = path_to_file_url(path, hide_sidebar=True)
                            current = (pdf_page.url or "").split("#", 1)[0]
                            target = url.split("#", 1)[0]
                            if current != target:
                                pdf_page.goto(url, wait_until="domcontentloaded")
                            try:
                                pdf_page.bring_to_front()
                            except Exception:  # noqa: BLE001
                                pass
                            self._bring_browser_to_front(title_hint=title_hint)
                            self._set_state(pdf_path=path, browser_open=True)
                        except Exception as exc:  # noqa: BLE001
                            self._emit(f"PDF 预览失败：{exc}")
                            if context is not None and not self._page_alive(eams_page):
                                drop_browser()
                            else:
                                pdf_page = None
                                self._set_state(pdf_path=None)
                        continue

                    if cmd == "run":
                        _cmd, fn, done, box, opts = item
                        try:
                            wanted = (
                                Path(opts["profile_dir"])
                                if opts.get("profile_dir")
                                else PDF_PREVIEW_USER_DATA_DIR
                            )
                            bounds = opts.get("bounds")
                            context, anchor, freshly = ensure_context(
                                p,
                                bounds=bounds,
                                wanted_profile=wanted,
                                accept_downloads=bool(
                                    opts.get("accept_downloads", True)
                                ),
                                slow_mo=int(opts.get("slow_mo") or 0),
                                title_hint="EAMS",
                            )
                            if not self._page_alive(eams_page):
                                if (
                                    freshly
                                    and not self._page_alive(pdf_page)
                                    and self._page_alive(anchor)
                                ):
                                    eams_page = anchor
                                elif (
                                    not freshly
                                    and not self._page_alive(pdf_page)
                                    and self._page_alive(anchor)
                                    and len(context.pages) == 1
                                ):
                                    eams_page = anchor
                                else:
                                    eams_page = context.new_page()
                            assert eams_page is not None
                            if bounds is not None:
                                self._apply_bounds(
                                    eams_page, bounds, title_hint="EAMS"
                                )
                            try:
                                eams_page.bring_to_front()
                            except Exception:  # noqa: BLE001
                                pass
                            self._set_state(has_eams=True, browser_open=True)
                            box["result"] = fn(context, eams_page)
                            # Keep EAMS tab; verify it still exists.
                            if not self._page_alive(eams_page):
                                eams_page = None
                                self._set_state(has_eams=False)
                        except Exception as exc:  # noqa: BLE001
                            box["error"] = exc
                            if eams_page is not None and not self._page_alive(
                                eams_page
                            ):
                                eams_page = None
                                self._set_state(has_eams=False)
                        finally:
                            done.set()
                        continue
        finally:
            self._close_context(context)
            self._alive = False
            self._notify_browser_closed()
            while True:
                try:
                    item = self._cmds.get_nowait()
                    if item and item[0] == "run" and len(item) >= 4:
                        done = item[2]
                        box = item[3]
                        box["error"] = RuntimeError("浏览器已关闭")
                        done.set()
                except queue.Empty:
                    break
            time.sleep(0.05)

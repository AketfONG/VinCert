"""Live PDF preview in a Playwright Chromium window (side-by-side with the app)."""

from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path
from typing import Callable

StatusFn = Callable[[str], None]
ClosedFn = Callable[[], None]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Persistent profile so cookies / dismissals survive reopen (not cleared on close).
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
    """Background Playwright browser that shows a local PDF and follows window bounds."""

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
        self._browser_pid: int | None = None
        self._lock = threading.Lock()

    @property
    def current_path(self) -> str | None:
        with self._lock:
            return self._current_path

    @property
    def is_open(self) -> bool:
        return self.current_path is not None

    def show(self, pdf_path: str, bounds: tuple[int, int, int, int]) -> None:
        path = str(Path(pdf_path).expanduser().resolve())
        if not Path(path).is_file():
            self._emit(f"PDF 不存在：{path}")
            return
        self._ensure_thread()
        self._cmds.put(("show", path, bounds))

    def focus(self) -> None:
        """Bring the preview Chromium window to the front."""
        if not self.is_open:
            return
        self._ensure_thread()
        self._cmds.put(("focus", None, None))

    def close(self) -> None:
        if not self._alive and self._thread is None:
            with self._lock:
                self._current_path = None
                self._browser_pid = None
            return
        self._cmds.put(("close", None, None))

    def shutdown(self) -> None:
        self._cmds.put(("shutdown", None, None))
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None
        self._alive = False
        with self._lock:
            self._current_path = None
            self._browser_pid = None

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._alive = True
        self._thread = threading.Thread(
            target=self._run,
            name="vincert-pdf-preview",
            daemon=True,
        )
        self._thread.start()

    def _emit(self, message: str) -> None:
        if self._status is not None:
            try:
                self._status(message)
            except Exception:  # noqa: BLE001
                pass

    def _notify_closed(self) -> None:
        with self._lock:
            self._current_path = None
            self._browser_pid = None
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
        with self._lock:
            self._browser_pid = pid

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

    def _snap_preview_right(self, *, title_hint: str | None = None) -> None:
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

    def _bring_preview_to_front(self, *, title_hint: str | None = None) -> None:
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

    def _launch_context(self, p, bounds: tuple[int, int, int, int]):
        """Open Chromium with a persistent profile (prefs/cookies kept across runs)."""
        PDF_PREVIEW_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        launch_args = [
            "--disable-infobars",
            "--new-window",
            # Cut down translate / first-run style prompts.
            "--disable-features=Translate,TranslateUI,OptimizationHints",
            "--disable-translate",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-position={int(bounds[0])},{int(bounds[1])}",
            f"--window-size={max(400, int(bounds[2]))},{max(400, int(bounds[3]))}",
        ]
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PDF_PREVIEW_USER_DATA_DIR),
            headless=False,
            no_viewport=True,
            accept_downloads=False,
            args=launch_args,
            ignore_default_args=["--enable-automation"],
        )
        self._capture_context_pid(context)
        page = context.pages[0] if context.pages else context.new_page()
        return context, page

    def _run(self) -> None:
        try:
            sync_playwright = ensure_playwright()
        except RuntimeError as exc:
            self._emit(str(exc))
            self._alive = False
            self._notify_closed()
            return

        context = None
        page = None
        try:
            with sync_playwright() as p:
                while self._alive:
                    try:
                        cmd, path, bounds = self._cmds.get(timeout=0.25)
                    except queue.Empty:
                        if context is not None:
                            try:
                                # Persistent context has no .is_connected; probe pages.
                                _ = context.pages
                            except Exception:  # noqa: BLE001
                                context = None
                                page = None
                                self._notify_closed()
                        continue

                    if cmd == "shutdown":
                        break

                    if cmd == "close":
                        if context is not None:
                            try:
                                context.close()
                            except Exception:  # noqa: BLE001
                                pass
                        context = None
                        page = None
                        self._notify_closed()
                        continue

                    if cmd == "focus":
                        hint = None
                        with self._lock:
                            if self._current_path:
                                hint = Path(self._current_path).name
                        if page is not None:
                            try:
                                page.bring_to_front()
                            except Exception:  # noqa: BLE001
                                pass
                        self._bring_preview_to_front(title_hint=hint)
                        continue

                    if cmd != "show" or path is None or bounds is None:
                        continue

                    try:
                        title_hint = Path(path).name
                        freshly_launched = False
                        if context is None:
                            self._emit("正在打开 PDF 预览…")
                            context, page = self._launch_context(p, bounds)
                            freshly_launched = True
                        assert page is not None
                        if freshly_launched:
                            if sys.platform == "win32":
                                try:
                                    self._set_window_bounds(page, bounds)
                                except Exception:  # noqa: BLE001
                                    pass
                                time.sleep(0.12)
                                self._snap_preview_right(title_hint=title_hint)
                            else:
                                self._set_window_bounds(page, bounds)
                        url = path_to_file_url(path, hide_sidebar=True)
                        # Compare without hash so navpanes flag doesn't force reloads forever.
                        current = (page.url or "").split("#", 1)[0]
                        target = url.split("#", 1)[0]
                        if current != target:
                            page.goto(url, wait_until="domcontentloaded")
                        try:
                            page.bring_to_front()
                        except Exception:  # noqa: BLE001
                            pass
                        self._bring_preview_to_front(title_hint=title_hint)
                        with self._lock:
                            self._current_path = path
                    except Exception as exc:  # noqa: BLE001
                        self._emit(f"PDF 预览失败：{exc}")
                        if context is not None:
                            try:
                                context.close()
                            except Exception:  # noqa: BLE001
                                pass
                        context = None
                        page = None
                        self._notify_closed()
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:  # noqa: BLE001
                    pass
            self._alive = False
            self._notify_closed()
            while True:
                try:
                    self._cmds.get_nowait()
                except queue.Empty:
                    break
            time.sleep(0.05)

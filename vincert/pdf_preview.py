"""Live PDF preview in a Playwright Chromium window (side-by-side with the app)."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Callable

StatusFn = Callable[[str], None]
ClosedFn = Callable[[], None]


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


def path_to_file_url(path: str | Path) -> str:
    return Path(path).expanduser().resolve().as_uri()


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

    def close(self) -> None:
        if not self._alive and self._thread is None:
            with self._lock:
                self._current_path = None
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
        if self._on_closed is not None:
            try:
                self._on_closed()
            except Exception:  # noqa: BLE001
                pass

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

    def _run(self) -> None:
        try:
            sync_playwright = ensure_playwright()
        except RuntimeError as exc:
            self._emit(str(exc))
            self._alive = False
            self._notify_closed()
            return

        browser = None
        page = None
        try:
            with sync_playwright() as p:
                while self._alive:
                    try:
                        cmd, path, bounds = self._cmds.get(timeout=0.25)
                    except queue.Empty:
                        if browser is not None and not browser.is_connected():
                            browser = None
                            page = None
                            self._notify_closed()
                        continue

                    if cmd == "shutdown":
                        break

                    if cmd == "close":
                        if browser is not None:
                            try:
                                browser.close()
                            except Exception:  # noqa: BLE001
                                pass
                        browser = None
                        page = None
                        self._notify_closed()
                        continue

                    if cmd != "show" or path is None or bounds is None:
                        continue

                    try:
                        if browser is None or not browser.is_connected():
                            self._emit("正在打开 PDF 预览…")
                            browser = p.chromium.launch(
                                headless=False,
                                args=[
                                    "--disable-infobars",
                                    "--new-window",
                                    f"--window-position={int(bounds[0])},{int(bounds[1])}",
                                    f"--window-size={max(400, int(bounds[2]))},"
                                    f"{max(400, int(bounds[3]))}",
                                ],
                            )
                            page = browser.new_page(no_viewport=True)
                        assert page is not None
                        self._set_window_bounds(page, bounds)
                        url = path_to_file_url(path)
                        if page.url != url:
                            page.goto(url, wait_until="domcontentloaded")
                        else:
                            # Same file selected again — still refresh bounds.
                            self._set_window_bounds(page, bounds)
                        with self._lock:
                            self._current_path = path
                    except Exception as exc:  # noqa: BLE001
                        self._emit(f"PDF 预览失败：{exc}")
                        if browser is not None:
                            try:
                                browser.close()
                            except Exception:  # noqa: BLE001
                                pass
                        browser = None
                        page = None
                        self._notify_closed()
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
            self._alive = False
            self._notify_closed()
            # Drain leftover commands so a restart starts clean.
            while True:
                try:
                    self._cmds.get_nowait()
                except queue.Empty:
                    break
            time.sleep(0.05)

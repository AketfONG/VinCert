"""Native Windows Aero Snap and window-focus helpers."""

from __future__ import annotations

import sys
import time
from typing import Literal

SnapSide = Literal["left", "right", "maximize", "restore"]


def is_windows() -> bool:
    return sys.platform == "win32"


def snap_hwnd(hwnd: int, side: SnapSide) -> bool:
    """Snap a window with the same gestures as Win+Arrow / maximize."""
    if not is_windows() or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        SW_MAXIMIZE = 3
        VK_LWIN = 0x5B
        VK_LEFT = 0x25
        VK_RIGHT = 0x27
        KEYEVENTF_KEYUP = 0x0002

        hwnd = int(hwnd)
        user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
        bring_hwnd_to_front(hwnd)
        time.sleep(0.05)

        if side == "restore":
            return True
        if side == "maximize":
            user32.ShowWindow(wintypes.HWND(hwnd), SW_MAXIMIZE)
            return True

        vk = VK_LEFT if side == "left" else VK_RIGHT
        user32.keybd_event(VK_LWIN, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)
        user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.08)
        return True
    except Exception:  # noqa: BLE001
        return False


def bring_hwnd_to_front(hwnd: int) -> bool:
    """Raise an HWND above other windows (best-effort on modern Windows)."""
    if not is_windows() or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = int(hwnd)
        SW_RESTORE = 9
        SW_SHOW = 5

        user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
        user32.ShowWindow(wintypes.HWND(hwnd), SW_SHOW)

        foreground = int(user32.GetForegroundWindow() or 0)
        if foreground == hwnd:
            return True

        current_thread = int(kernel32.GetCurrentThreadId())
        foreground_thread = int(user32.GetWindowThreadProcessId(foreground, None) or 0)
        target_thread = int(user32.GetWindowThreadProcessId(hwnd, None) or 0)

        attached_fg = False
        attached_tg = False
        if foreground_thread and foreground_thread != current_thread:
            attached_fg = bool(
                user32.AttachThreadInput(current_thread, foreground_thread, True)
            )
        if target_thread and target_thread != current_thread:
            attached_tg = bool(
                user32.AttachThreadInput(current_thread, target_thread, True)
            )

        user32.BringWindowToTop(wintypes.HWND(hwnd))
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
        # HWND_TOPMOST then HWND_NOTOPMOST — forces Z-order without staying always-on-top.
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        user32.SetWindowPos(
            wintypes.HWND(hwnd),
            wintypes.HWND(HWND_TOPMOST),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        )
        user32.SetWindowPos(
            wintypes.HWND(hwnd),
            wintypes.HWND(HWND_NOTOPMOST),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        )

        if attached_fg:
            user32.AttachThreadInput(current_thread, foreground_thread, False)
        if attached_tg:
            user32.AttachThreadInput(current_thread, target_thread, False)
        return True
    except Exception:  # noqa: BLE001
        return False


def raise_hwnds_above_others(*hwnds: int | None) -> bool:
    """Raise several windows above normal apps without leaving them always-on-top.

    Used while VinCert + Chromium resize/split so other windows do not cover them.
    Does not keep permanent topmost — user can still Alt+Tab elsewhere afterward.
    """
    if not is_windows():
        return False
    ordered = [int(h) for h in hwnds if h]
    if not ordered:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        SW_RESTORE = 9
        SW_SHOW = 5

        for hwnd in ordered:
            user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
            user32.ShowWindow(wintypes.HWND(hwnd), SW_SHOW)
            user32.SetWindowPos(
                wintypes.HWND(hwnd),
                wintypes.HWND(HWND_TOPMOST),
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
            )

        # Clear topmost in reverse so the first window ends highest among the pair.
        for hwnd in reversed(ordered):
            user32.SetWindowPos(
                wintypes.HWND(hwnd),
                wintypes.HWND(HWND_NOTOPMOST),
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
            )
            user32.BringWindowToTop(wintypes.HWND(hwnd))

        # Focus the primary (first) window without burying the rest.
        bring_hwnd_to_front(ordered[0])
        return True
    except Exception:  # noqa: BLE001
        return False


def find_hwnds_for_pid(pid: int) -> list[int]:
    """Return visible top-level HWNDs owned by ``pid`` (largest first)."""
    if not is_windows() or not pid:
        return []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        results: list[tuple[int, int]] = []  # (area, hwnd)

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            proc_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            if int(proc_id.value) != int(pid):
                return True
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            w = int(rect.right) - int(rect.left)
            h = int(rect.bottom) - int(rect.top)
            if w < 200 or h < 200:
                return True
            results.append((w * h, int(hwnd)))
            return True

        user32.EnumWindows(_enum, 0)
        results.sort(reverse=True)
        return [hwnd for _area, hwnd in results]
    except Exception:  # noqa: BLE001
        return []


def find_chrome_hwnd(*, title_hint: str | None = None, pid: int | None = None) -> int | None:
    """Best-effort Chromium/Chrome top-level window HWND."""
    if not is_windows():
        return None
    if pid:
        hwnds = find_hwnds_for_pid(pid)
        if hwnds:
            return hwnds[0]
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        GetClassNameW = user32.GetClassNameW
        GetWindowTextW = user32.GetWindowTextW
        hint = (title_hint or "").lower()
        matches: list[tuple[int, int]] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            GetClassNameW(hwnd, buf, 256)
            cls = buf.value
            if "Chrome_WidgetWin" not in cls:
                return True
            title_buf = ctypes.create_unicode_buffer(512)
            GetWindowTextW(hwnd, title_buf, 512)
            title = title_buf.value or ""
            if not title.strip():
                return True
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            w = int(rect.right) - int(rect.left)
            h = int(rect.bottom) - int(rect.top)
            if w < 200 or h < 200:
                return True
            score = w * h
            if hint and hint in title.lower():
                score += 10_000_000
            matches.append((score, int(hwnd)))
            return True

        user32.EnumWindows(_enum, 0)
        matches.sort(reverse=True)
        return matches[0][1] if matches else None
    except Exception:  # noqa: BLE001
        return None


def snap_chrome(*, side: SnapSide, title_hint: str | None = None, pid: int | None = None) -> bool:
    hwnd = find_chrome_hwnd(title_hint=title_hint, pid=pid)
    if not hwnd:
        return False
    return snap_hwnd(hwnd, side)


def bring_chrome_to_front(*, title_hint: str | None = None, pid: int | None = None) -> bool:
    hwnd = find_chrome_hwnd(title_hint=title_hint, pid=pid)
    if not hwnd:
        return False
    return bring_hwnd_to_front(hwnd)

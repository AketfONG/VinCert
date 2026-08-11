"""
VinCert — certificate OCR / parse desktop app.
"""

from __future__ import annotations

from pathlib import Path
import json
import shutil
import sys
import threading

import customtkinter
import tkinter as tk
from tkinter import filedialog
from tkinter import font as tkfont

from vincert.models import CertificateFields, ParseResult
from vincert.pipeline import parse_certificate
from vincert.folder_import import find_pdfs_in_folder
from vincert.mas_autofill import (
    AutofillControl,
    AutofillItem,
    FAILED_ITEMS_DIR,
    close_keepalive_browser,
    load_credentials,
    next_export_path,
    run_mas_autofill,
    save_credentials,
    write_batch_excel,
)
from vincert.pdf_preview import PdfPreviewController

customtkinter.set_appearance_mode("System")
customtkinter.set_default_color_theme("blue")

PROJECT_ROOT = Path(__file__).resolve().parent
UI_SETTINGS_PATH = PROJECT_ROOT / "ui_settings.json"
UI_SCALE_NORMAL = 1.0
UI_SCALE_ZOOMED = 1.2
DEFAULT_AUTOFILL_STEP_DELAY_SEC = 1.0
AUTOFILL_EXIT_WARN_MS = 10_000


DEFAULT_FAILED_ITEMS_DIR = FAILED_ITEMS_DIR.resolve()


def load_ui_settings(path: Path | None = None) -> dict:
    """Load persisted UI prefs from ui_settings.json."""
    settings_path = Path(path or UI_SETTINGS_PATH)
    defaults = {
        "ui_zoomed": True,
        "ocr_enabled": False,
        "buttons_bold": True,
        "content_centering": True,
        "status_dots": True,
        "doc_list_scale_fonts": True,
        "failed_items_dir": str(DEFAULT_FAILED_ITEMS_DIR),
        "testing_mode": False,
        "demo_folder": "",
        "autofill_step_delay_sec": DEFAULT_AUTOFILL_STEP_DELAY_SEC,
    }
    if not settings_path.exists():
        return dict(defaults)
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(defaults)
    except Exception:  # noqa: BLE001
        return dict(defaults)
    out = dict(defaults)
    if "ui_zoomed" in data:
        out["ui_zoomed"] = bool(data["ui_zoomed"])
    if "ocr_enabled" in data:
        out["ocr_enabled"] = bool(data["ocr_enabled"])
    if "buttons_bold" in data:
        out["buttons_bold"] = bool(data["buttons_bold"])
    if "content_centering" in data:
        out["content_centering"] = bool(data["content_centering"])
    if "status_dots" in data:
        out["status_dots"] = bool(data["status_dots"])
    if "doc_list_scale_fonts" in data:
        out["doc_list_scale_fonts"] = bool(data["doc_list_scale_fonts"])
    if "failed_items_dir" in data and data["failed_items_dir"]:
        out["failed_items_dir"] = str(data["failed_items_dir"])
    if "testing_mode" in data:
        out["testing_mode"] = bool(data["testing_mode"])
    if "demo_folder" in data and data["demo_folder"]:
        out["demo_folder"] = str(data["demo_folder"])
    if "autofill_step_delay_sec" in data:
        try:
            delay = float(data["autofill_step_delay_sec"])
            out["autofill_step_delay_sec"] = max(0.0, min(30.0, delay))
        except (TypeError, ValueError):
            pass
    return out


def save_ui_settings(**updates) -> Path:
    settings_path = Path(UI_SETTINGS_PATH)
    data = load_ui_settings(settings_path)
    data.update(updates)
    settings_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return settings_path


def load_ui_zoomed(path: Path | None = None) -> bool:
    """Return True when large UI scale is enabled (default: zoomed)."""
    return bool(load_ui_settings(path).get("ui_zoomed", True))


def save_ui_zoomed(zoomed: bool, path: Path | None = None) -> Path:
    return save_ui_settings(ui_zoomed=bool(zoomed))


def load_ocr_enabled(path: Path | None = None) -> bool:
    """Return True when OCR extraction is enabled (default: off)."""
    return bool(load_ui_settings(path).get("ocr_enabled", False))


def save_ocr_enabled(enabled: bool, path: Path | None = None) -> Path:
    return save_ui_settings(ocr_enabled=bool(enabled))


def load_buttons_bold(path: Path | None = None) -> bool:
    """Return True when button labels use bold weight (default: on)."""
    return bool(load_ui_settings(path).get("buttons_bold", True))


def save_buttons_bold(bold: bool, path: Path | None = None) -> Path:
    return save_ui_settings(buttons_bold=bool(bold))


def load_content_centering(path: Path | None = None) -> bool:
    """Return True when page content is vertically centered when it fits."""
    return bool(load_ui_settings(path).get("content_centering", True))


def save_content_centering(enabled: bool, path: Path | None = None) -> Path:
    return save_ui_settings(content_centering=bool(enabled))


def load_status_dots(path: Path | None = None) -> bool:
    """Return True when approved/removed status uses red/green dots."""
    return bool(load_ui_settings(path).get("status_dots", True))


def save_status_dots(enabled: bool, path: Path | None = None) -> Path:
    return save_ui_settings(status_dots=bool(enabled))


def load_doc_list_scale_fonts(path: Path | None = None) -> bool:
    """Return True when document-list canvas text follows UI zoom scaling."""
    return bool(load_ui_settings(path).get("doc_list_scale_fonts", True))


def save_doc_list_scale_fonts(enabled: bool, path: Path | None = None) -> Path:
    return save_ui_settings(doc_list_scale_fonts=bool(enabled))


def load_failed_items_dir(path: Path | None = None) -> Path:
    """Return the configured quarantine folder for failed certificates."""
    raw = load_ui_settings(path).get("failed_items_dir") or str(DEFAULT_FAILED_ITEMS_DIR)
    try:
        return Path(str(raw)).expanduser().resolve()
    except Exception:  # noqa: BLE001
        return DEFAULT_FAILED_ITEMS_DIR


def save_failed_items_dir(folder: str | Path, path: Path | None = None) -> Path:
    resolved = Path(folder).expanduser().resolve()
    save_ui_settings(failed_items_dir=str(resolved))
    return resolved


def load_testing_mode(path: Path | None = None) -> bool:
    """Return True when testing mode auto-loads the demo folder on launch."""
    return bool(load_ui_settings(path).get("testing_mode", False))


def save_testing_mode(enabled: bool, path: Path | None = None) -> Path:
    return save_ui_settings(testing_mode=bool(enabled))


def load_demo_folder(path: Path | None = None) -> str:
    """Return the configured demo certificates folder path (may be empty)."""
    raw = load_ui_settings(path).get("demo_folder") or ""
    return str(raw).strip()


def save_demo_folder(folder: str | Path | None, path: Path | None = None) -> str:
    value = "" if folder is None else str(Path(folder).expanduser())
    if value:
        try:
            value = str(Path(value).resolve())
        except Exception:  # noqa: BLE001
            pass
    save_ui_settings(demo_folder=value)
    return value


def load_autofill_step_delay_sec(path: Path | None = None) -> float:
    """Seconds to pause between autofill steps (default 1.0)."""
    raw = load_ui_settings(path).get(
        "autofill_step_delay_sec", DEFAULT_AUTOFILL_STEP_DELAY_SEC
    )
    try:
        return max(0.0, min(30.0, float(raw)))
    except (TypeError, ValueError):
        return DEFAULT_AUTOFILL_STEP_DELAY_SEC


def save_autofill_step_delay_sec(seconds: float, path: Path | None = None) -> float:
    value = max(0.0, min(30.0, float(seconds)))
    save_ui_settings(autofill_step_delay_sec=value)
    return value


def apply_ui_scale(zoomed: bool) -> float:
    scale = UI_SCALE_ZOOMED if zoomed else UI_SCALE_NORMAL
    customtkinter.set_widget_scaling(scale)
    # Keep window scaling fixed. Changing it with zoom (esp. zoom-out in
    # fullscreen) shrinks the drawable area and leaves black letterbox bars.
    customtkinter.set_window_scaling(1.0)
    return scale


# Apply saved scale before widgets are constructed.
apply_ui_scale(load_ui_zoomed())

DOC_SIDEBAR_WIDTH = 400
DOC_SIDEBAR_MARGIN = 10
MAIN_MIN_WIDTH = 480
MAIN_MAX_WIDTH = int(DOC_SIDEBAR_WIDTH * 2.5)  # center view cap
CONTENT_WRAP = MAIN_MAX_WIDTH - 48
DOC_WRAP = DOC_SIDEBAR_WIDTH - 32
STEP_TILE_HEIGHT = 72
BRAND_TILE_HEIGHT = 48
NAV_BADGE_SIZE = 36
SETTINGS_BTN_HEIGHT = 40
SMALL_BTN_HEIGHT = 36
ENTRY_HEIGHT = 44
PRIMARY_ACTION_BTN_HEIGHT = 45  # 45×1.2 = 54px — avoids CTk odd-height text bias when zoomed
UI_RADIUS = 12  # shared corner radius for panels + buttons
BUILD_VERSION = "v0.5b"
BUILD_DATE = "06/08/2026"

# Typography — sizes chosen for readability at both 1.0× and 1.2× UI scale.
FONT_BRAND = 22
FONT_TITLE = 18
FONT_SECTION = 15
FONT_BODY = 14
FONT_LABEL = 13
FONT_ENTRY = 15
FONT_META = 13
FONT_BUTTON = 14
FONT_STEP = 15
FONT_BADGE = 18

STEPS = [
    ("extract", "批量提取", "1"),
    ("review", "核对填写", "2"),
]

_theme = customtkinter.ThemeManager.theme
APP_BG_COLOR = _theme["CTk"]["fg_color"]
_theme_frame = _theme["CTkFrame"]
_frame_fg = _theme_frame["fg_color"]
_frame_top = _theme_frame["top_fg_color"]
_dark_frame_fg = _frame_fg[1] if isinstance(_frame_fg, (tuple, list)) else _frame_fg
_dark_frame_top = _frame_top[1] if isinstance(_frame_top, (tuple, list)) else _frame_top
_theme_textbox = _theme.get("CTkTextbox", {})
_textbox_fg = _theme_textbox.get("fg_color", ("#F9F9FA", "#1D1E1E"))
if isinstance(_textbox_fg, (tuple, list)):
    _dark_field_fg = _textbox_fg[1]
else:
    _dark_field_fg = _textbox_fg
# Light mode: white surfaces for sidebar modules, doc panel, and fields.
EMBED_BG_COLOR = ("#ffffff", _dark_frame_fg)
ACTIVE_OUTLINE = ("#3b8ed0", "#1f6aa5")
TILE_BG_NORMAL = ("#ffffff", _dark_frame_fg)
TILE_BG_HOVER = ("#eef1f4", _dark_frame_top)
TILE_BG_ACTIVE = ("#e3f0fa", "#343638")  # light blue wash / softer dark fill
SECONDARY_BTN_FG = ("#c9d1d9", "#3d444b")
SECONDARY_BTN_HOVER = ("#dde4eb", "#556068")
SECONDARY_BTN_TEXT = ("gray10", "gray90")
OUTLINE_BTN_HOVER = ("gray70", "gray30")
PRIMARY_BTN_FG = ("#3B8ED0", "#1F6AA5")
PRIMARY_BTN_HOVER = ("#5BA3DB", "#2E83C4")
PRIMARY_BTN_TEXT = ("#DCE4EE", "#DCE4EE")
SUCCESS_BTN_FG = ("#2d8a4e", "#2d8a4e")
SUCCESS_BTN_HOVER = ("#38a460", "#38a460")
SUCCESS_BTN_TEXT = ("#ffffff", "#ffffff")
DANGER_BTN_FG = ("#c0392b", "#c0392b")
DANGER_BTN_HOVER = ("#e74c3c", "#e74c3c")
DANGER_BTN_TEXT = ("#ffffff", "#ffffff")
TOAST_BG = ("#ffffff", "#1a1a1a")
TOAST_TITLE_COLOR = ("gray10", "#ffffff")
TOAST_MESSAGE_COLOR = ("gray30", "#f0f0f0")
TOAST_PROGRESS_TRACK = ("#e5e5e5", "#2a2a2a")
TOAST_WIDTH = 360  # compact fixed toast; do not stretch to ops column
TOAST_PAD = 12
TOAST_BTN_HEIGHT = 40
TOAST_BORDER_WIDTH = 2  # match active tile / settings outline
TOAST_RADIUS = UI_RADIUS + 2  # 2px rounder than shared UI radius
TOAST_MIN_MS = 5000
TOAST_DEFAULT_MS = 5000
TOAST_SUCCESS_MS = 5000
TOAST_TICK_MS = 50
TOAST_STACK_MAX = 8
TOAST_STACK_GAP = 8
AUTOFILL_LOG_WIDTH = TOAST_WIDTH
AUTOFILL_LOG_PAD = TOAST_PAD
AUTOFILL_LOG_FINISH_MS = 12000
DOC_ROW_ACTIVE = ("#3b8ed0", "#1f6aa5")
DOC_ROW_ACTIVE_TEXT = ("#ffffff", "#ffffff")
# Index numbers at ~50% opacity (emoji marks stay full strength).
DOC_MARK_NUMBER_COLOR = ("gray50", "gray50")
DOC_MARK_NUMBER_ACTIVE = ("#9dc6e7", "#9fb5d2")  # white blended ~50% onto selected blue
DOC_STATUS_DOT = "●"
DOC_STATUS_DOT_OK = ("#2d8a4e", "#38a460")
DOC_STATUS_DOT_BAD = ("#c0392b", "#e74c3c")
DOC_STATUS_DOT_SIZE = FONT_BODY + 6
DOC_ROW_HEIGHT = 36
DOC_MARK_COL_WIDTH = 32
DOC_NAME_TIP_PADX = 8
DOC_NAME_TIP_PADY = 4
RESULT_INFO_HEIGHT = 128  # ~4 taller entry rows
FIELD_FG_COLOR = ("#ffffff", _dark_field_fg)
FIELD_TEXT_COLOR = _theme_textbox.get("text_color", ("gray10", "#DCE4EE"))
FIELD_FG_COLOR_DISABLED = ("gray90", "gray22")
FIELD_TEXT_COLOR_DISABLED = ("gray55", "gray55")
# Autofill UI lock: mute chrome that `state=disabled` alone does not cover.
UI_LOCK_BTN_FG = ("#d5dbe0", "#3a3a3a")
UI_LOCK_BTN_TEXT = ("#8b9298", "#777777")
UI_LOCK_TILE_FG = ("#eceff1", "#2c2c2c")
UI_LOCK_BADGE_FG = ("#b0b6bc", "#555555")
UI_LOCK_LABEL = ("gray55", "gray55")
UI_LOCK_DOC_ROW = ("#dfe3e7", "#2f2f2f")
UI_LOCK_DOC_TEXT = ("gray55", "gray55")
# Match fill so CTkEntry's 1px border stays invisible (avoids a hairline under fields).
FIELD_BORDER_WIDTH = 1

MATCH_FIELDS = [
    ("name", "计量器具名称"),
    ("serial_num", "计量器具编号"),
    ("manufacturer", "制造厂"),
]

METROLOGY_FIELDS = [
    ("measurement_type", "检验方式"),
    ("measurement_date", "本次检测日期"),
    ("measurement_unit", "检测机构"),
]

# Review-only user fields (not OCR-extracted).
REVIEW_USER_FIELDS = [
    ("result_info", "计量结果信息"),
]

REVIEW_FILL_FIELDS = METROLOGY_FIELDS + REVIEW_USER_FIELDS
EXTRACT_DISPLAY_FIELDS = MATCH_FIELDS + METROLOGY_FIELDS
REVIEW_DISPLAY_FIELDS = MATCH_FIELDS + REVIEW_FILL_FIELDS
DEFAULT_RESULT_INFO = "合格"
EXPORT_COLUMNS = [
    ("name", "名称"),
    ("serial_num", "编号"),
    ("model", "型号"),
    ("measurement_unit", "计量单位"),
    ("measurement_date", "计量日期"),
    ("measurement_type", "计量类型"),
]


WINDOW_MIN_HEIGHT_NORMAL = 830  # design units × 1.0 scale
WINDOW_MIN_HEIGHT_ZOOMED = 820  # design units × 1.2 scale — tune separately from normal
WINDOW_MIN_WIDTH = DOC_SIDEBAR_WIDTH + MAIN_MIN_WIDTH


class App(customtkinter.CTk):
    def __init__(self):
        super().__init__()

        self.title("VinCert")
        _boot_min_h = (
            WINDOW_MIN_HEIGHT_ZOOMED
            if load_ui_zoomed()
            else WINDOW_MIN_HEIGHT_NORMAL
        )
        self.geometry(
            f"{WINDOW_MIN_WIDTH + 40}x{max(900, _boot_min_h + 40)}"
        )

        self._imported_files: list[str] = []
        self._parse_results: dict[str, ParseResult] = {}
        self._autofill_queue: list[str] = []
        self._removed_paths: set[str] = set()
        self._current_cert_index = 0
        self._current_step = "extract"
        # Workflow gate: review stays locked until failed certs are cleared;
        # extract locks after advancing to review. Tiles are not clickable.
        self._workflow_phase = "extract"  # "extract" | "review"
        self._step_before_settings = "extract"

        self._source_folder: str | None = None
        self._selected_path: str | None = None
        self._doc_rows: dict[str, dict] = {}
        self._doc_hover_path: str | None = None
        self._doc_name_tip: customtkinter.CTkFrame | None = None
        self._doc_name_tip_path: str | None = None
        self._extract_busy = False
        self._autofill_busy = False
        self._autofill_control: AutofillControl | None = None
        self._autofill_exit_confirming = False
        self._autofill_was_paused_before_exit = False
        self._autofill_disabled_widgets: list = []
        self._autofill_ui_chrome_locked = False
        self._toast_host: customtkinter.CTkFrame | None = None
        self._toasts: list[dict] = []
        self._toast_seq = 0
        self._toast_tick_after_id: str | None = None
        self._autofill_log_frame: customtkinter.CTkFrame | None = None
        self._autofill_log_text: customtkinter.CTkTextbox | None = None
        self._autofill_log_status: customtkinter.CTkLabel | None = None
        self._autofill_log_finish_after_id: str | None = None
        self._pending_quarantine_paths: list[str] = []
        self._ui_zoomed = load_ui_zoomed()
        self._ocr_enabled = load_ocr_enabled()
        self._buttons_bold = load_buttons_bold()
        self._content_centering = load_content_centering()
        self._status_dots = load_status_dots()
        self._doc_list_scale_fonts = load_doc_list_scale_fonts()
        self._failed_items_dir = load_failed_items_dir()
        self._testing_mode = load_testing_mode()
        self._demo_folder = load_demo_folder()
        self._autofill_step_delay_sec = load_autofill_step_delay_sec()
        self._content_wrap_labels: list[customtkinter.CTkLabel] = []
        self._pdf_preview_layout_active = False
        # Ignore the next preview-closed callback (intentional close before re-snap).
        self._pdf_preview_suppress_restore = False
        self._pdf_preview = PdfPreviewController(
            on_closed=lambda: self.after(0, self._on_pdf_preview_closed),
            status=lambda msg: self.after(0, lambda m=msg: self.set_status(m)),
        )

        self.grid_rowconfigure(0, weight=1)
        self._apply_layout_column_minsizes()

        self._build_doc_sidebar()
        self._build_controls_panel()
        self._apply_min_window_size()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.show_step("extract")
        self._update_extract_ocr_ui()
        self.after_idle(self._bootstrap_window_layout)

    def _widget_scaling_factor(self) -> float:
        if hasattr(self, "controls_inner"):
            return max(
                customtkinter.ScalingTracker.get_widget_scaling(self.controls_inner),
                0.01,
            )
        return max(customtkinter.ScalingTracker.widget_scaling, 0.01)

    def _apply_layout_column_minsizes(self):
        # Tk grid minsize is in pixels; CTk widget widths are design units × scale.
        scale = self._widget_scaling_factor()
        self.grid_columnconfigure(
            0, weight=0, minsize=int(DOC_SIDEBAR_WIDTH * scale)
        )
        self.grid_columnconfigure(
            1, weight=1, minsize=int(MAIN_MIN_WIDTH * scale)
        )

    def _bootstrap_window_layout(self):
        """Fullscreen on launch; demo autoload may then snap for a PDF preview."""
        self._apply_window_fullscreen()
        self._maybe_autoload_demo_folder()

    def _screen_work_area(self) -> tuple[int, int, int, int]:
        """Return (x, y, width, height) for the usable desktop area.

        On Windows this uses the monitor work rect (excludes the taskbar /
        bottom navigation bar). Falls back to full screen metrics elsewhere.
        """
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                class RECT(ctypes.Structure):
                    _fields_ = [
                        ("left", wintypes.LONG),
                        ("top", wintypes.LONG),
                        ("right", wintypes.LONG),
                        ("bottom", wintypes.LONG),
                    ]

                class MONITORINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.DWORD),
                        ("rcMonitor", RECT),
                        ("rcWork", RECT),
                        ("dwFlags", wintypes.DWORD),
                    ]

                user32 = ctypes.windll.user32
                # Prefer the monitor that contains this window (multi-monitor safe).
                try:
                    hwnd = self._win_toplevel_hwnd() or int(self.winfo_id())
                    MONITOR_DEFAULTTONEAREST = 2
                    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
                    info = MONITORINFO()
                    info.cbSize = ctypes.sizeof(MONITORINFO)
                    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                        work = info.rcWork
                        return (
                            int(work.left),
                            int(work.top),
                            int(work.right - work.left),
                            int(work.bottom - work.top),
                        )
                except Exception:  # noqa: BLE001
                    pass

                rect = RECT()
                SPI_GETWORKAREA = 0x0030
                if user32.SystemParametersInfoW(
                    SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
                ):
                    return (
                        int(rect.left),
                        int(rect.top),
                        int(rect.right - rect.left),
                        int(rect.bottom - rect.top),
                    )
            except Exception:  # noqa: BLE001
                pass

        self.update_idletasks()
        # macOS / fallback: full screen size (Dock may still overlap slightly).
        return 0, 0, int(self.winfo_screenwidth()), int(self.winfo_screenheight())

    def _win_toplevel_hwnd(self) -> int | None:
        """Return the Win32 HWND for this Tk toplevel (outer frame), if available."""
        if sys.platform != "win32":
            return None
        try:
            import ctypes

            user32 = ctypes.windll.user32
            GA_ROOT = 2
            # Prefer the OS root window; winfo_id() alone is often an inner child.
            try:
                child = int(self.winfo_id())
                root = int(user32.GetAncestor(child, GA_ROOT) or 0)
                if root:
                    return root
            except Exception:  # noqa: BLE001
                pass
            # wm_frame() may be "0x...." — use base 0 so the prefix parses.
            try:
                frame = str(self.wm_frame()).strip()
                if frame:
                    return int(frame, 0)
            except Exception:  # noqa: BLE001
                pass
            return int(self.winfo_id())
        except Exception:  # noqa: BLE001
            return None

    def _set_app_bounds(self, left: int, top: int, width: int, height: int) -> None:
        """Place VinCert exactly in a screen rect (respects Windows work area).

        On Windows, Win32 SetWindowPos sizes the *outer* frame to the work area.
        Tk ``geometry`` sizes the *client* area, which would push the title bar /
        borders into the taskbar — never use that for fullscreen/snap on win32.
        """
        left, top = int(left), int(top)
        width, height = max(400, int(width)), max(400, int(height))
        try:
            if sys.platform == "darwin":
                try:
                    self.attributes("-fullscreen", False)
                except Exception:  # noqa: BLE001
                    pass
            # Never use state('zoomed') — it ignores the taskbar on some DPI setups.
            self.state("normal")
        except Exception:  # noqa: BLE001
            pass
        self.update_idletasks()

        if sys.platform == "win32":
            hwnd = self._win_toplevel_hwnd()
            if hwnd:
                try:
                    import ctypes
                    from ctypes import wintypes

                    user32 = ctypes.windll.user32
                    # SWP_NOZORDER | SWP_SHOWWINDOW — exact outer-frame placement.
                    SWP_NOZORDER = 0x0004
                    SWP_SHOWWINDOW = 0x0040
                    user32.SetWindowPos(
                        wintypes.HWND(hwnd),
                        wintypes.HWND(0),
                        left,
                        top,
                        width,
                        height,
                        SWP_NOZORDER | SWP_SHOWWINDOW,
                    )
                    self.update_idletasks()

                    # Clamp if DPI / DWM still pushed us past the work rect.
                    class RECT(ctypes.Structure):
                        _fields_ = [
                            ("left", wintypes.LONG),
                            ("top", wintypes.LONG),
                            ("right", wintypes.LONG),
                            ("bottom", wintypes.LONG),
                        ]

                    rect = RECT()
                    if user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                        work_x, work_y, work_w, work_h = self._screen_work_area()
                        work_right = work_x + work_w
                        work_bottom = work_y + work_h
                        over_x = max(0, int(rect.right) - work_right)
                        over_y = max(0, int(rect.bottom) - work_bottom)
                        if over_x or over_y or int(rect.left) < work_x or int(rect.top) < work_y:
                            user32.SetWindowPos(
                                wintypes.HWND(hwnd),
                                wintypes.HWND(0),
                                work_x if int(rect.left) < work_x else left,
                                work_y if int(rect.top) < work_y else top,
                                max(400, width - over_x),
                                max(400, height - over_y),
                                SWP_NOZORDER | SWP_SHOWWINDOW,
                            )
                            self.update_idletasks()
                    return
                except Exception:  # noqa: BLE001
                    pass

        # Non-Windows (or Win32 API unavailable): Tk geometry is best-effort.
        self.geometry(f"{width}x{height}+{left}+{top}")
        self.update_idletasks()

    def _apply_window_fullscreen(self):
        """Fill the monitor — native maximize on Windows, work-area geometry elsewhere."""
        self._pdf_preview_layout_active = False
        if sys.platform == "win32":
            hwnd = self._win_toplevel_hwnd()
            if hwnd:
                try:
                    from vincert.win_snap import snap_hwnd

                    if snap_hwnd(hwnd, "maximize"):
                        self.update_idletasks()
                        return
                except Exception:  # noqa: BLE001
                    pass
            try:
                self.state("zoomed")
                self.update_idletasks()
                return
            except Exception:  # noqa: BLE001
                pass
        x, y, w, h = self._screen_work_area()
        self._set_app_bounds(x, y, w, h)
        self.after(50, lambda: self._reassert_geometry_bounds(full=True))
        self.after(200, lambda: self._reassert_geometry_bounds(full=True))

    def _reassert_geometry_bounds(self, *, full: bool) -> None:
        """Re-apply calculated geometry on non-native platforms only."""
        if sys.platform == "win32":
            return
        try:
            if not self.winfo_exists():
                return
        except Exception:  # noqa: BLE001
            return
        if full and self._pdf_preview_layout_active:
            return
        if not full and not self._pdf_preview_layout_active:
            return
        x, y, w, h = self._screen_work_area()
        if full:
            self._set_app_bounds(x, y, w, h)
        else:
            half = max(640, w // 2)
            self._set_app_bounds(x, y, half, h)

    def _snap_app_left_half(self):
        """Snap VinCert to the left half (native Win+Left on Windows)."""
        self._pdf_preview_layout_active = True
        if sys.platform == "win32":
            hwnd = self._win_toplevel_hwnd()
            if hwnd:
                try:
                    from vincert.win_snap import snap_hwnd

                    if snap_hwnd(hwnd, "left"):
                        self.update_idletasks()
                        return
                except Exception:  # noqa: BLE001
                    pass
        x, y, w, h = self._screen_work_area()
        half = max(640, w // 2)
        self._set_app_bounds(x, y, half, h)
        self.after(50, lambda: self._reassert_geometry_bounds(full=False))
        self.after(200, lambda: self._reassert_geometry_bounds(full=False))

    def _prepare_side_browser_layout(self) -> tuple[int, int, int, int]:
        """Snap VinCert left and return remaining bounds for a browser window."""
        # Autofill / preview share one side-by-side layout.
        if self._pdf_preview.is_open:
            # Closing triggers on_closed asynchronously — don't expand to fullscreen.
            self._pdf_preview_suppress_restore = True
            self._pdf_preview.close()
        self._snap_app_left_half()
        self.update_idletasks()
        self.update()
        return self._pdf_preview_bounds_remaining()

    def _pdf_preview_bounds_remaining(self) -> tuple[int, int, int, int]:
        """Size the browser to the unused work-area space beside VinCert.

        Uses the live gui.py window geometry so the preview gets whatever
        remains (typically to the right), not a hard-coded half split.
        """
        self.update_idletasks()
        work_x, work_y, work_w, work_h = self._screen_work_area()
        work_right = work_x + work_w
        work_bottom = work_y + work_h

        app_left = int(self.winfo_rootx())
        app_top = int(self.winfo_rooty())
        app_width = max(int(self.winfo_width()), 1)
        app_height = max(int(self.winfo_height()), 1)
        app_right = app_left + app_width
        app_bottom = app_top + app_height

        # Prefer the strip to the right of the app within the work area.
        right_width = work_right - app_right
        if right_width >= 400:
            left = max(app_right, work_x)
            top = work_y
            width = work_right - left
            height = work_h
            return (left, top, max(400, width), max(400, height))

        # Prefer the strip to the left of the app.
        left_width = app_left - work_x
        if left_width >= 400:
            return (work_x, work_y, max(400, left_width), max(400, work_h))

        # Prefer space below the app (unusual, but better than overlapping).
        below = work_bottom - app_bottom
        if below >= 300:
            return (
                work_x,
                max(app_bottom, work_y),
                max(400, work_w),
                max(300, below),
            )

        # Last resort: right half of the work area.
        half = max(400, work_w // 2)
        return (work_x + work_w - half, work_y, half, max(400, work_h))

    def _sync_pdf_preview(self):
        """Open/update PDF preview when a file is selected; otherwise fullscreen."""
        path = self._selected_path
        if path and Path(path).is_file():
            if self._autofill_busy:
                return
            if self._pdf_preview.is_open:
                # Keep the same Chromium window — only navigate to the new file:// URL.
                if not self._pdf_preview_layout_active:
                    self._snap_app_left_half()
                self.update_idletasks()
                bounds = self._pdf_preview_bounds_remaining()
                self._pdf_preview.show(path, bounds)
                self._pdf_preview.focus()
                return
            bounds = self._prepare_side_browser_layout()
            self._pdf_preview.show(path, bounds)
            self._pdf_preview.focus()
            return
        if self._pdf_preview.is_open:
            self._pdf_preview.close()
        # No side browser — fill the work area immediately (don't wait on close callback).
        self._apply_window_fullscreen()

    def _on_pdf_preview_closed(self):
        """Restore fullscreen when the Chromium preview window is closed."""
        try:
            if not self.winfo_exists():
                return
        except Exception:  # noqa: BLE001
            return
        if self._pdf_preview_suppress_restore:
            self._pdf_preview_suppress_restore = False
            return
        if self._autofill_busy:
            return
        if self._pdf_preview.is_open:
            return
        # User closed the preview (or it died) — expand VinCert to the work area.
        self._apply_window_fullscreen()

    def _track_content_wrap(self, label: customtkinter.CTkLabel) -> customtkinter.CTkLabel:
        self._content_wrap_labels.append(label)
        return label

    def _update_content_wraplengths(self, wrap: int):
        for label in self._content_wrap_labels:
            try:
                label.configure(wraplength=wrap)
            except Exception:  # noqa: BLE001
                pass

    def _button_font(self, size: int = FONT_BUTTON) -> customtkinter.CTkFont:
        """Shared button typeface; weight follows the 按钮加粗 setting."""
        return customtkinter.CTkFont(
            size=size,
            weight="bold" if self._buttons_bold else "normal",
        )

    def _fix_ctk_button_text_vcenter(self, button: customtkinter.CTkButton) -> None:
        """Re-balance CTkButton label grid so bold text stays centered under UI scale."""
        label = getattr(button, "_text_label", None)
        if label is None:
            return
        try:
            label.configure(anchor="center")
            base = max(int(button._border_width) + 1, int(getattr(button, "_border_spacing", 2) or 2))
            # Zoomed bold glyphs sit slightly low; give the bottom row +1 design-unit.
            extra = 1 if self._ui_zoomed and self._buttons_bold else 0
            top = button._apply_widget_scaling(base)
            bottom = button._apply_widget_scaling(base + extra)
            button.grid_rowconfigure(0, weight=1000, minsize=top)
            button.grid_rowconfigure(4, weight=1000, minsize=bottom)
            button.grid_rowconfigure((1, 3), weight=1)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _fix_ctk_entry_bottom_line(entry: customtkinter.CTkEntry) -> None:
        """CTkEntry pads the inner widget with (bw, bw+1); that extra bottom px shows as a line."""

        def _create_grid():
            entry._canvas.grid(column=0, row=0, sticky="nswe")
            bw = entry._apply_widget_scaling(entry._border_width)
            if entry._corner_radius >= entry._minimum_x_padding:
                padx = min(
                    entry._apply_widget_scaling(entry._corner_radius),
                    round(entry._apply_widget_scaling(entry._current_height / 2)),
                )
            else:
                padx = entry._apply_widget_scaling(entry._minimum_x_padding)
            entry._entry.grid(
                column=0, row=0, sticky="nswe", padx=padx, pady=(bw, bw)
            )

        entry._create_grid = _create_grid
        entry._create_grid()

    def _make_field_entry(
        self,
        parent,
        *,
        placeholder: str = "",
        height: int = ENTRY_HEIGHT,
        show: str | None = None,
    ) -> customtkinter.CTkEntry:
        kwargs = {}
        if show is not None:
            kwargs["show"] = show
        entry = customtkinter.CTkEntry(
            parent,
            height=height,
            corner_radius=UI_RADIUS,
            placeholder_text=placeholder,
            border_width=FIELD_BORDER_WIDTH,
            border_color=FIELD_FG_COLOR,
            fg_color=FIELD_FG_COLOR,
            text_color=FIELD_TEXT_COLOR,
            font=customtkinter.CTkFont(size=FONT_ENTRY),
            **kwargs,
        )
        self._fix_ctk_entry_bottom_line(entry)
        return entry

    def _style_primary_action_button(self, button: customtkinter.CTkButton) -> customtkinter.CTkButton:
        self._fix_ctk_button_text_vcenter(button)
        return button

    def _restyle_primary_action_buttons(self):
        for name in (
            "ocr_extract_button",
            "autofill_button",
            "autofill_pause_button",
            "autofill_exit_button",
        ):
            btn = getattr(self, name, None)
            if btn is not None:
                self._fix_ctk_button_text_vcenter(btn)

    # ------------------------------------------------------------------ layout
    def _create_step_tile(
        self,
        parent: customtkinter.CTkFrame,
        key: str,
        label: str,
        number: str,
    ) -> customtkinter.CTkFrame:
        tile = customtkinter.CTkFrame(
            parent,
            height=STEP_TILE_HEIGHT,
            corner_radius=UI_RADIUS,
            fg_color=TILE_BG_NORMAL,
            border_width=0,
            border_color=ACTIVE_OUTLINE,
        )
        tile.grid_propagate(False)
        tile.grid_columnconfigure(1, weight=1)
        tile.grid_rowconfigure(0, weight=1)

        badge = customtkinter.CTkFrame(
            tile,
            width=NAV_BADGE_SIZE,
            height=NAV_BADGE_SIZE,
            corner_radius=NAV_BADGE_SIZE // 2,
            fg_color=ACTIVE_OUTLINE,
        )
        badge.grid(row=0, column=0, padx=(12, 8), pady=12, sticky="w")
        badge.grid_propagate(False)
        customtkinter.CTkLabel(
            badge,
            text=number,
            font=customtkinter.CTkFont(
                family="SF Pro Display",
                size=FONT_BADGE,
                weight="bold",
            ),
            text_color=("#ffffff", "#ffffff"),
        ).place(relx=0.5, rely=0.5, anchor="center")

        customtkinter.CTkLabel(
            tile,
            text=label,
            anchor="w",
            font=customtkinter.CTkFont(size=FONT_STEP, weight="bold"),
        ).grid(row=0, column=1, padx=(0, 12), sticky="ew")

        self._bind_step_tile_hover(tile, key)
        # Step modules are progress indicators only — not manually selectable.
        tile.configure(cursor="")
        return tile

    def _bind_step_tile_hover(self, widget, key: str):
        widget.bind("<Enter>", lambda _e, k=key: self._hover_step_tile(k, True))
        widget.bind("<Leave>", lambda e, k=key: self._on_tile_leave(e, k))
        for child in widget.winfo_children():
            self._bind_step_tile_hover(child, key)

    def _on_tile_leave(self, event, key: str):
        tile = self.step_tiles[key]
        widget_under = tile.winfo_containing(event.x_root, event.y_root)
        if widget_under is None or not self._is_descendant(widget_under, tile):
            self._hover_step_tile(key, False)

    def _is_descendant(self, widget, ancestor) -> bool:
        while widget:
            if widget == ancestor:
                return True
            widget = widget.master
        return False

    def _hover_step_tile(self, key: str, entering: bool):
        if self._autofill_busy:
            return
        if key == self._current_step:
            return
        if self._step_tile_is_locked(key):
            return
        # Steps are not clickable — no hover affordance.
        return

    def _step_tile_is_locked(self, key: str) -> bool:
        """True when a workflow tile should appear greyed / inactive."""
        phase = getattr(self, "_workflow_phase", "extract")
        if key == "review" and phase != "review":
            return True
        if key == "extract" and phase == "review":
            return True
        return False

    def _apply_tile_style(self, key: str, style: str):
        if self._autofill_busy or self._autofill_ui_chrome_locked:
            return
        tile = self.step_tiles[key]
        if style == "locked" or (style != "active" and self._step_tile_is_locked(key)):
            tile.configure(
                fg_color=UI_LOCK_TILE_FG,
                border_color=UI_LOCK_TILE_FG,
                border_width=0,
            )
            for child in tile.winfo_children():
                try:
                    if isinstance(child, customtkinter.CTkFrame):
                        child.configure(fg_color=UI_LOCK_BADGE_FG)
                    elif isinstance(child, customtkinter.CTkLabel):
                        child.configure(text_color=UI_LOCK_LABEL)
                except Exception:  # noqa: BLE001
                    pass
            return
        styles = {
            "normal": {
                "tile_fg": TILE_BG_NORMAL,
                "tile_border": TILE_BG_NORMAL,
                "border_width": 0,
            },
            "hover": {
                "tile_fg": TILE_BG_HOVER,
                "tile_border": TILE_BG_HOVER,
                "border_width": 0,
            },
            "active": {
                "tile_fg": TILE_BG_ACTIVE,
                "tile_border": ACTIVE_OUTLINE,
                "border_width": 2,
            },
        }
        colors = styles[style]
        tile.configure(
            fg_color=colors["tile_fg"],
            border_color=colors["tile_border"],
            border_width=colors["border_width"],
        )
        if style == "active":
            for child in tile.winfo_children():
                try:
                    if isinstance(child, customtkinter.CTkFrame):
                        child.configure(fg_color=ACTIVE_OUTLINE)
                        for sub in child.winfo_children():
                            if isinstance(sub, customtkinter.CTkLabel):
                                sub.configure(text_color=("#ffffff", "#ffffff"))
                    elif isinstance(child, customtkinter.CTkLabel):
                        child.configure(text_color=SECONDARY_BTN_TEXT)
                except Exception:  # noqa: BLE001
                    pass

    def _update_step_tiles(self, active_key: str):
        for key in self.step_tiles:
            if self._step_tile_is_locked(key):
                self._apply_tile_style(key, "locked")
            elif key == active_key:
                self._apply_tile_style(key, "active")
            else:
                self._apply_tile_style(key, "normal")

    def _reset_workflow_to_extract(self):
        """Back to extract phase (review locked) — used on clear / new folder."""
        self._workflow_phase = "extract"
        if self._current_step not in ("extract", "settings"):
            self.show_step("extract")
        else:
            self._update_step_tiles(self._current_step)

    def _advance_to_review(self):
        """After failed certs are cleared — unlock review and lock extract."""
        if not self._imported_files:
            self.set_status("没有可核对的证书")
            self._reset_workflow_to_extract()
            return
        self._workflow_phase = "review"
        self._save_extract_fields_to_result()
        self._load_approve_fields_for_current()
        self.show_step("review")
        self.set_status("已进入核对填写")

    def _update_settings_button(self, active: bool):
        if not hasattr(self, "settings_button"):
            return
        if self._autofill_busy or self._autofill_ui_chrome_locked:
            return
        if active:
            self.settings_button.configure(
                border_width=2,
                border_color=ACTIVE_OUTLINE,
                fg_color=TILE_BG_ACTIVE,
                hover_color=TILE_BG_ACTIVE,
                text_color=SECONDARY_BTN_TEXT,
            )
        else:
            self.settings_button.configure(
                border_width=0,
                border_color=SECONDARY_BTN_FG,
                fg_color=SECONDARY_BTN_FG,
                hover_color=SECONDARY_BTN_HOVER,
                text_color=SECONDARY_BTN_TEXT,
            )

    # ------------------------------------------------------------- doc sidebar
    def _build_doc_sidebar(self):
        self.doc_sidebar = customtkinter.CTkFrame(
            self,
            width=DOC_SIDEBAR_WIDTH,
            corner_radius=0,
            fg_color=APP_BG_COLOR,
        )
        self.doc_sidebar.grid(row=0, column=0, sticky="nsew")
        self.doc_sidebar.grid_propagate(False)
        self.doc_sidebar.grid_columnconfigure(0, weight=1)
        self.doc_sidebar.grid_rowconfigure(1, weight=1)

        # Nav strip: brand + step buttons
        nav = customtkinter.CTkFrame(self.doc_sidebar, fg_color="transparent")
        nav.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=DOC_SIDEBAR_MARGIN,
            pady=(DOC_SIDEBAR_MARGIN, 0),
        )
        nav.grid_columnconfigure(0, weight=1)

        brand_tile = customtkinter.CTkFrame(
            nav,
            height=BRAND_TILE_HEIGHT,
            corner_radius=UI_RADIUS,
            fg_color="transparent",
        )
        brand_tile.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        brand_tile.grid_propagate(False)
        brand_tile.grid_columnconfigure(0, weight=1)
        brand_tile.grid_rowconfigure(0, weight=1)

        brand_inner = customtkinter.CTkFrame(brand_tile, fg_color="transparent")
        brand_inner.grid(row=0, column=0, sticky="ew", padx=4, pady=0)
        brand_inner.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            brand_inner,
            text="VinCert",
            font=customtkinter.CTkFont(size=FONT_BRAND, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        customtkinter.CTkLabel(
            brand_inner,
            text=f"{BUILD_VERSION} · {BUILD_DATE}",
            font=customtkinter.CTkFont(size=FONT_META),
            text_color="gray60",
            anchor="e",
        ).grid(row=0, column=1, sticky="e")

        steps_row = customtkinter.CTkFrame(nav, fg_color="transparent")
        steps_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        steps_row.grid_columnconfigure((0, 1), weight=1, uniform="steps")

        self.step_tiles: dict[str, customtkinter.CTkFrame] = {}
        for col, (key, label, number) in enumerate(STEPS):
            tile = self._create_step_tile(steps_row, key, label, number)
            tile.grid(
                row=0,
                column=col,
                sticky="ew",
                padx=(0 if col == 0 else 4, 0 if col == len(STEPS) - 1 else 4),
            )
            self.step_tiles[key] = tile

        self.doc_panel = customtkinter.CTkFrame(
            self.doc_sidebar,
            corner_radius=UI_RADIUS,
            fg_color=EMBED_BG_COLOR,
        )
        self.doc_panel.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=DOC_SIDEBAR_MARGIN,
            pady=(0, 8),
        )
        self.doc_panel.grid_columnconfigure(0, weight=1)
        self.doc_panel.grid_rowconfigure(3, weight=1)

        title_row = customtkinter.CTkFrame(self.doc_panel, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        title_row.grid_columnconfigure(1, weight=1)

        customtkinter.CTkLabel(
            title_row,
            text="文档列表",
            font=customtkinter.CTkFont(size=FONT_TITLE, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.folder_label = customtkinter.CTkLabel(
            title_row,
            text="未选择文件夹",
            font=customtkinter.CTkFont(size=FONT_META),
            text_color="gray60",
            anchor="e",
            wraplength=DOC_WRAP // 2,
            justify="right",
        )
        self.folder_label.grid(row=0, column=1, sticky="e")

        btn_row = customtkinter.CTkFrame(self.doc_panel, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        btn_row.grid_columnconfigure((0, 1), weight=1)

        customtkinter.CTkButton(
            btn_row,
            text="选择文件夹…",
            height=SMALL_BTN_HEIGHT,
            corner_radius=UI_RADIUS,
            fg_color=PRIMARY_BTN_FG,
            hover_color=PRIMARY_BTN_HOVER,
            text_color=PRIMARY_BTN_TEXT,
            font=self._button_font(FONT_BUTTON),
            command=self._pick_folder,
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        customtkinter.CTkButton(
            btn_row,
            text="清空",
            height=SMALL_BTN_HEIGHT,
            corner_radius=UI_RADIUS,
            fg_color=(SECONDARY_BTN_HOVER[0], SECONDARY_BTN_FG[1]),
            hover_color=(SECONDARY_BTN_FG[0], SECONDARY_BTN_HOVER[1]),
            text_color=SECONDARY_BTN_TEXT,
            font=self._button_font(FONT_BUTTON),
            command=self._clear_extract,
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self.doc_list_frame = customtkinter.CTkScrollableFrame(
            self.doc_panel,
            fg_color="transparent",
            corner_radius=0,
        )
        self.doc_list_frame.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 14))
        self.doc_list_frame.grid_columnconfigure(0, weight=1)
        self._doc_list_scroll_after: str | None = None
        # Keep scrollregion / scrollbar in sync without fighting CTk every paint.
        self.doc_list_frame._parent_canvas.bind(
            "<Configure>", self._schedule_doc_list_scrollbar_sync, add="+"
        )
        self._bind_scrollable_mousewheel(self.doc_list_frame, self.doc_list_frame)
        self._bind_scrollable_mousewheel(
            self.doc_list_frame, self.doc_list_frame._parent_canvas
        )

        self.doc_list_empty = customtkinter.CTkLabel(
            self.doc_list_frame,
            text="选择文件夹后，\nPDF 将显示在这里",
            text_color="gray50",
            justify="center",
        )
        self.doc_list_empty.grid(row=0, column=0, pady=24)

        self.settings_button = customtkinter.CTkButton(
            self.doc_sidebar,
            text="设置",
            height=SETTINGS_BTN_HEIGHT,
            corner_radius=UI_RADIUS,
            fg_color=SECONDARY_BTN_FG,
            hover_color=SECONDARY_BTN_HOVER,
            text_color=SECONDARY_BTN_TEXT,
            font=self._button_font(FONT_BUTTON),
            border_width=0,
            command=self._on_open_settings,
        )
        self.settings_button.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=DOC_SIDEBAR_MARGIN,
            pady=(0, DOC_SIDEBAR_MARGIN),
        )

    # ------------------------------------------------------------- main panel
    def _build_controls_panel(self):
        self.controls_panel = customtkinter.CTkFrame(
            self, corner_radius=0, fg_color=APP_BG_COLOR
        )
        self.controls_panel.grid(row=0, column=1, sticky="nsew", padx=0)
        self.controls_panel.grid_rowconfigure(0, weight=1)
        # Equal side spacers center content when panel is wider than MAIN_MAX_WIDTH.
        self.controls_panel.grid_columnconfigure(0, weight=1)
        self.controls_panel.grid_columnconfigure(1, weight=0)
        self.controls_panel.grid_columnconfigure(2, weight=1)

        self._controls_inner_width = MAIN_MIN_WIDTH
        self._controls_inner_height = -1
        self.controls_inner = customtkinter.CTkFrame(
            self.controls_panel,
            width=MAIN_MIN_WIDTH,
            corner_radius=0,
            fg_color="transparent",
        )
        # Fill panel height so short pages (extract) don't float centered
        # against the sidebar; field vcenter then uses the real content band.
        self.controls_inner.grid(row=0, column=1, sticky="nsew")
        self.controls_inner.grid_propagate(False)
        self.controls_inner.grid_rowconfigure(1, weight=1)
        self.controls_inner.grid_columnconfigure(0, weight=1)
        self.bind("<Configure>", self._on_app_configure_for_controls, add="+")
        self.after_idle(self._sync_controls_inner_width)

        # Match VinCert brand row: same top inset, height, and type size.
        self.controls_header_wrap = customtkinter.CTkFrame(
            self.controls_inner,
            height=BRAND_TILE_HEIGHT,
            fg_color="transparent",
        )
        self.controls_header_wrap.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=24,
            pady=(DOC_SIDEBAR_MARGIN, 8),
        )
        self.controls_header_wrap.grid_propagate(False)
        self.controls_header_wrap.grid_columnconfigure(0, weight=1)
        self.controls_header_wrap.grid_rowconfigure(0, weight=1)

        self.controls_header = customtkinter.CTkLabel(
            self.controls_header_wrap,
            text="",
            font=customtkinter.CTkFont(size=FONT_BRAND, weight="bold"),
            anchor="w",
        )
        self.controls_header.grid(row=0, column=0, sticky="w")

        self.controls_body = customtkinter.CTkFrame(self.controls_inner, fg_color="transparent")
        self.controls_body.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 16))
        self.controls_body.grid_rowconfigure(0, weight=1)
        self.controls_body.grid_columnconfigure(0, weight=1)

        self.step_views: dict[str, customtkinter.CTkFrame] = {}
        for key, builder in [
            ("extract", self._build_extract_controls),
            ("review", self._build_review_controls),
            ("settings", self._build_settings_controls),
        ]:
            frame = customtkinter.CTkFrame(self.controls_body, fg_color="transparent")
            frame.grid(row=0, column=0, sticky="nsew")
            frame.grid_columnconfigure(0, weight=1)
            # Row weights are owned by each page builder / pinned layout.
            builder(frame)
            self.step_views[key] = frame

    def _on_app_configure_for_controls(self, event):
        if event.widget is not self:
            return
        self.after_idle(self._sync_controls_inner_width)
        self.after_idle(self._sync_active_page_vcenter)

    def _sync_controls_inner_width(self):
        if not hasattr(self, "controls_panel") or not hasattr(self, "controls_inner"):
            return
        panel_w_px = int(self.controls_panel.winfo_width())
        panel_h_px = int(self.controls_panel.winfo_height())
        if panel_w_px <= 1 or panel_h_px <= 1:
            return
        # winfo_* is scaled pixels; configure(width/height=) expects design units.
        scale = self._widget_scaling_factor()
        panel_w = panel_w_px / scale
        width = int(round(min(panel_w, float(MAIN_MAX_WIDTH))))
        height = int(round(panel_h_px / scale))
        if width < 1 or height < 1:
            return
        if (
            width == self._controls_inner_width
            and height == getattr(self, "_controls_inner_height", -1)
        ):
            return
        self._controls_inner_width = width
        self._controls_inner_height = height
        self.controls_inner.configure(width=width, height=height)
        self._update_content_wraplengths(max(120, width - 48))
        self.after_idle(self._sync_active_page_vcenter)

    def show_step(self, key: str):
        if self._autofill_busy and key != self._current_step:
            self.set_status("自动填写进行中，请先暂停或退出…")
            return
        # Workflow tiles are not manually selectable; block out-of-phase jumps.
        phase = getattr(self, "_workflow_phase", "extract")
        if key == "review" and phase != "review" and key != self._current_step:
            self.set_status("请先完成批量提取并移出失败证书")
            return
        if key == "extract" and phase == "review" and key != self._current_step:
            self.set_status("已进入核对填写，请继续审批或重新导入文件夹")
            return
        self._current_step = key

        self._update_step_tiles(key)
        self._update_settings_button(key == "settings")

        titles = {
            "extract": "批量提取",
            "review": "核对填写",
            "settings": "设置",
        }
        self.controls_header.configure(text=titles.get(key, key))

        for name, frame in self.step_views.items():
            if name == key:
                frame.tkraise()

        if key == "review":
            self._cancel_pending_quarantine()
            self._load_approve_fields_for_current()
            self._update_autofill_button()
            self._schedule_active_page_vcenter(force=True)
        elif key == "settings":
            self._schedule_active_page_vcenter(force=True)
        elif key == "extract":
            # Re-measure after raise (esp. returning from settings/zoom).
            self._schedule_active_page_vcenter(force=True)

    def _schedule_active_page_vcenter(self, *, force: bool = False):
        if force:
            self._invalidate_page_vcenters()
        self.after_idle(self._sync_active_page_vcenter)
        self.after(40, self._sync_active_page_vcenter)
        self.after(120, self._sync_active_page_vcenter)
        self.after(280, self._sync_active_page_vcenter)

    def _sync_visible_pinned_vcenters(self):
        """Backward-compatible alias: only the raised step is laid out."""
        self._sync_active_page_vcenter()

    def _sync_active_page_vcenter(self):
        """Center page content when it fits; top-align when scrolling / overflowing."""
        key = getattr(self, "_current_step", None)
        if not key or not hasattr(self, "step_views"):
            return
        frame = self.step_views.get(key)
        if frame is None:
            return
        self._sync_pinned_page_vcenter(frame)

    def _measure_scrollable_inner_height(self, scrollable) -> int:
        """Pixel height of scrollable inner content (window offset ignored)."""
        try:
            # reqheight is independent of canvas y-offset (avoids measure flicker).
            need = int(scrollable.winfo_reqheight())
            if need > 1:
                return need
        except Exception:  # noqa: BLE001
            pass
        try:
            canvas = scrollable._parent_canvas
            window_id = getattr(scrollable, "_create_window_id", None)
            if window_id is not None:
                canvas.coords(window_id, 0, 0)
            scrollable.update_idletasks()
            bbox = canvas.bbox("all")
            if bbox is None:
                return 1
            return max(1, int(bbox[3] - bbox[1]))
        except Exception:  # noqa: BLE001
            return 1

    def _settings_content_fits(self, avail: int, need: int) -> bool:
        return need <= avail + 1

    def _apply_settings_fits_chrome(
        self,
        content,
        *,
        center: bool,
        avail: int,
        need: int,
    ) -> None:
        """Hide scrollbar + lock scrollregion when settings content fits.

        CTkScrollableFrame's Configure handler sets scrollregion=bbox("all").
        After small→fullscreen, a leftover y-offset bbox makes the thumb sit in
        the middle of the content. Use a canvas-sized scrollregion instead.
        """
        canvas = content._parent_canvas
        scrollbar = content._scrollbar
        window_id = getattr(content, "_create_window_id", None)
        try:
            scrollbar.grid_remove()
        except Exception:  # noqa: BLE001
            pass
        y_off = max(0, (avail - need) // 2) if center else 0
        if window_id is not None:
            try:
                cur = canvas.coords(window_id)
            except Exception:  # noqa: BLE001
                cur = None
            want = [0.0, float(y_off)]
            if cur is None or len(cur) < 2 or abs(cur[0] - want[0]) > 0.5 or abs(cur[1] - want[1]) > 0.5:
                canvas.coords(window_id, 0, y_off)
        content.update_idletasks()
        cw = max(int(canvas.winfo_width()), 1)
        ch = max(int(canvas.winfo_height()), avail, 1)
        try:
            current_region = canvas.cget("scrollregion")
        except Exception:  # noqa: BLE001
            current_region = ""
        desired = f"0 0 {cw} {ch}"
        # Avoid redundant configure calls that retrigger CTk Configure.
        if str(current_region).replace(",", " ") != desired:
            canvas.configure(scrollregion=(0, 0, cw, ch))
        canvas.yview_moveto(0)

    def _repair_settings_scroll_after_resize(self, _event=None):
        """Idle repair for settings after window grow / fullscreen (no tight loop)."""
        if getattr(self, "_current_step", None) != "settings":
            return
        frame = getattr(self, "step_views", {}).get("settings")
        if frame is None:
            return
        meta = getattr(frame, "_vcenter_meta", None)
        if not meta or not meta.get("scrollable"):
            return
        content = meta.get("content")
        middle = meta.get("middle")
        if content is None or middle is None:
            return
        try:
            if not frame.winfo_ismapped():
                return
            avail = max(int(middle.winfo_height()), 1)
            if avail < 48:
                return
            need = self._measure_scrollable_inner_height(content)
            if not self._settings_content_fits(avail, need):
                return
            center = bool(self._content_centering)
            meta["centered"] = center
            self._apply_settings_fits_chrome(
                content, center=center, avail=avail, need=need
            )
        except Exception:  # noqa: BLE001
            pass

    def _schedule_settings_scroll_repair(self, _event=None):
        after_id = getattr(self, "_settings_scroll_repair_after", None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception:  # noqa: BLE001
                pass
        # Debounced: catches CTk's scrollregion=bbox overwrite after fullscreen.
        self._settings_scroll_repair_after = self.after(
            80, self._repair_settings_scroll_after_resize
        )

    def _sync_settings_scroll_vcenter(self, parent: customtkinter.CTkFrame):
        """Settings: fill the page; center via canvas offset when no bar needed.

        Placing a CTkScrollableFrame collapses its canvas (invisible layout).
        Always pack-fill, then after size settles show/hide the scrollbar.
        """
        meta = getattr(parent, "_vcenter_meta", None)
        if not meta:
            return
        content = meta["content"]
        middle = meta["middle"]
        try:
            if not parent.winfo_ismapped():
                return
            scale = self._widget_scaling_factor()
            # Keep scrollable filling the middle band (never place it).
            if str(content.winfo_manager()) != "pack":
                content.place_forget()
                content.grid_forget()
            avail = max(int(middle.winfo_height()), 1)
            content.configure(height=max(1, int(round(avail / scale))))
            content.pack(side="top", fill="both", expand=True)
            parent.update_idletasks()

            avail = int(middle.winfo_height())
            if avail < 48:
                meta["centered"] = None
                meta.pop("layout_sig", None)
                return

            need = self._measure_scrollable_inner_height(content)
            fits = self._settings_content_fits(avail, need)
            center = bool(self._content_centering) and fits
            sig = (avail, need, center, bool(self._content_centering))
            if sig == meta.get("layout_sig"):
                # Same layout math, but small→fullscreen can leave a mapped bar
                # or a corrupted scrollregion — repair fits chrome only.
                if fits:
                    self._apply_settings_fits_chrome(
                        content, center=center, avail=avail, need=need
                    )
                return
            meta["layout_sig"] = sig
            meta["centered"] = center

            canvas = content._parent_canvas
            window_id = getattr(content, "_create_window_id", None)

            if fits:
                self._apply_settings_fits_chrome(
                    content, center=center, avail=avail, need=need
                )
                self._schedule_settings_scroll_repair()
            else:
                # Overflow: top-align and show scrollbar.
                if window_id is not None:
                    canvas.coords(window_id, 0, 0)
                content.update_idletasks()
                bbox = canvas.bbox("all")
                if bbox is not None:
                    _x1, _y1, x2, y2 = bbox
                    canvas.configure(
                        scrollregion=(0, 0, max(0, x2), max(0, y2 - _y1))
                    )
                content._create_grid()
                canvas.yview_moveto(0)
        except Exception:  # noqa: BLE001
            pass

    def _sync_pinned_page_vcenter(self, parent: customtkinter.CTkFrame):
        """Center field content between fixed header/footer when it fits."""
        meta = getattr(parent, "_vcenter_meta", None)
        if not meta:
            return
        if meta.get("scrollable"):
            self._sync_settings_scroll_vcenter(parent)
            return
        try:
            if not parent.winfo_ismapped():
                return
            parent.update_idletasks()
            middle = meta["middle"]
            content = meta["content"]
            top = meta["top"]
            bottom = meta["bottom"]
            avail = int(middle.winfo_height())
            need = max(int(content.winfo_reqheight()), 1)
            if avail < 48:
                meta["centered"] = None
                meta.pop("layout_sig", None)
                return
            center = bool(self._content_centering) and need <= avail + 1
            sig = (avail, need, center, bool(self._content_centering))
            if sig == meta.get("layout_sig"):
                return
            meta["layout_sig"] = sig
            meta["centered"] = center

            # Drop any prior manager so place/pack can take over cleanly.
            for spacer in (top, content, bottom):
                spacer.pack_forget()
                spacer.place_forget()
                spacer.grid_forget()

            if center:
                # Place in the middle band (header/footer stay docked outside).
                content.place(relx=0.5, rely=0.5, anchor="center", relwidth=1.0)
            else:
                content.pack(side="top", fill="both", expand=True)
        except Exception:  # noqa: BLE001
            pass

    def _invalidate_page_vcenters(self):
        for frame in getattr(self, "step_views", {}).values():
            meta = getattr(frame, "_vcenter_meta", None)
            if meta is not None:
                meta["centered"] = None
                meta.pop("layout_sig", None)

    def _make_pinned_footer_layout(
        self,
        parent: customtkinter.CTkFrame,
        *,
        with_header: bool = True,
        with_footer: bool = True,
    ) -> tuple[customtkinter.CTkFrame, customtkinter.CTkFrame, customtkinter.CTkFrame]:
        """Fixed optional header + centerable fields + optional pinned footer.

        Returns (header, content, footer). Only ``content`` is vertically
        centered when it fits; header/footer stay docked when present.
        """
        parent.grid_columnconfigure(0, weight=1)

        header = customtkinter.CTkFrame(parent, fg_color="transparent")
        header.grid_columnconfigure(0, weight=1)

        middle = customtkinter.CTkFrame(parent, fg_color="transparent")
        middle.grid_columnconfigure(0, weight=1)

        top = customtkinter.CTkFrame(middle, fg_color="transparent", height=0)
        content = customtkinter.CTkFrame(middle, fg_color="transparent")
        content.grid_columnconfigure(0, weight=1)
        bottom = customtkinter.CTkFrame(middle, fg_color="transparent", height=0)

        footer = customtkinter.CTkFrame(parent, fg_color="transparent")
        footer.grid_columnconfigure(0, weight=1)

        if with_header and with_footer:
            parent.grid_rowconfigure(0, weight=0)
            parent.grid_rowconfigure(1, weight=1)
            parent.grid_rowconfigure(2, weight=0)
            header.grid(row=0, column=0, sticky="ew")
            middle.grid(row=1, column=0, sticky="nsew")
            footer.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        elif with_header and not with_footer:
            parent.grid_rowconfigure(0, weight=0)
            parent.grid_rowconfigure(1, weight=1)
            header.grid(row=0, column=0, sticky="ew")
            middle.grid(row=1, column=0, sticky="nsew")
        elif not with_header and with_footer:
            parent.grid_rowconfigure(0, weight=1)
            parent.grid_rowconfigure(1, weight=0)
            middle.grid(row=0, column=0, sticky="nsew")
            footer.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        else:
            # Settings: full-page middle, same place/pack sync as step 2.
            parent.grid_rowconfigure(0, weight=1)
            middle.grid(row=0, column=0, sticky="nsew")

        # Default centered; sync may switch to top-align on overflow.
        content.place(relx=0.5, rely=0.5, anchor="center", relwidth=1.0)

        parent._vcenter_meta = {
            "top": top,
            "bottom": bottom,
            "middle": middle,
            "content": content,
            "centered": None,
            "with_header": with_header,
            "with_footer": with_footer,
        }
        parent.bind(
            "<Configure>",
            lambda _e, p=parent: self.after_idle(
                lambda: self._sync_pinned_page_vcenter(p)
            ),
            add="+",
        )
        middle.bind(
            "<Configure>",
            lambda _e, p=parent: self.after_idle(
                lambda: self._sync_pinned_page_vcenter(p)
            ),
            add="+",
        )
        self.after_idle(lambda p=parent: self._sync_pinned_page_vcenter(p))
        return header, content, footer

    def _build_settings_controls(self, parent: customtkinter.CTkFrame):
        # Same place/pack vcenter as step 2; scrollable content shows a bar only
        # after layout settles and content overflows.
        _header, old_content, _footer = self._make_pinned_footer_layout(
            parent, with_header=False, with_footer=False
        )
        meta = parent._vcenter_meta
        middle = meta["middle"]
        old_content.destroy()

        self.settings_scroll = customtkinter.CTkScrollableFrame(
            middle,
            fg_color="transparent",
            corner_radius=0,
        )
        self.settings_scroll.grid_columnconfigure(0, weight=1)
        meta["content"] = self.settings_scroll
        meta["scrollable"] = True
        # After small→fullscreen, CTk overwrites scrollregion with an offset bbox.
        # Debounced repair only — not a tight Configure loop.
        self.settings_scroll.bind(
            "<Configure>", self._schedule_settings_scroll_repair, add="+"
        )
        self.settings_scroll._parent_canvas.bind(
            "<Configure>", self._schedule_settings_scroll_repair, add="+"
        )

        content = self.settings_scroll
        self._bind_scrollable_mousewheel(self.settings_scroll, self.settings_scroll)
        self._bind_scrollable_mousewheel(
            self.settings_scroll, self.settings_scroll._parent_canvas
        )

        customtkinter.CTkLabel(
            content,
            text="EAMS 登录",
            anchor="w",
            font=customtkinter.CTkFont(size=FONT_SECTION, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self._track_content_wrap(
            customtkinter.CTkLabel(
                content,
                text="填写账号密码并保存，自动填写时会代为登录。",
                anchor="w",
                font=customtkinter.CTkFont(size=FONT_BODY),
                text_color="gray60",
                wraplength=CONTENT_WRAP,
                justify="left",
            )
        ).grid(row=1, column=0, sticky="ew", pady=(0, 12))

        saved_user, saved_pass = load_credentials()

        cred_row = customtkinter.CTkFrame(content, fg_color="transparent")
        cred_row.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        cred_row.grid_columnconfigure((0, 1), weight=1, uniform="eams_creds")

        user_col = customtkinter.CTkFrame(cred_row, fg_color="transparent")
        user_col.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        user_col.grid_columnconfigure(0, weight=1)
        customtkinter.CTkLabel(user_col, text="用户名", anchor="w").grid(
            row=0, column=0, sticky="ew", pady=(0, 4)
        )
        self.eams_username_entry = self._make_field_entry(
            user_col, placeholder="EAMS 用户名"
        )
        self.eams_username_entry.grid(row=1, column=0, sticky="ew")
        if saved_user:
            self.eams_username_entry.insert(0, saved_user)

        pass_col = customtkinter.CTkFrame(cred_row, fg_color="transparent")
        pass_col.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        pass_col.grid_columnconfigure(0, weight=1)
        customtkinter.CTkLabel(pass_col, text="密码", anchor="w").grid(
            row=0, column=0, sticky="ew", pady=(0, 4)
        )
        self.eams_password_entry = self._make_field_entry(
            pass_col, placeholder="EAMS 密码", show="•"
        )
        self.eams_password_entry.grid(row=1, column=0, sticky="ew")
        if saved_pass:
            self.eams_password_entry.insert(0, saved_pass)

        customtkinter.CTkButton(
            content,
            corner_radius=UI_RADIUS,
            text="保存登录信息",
            height=40,
            fg_color=PRIMARY_BTN_FG,
            hover_color=PRIMARY_BTN_HOVER,
            text_color=PRIMARY_BTN_TEXT,
            font=self._button_font(FONT_BUTTON),
            command=self._save_eams_login_info,
        ).grid(row=3, column=0, sticky="ew")

        customtkinter.CTkLabel(
            content,
            text="自动填写",
            anchor="w",
            font=customtkinter.CTkFont(size=FONT_SECTION, weight="bold"),
        ).grid(row=4, column=0, sticky="ew", pady=(24, 8))

        self._track_content_wrap(
            customtkinter.CTkLabel(
                content,
                text="每个自动化步骤之间的等待时间（秒）。默认 1 秒。",
                anchor="w",
                font=customtkinter.CTkFont(size=FONT_BODY),
                text_color="gray60",
                wraplength=CONTENT_WRAP,
                justify="left",
            )
        ).grid(row=5, column=0, sticky="ew", pady=(0, 8))

        delay_row = customtkinter.CTkFrame(content, fg_color="transparent")
        delay_row.grid(row=6, column=0, sticky="ew", pady=(0, 4))
        delay_row.grid_columnconfigure(0, weight=1)
        customtkinter.CTkLabel(delay_row, text="步骤间隔（秒）", anchor="w").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        self.autofill_step_delay_entry = self._make_field_entry(
            delay_row, placeholder="1.0"
        )
        self.autofill_step_delay_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self.autofill_step_delay_entry.insert(0, f"{self._autofill_step_delay_sec:g}")
        customtkinter.CTkButton(
            delay_row,
            corner_radius=UI_RADIUS,
            text="保存",
            width=88,
            height=40,
            fg_color=PRIMARY_BTN_FG,
            hover_color=PRIMARY_BTN_HOVER,
            text_color=PRIMARY_BTN_TEXT,
            font=self._button_font(FONT_BUTTON),
            command=self._save_autofill_step_delay,
        ).grid(row=1, column=1, sticky="e")

        customtkinter.CTkLabel(
            content,
            text="界面与功能",
            anchor="w",
            font=customtkinter.CTkFont(size=FONT_SECTION, weight="bold"),
        ).grid(row=7, column=0, sticky="ew", pady=(24, 8))

        switches_row = customtkinter.CTkFrame(content, fg_color="transparent")
        switches_row.grid(row=8, column=0, sticky="ew", pady=(0, 16))
        switches_row.grid_columnconfigure((0, 1), weight=1, uniform="settings_switches")

        self.ui_zoom_switch = customtkinter.CTkSwitch(
            switches_row,
            text="放大界面",
            font=customtkinter.CTkFont(size=FONT_BODY),
        )
        if self._ui_zoomed:
            self.ui_zoom_switch.select()
        else:
            self.ui_zoom_switch.deselect()
        self.ui_zoom_switch.configure(command=self._on_ui_zoom_toggle)
        self.ui_zoom_switch.grid(row=0, column=0, sticky="w", padx=(0, 4))

        self.ocr_enabled_switch = customtkinter.CTkSwitch(
            switches_row,
            text="启用 OCR",
            font=customtkinter.CTkFont(size=FONT_BODY),
        )
        if self._ocr_enabled:
            self.ocr_enabled_switch.select()
        else:
            self.ocr_enabled_switch.deselect()
        self.ocr_enabled_switch.configure(command=self._on_ocr_enabled_toggle)
        self.ocr_enabled_switch.grid(row=0, column=1, sticky="w", padx=(4, 0))

        self.buttons_bold_switch = customtkinter.CTkSwitch(
            switches_row,
            text="按钮加粗",
            font=customtkinter.CTkFont(size=FONT_BODY),
        )
        if self._buttons_bold:
            self.buttons_bold_switch.select()
        else:
            self.buttons_bold_switch.deselect()
        self.buttons_bold_switch.configure(command=self._on_buttons_bold_toggle)
        self.buttons_bold_switch.grid(row=1, column=0, sticky="w", padx=(0, 4), pady=(10, 0))

        self.content_centering_switch = customtkinter.CTkSwitch(
            switches_row,
            text="内容居中",
            font=customtkinter.CTkFont(size=FONT_BODY),
        )
        if self._content_centering:
            self.content_centering_switch.select()
        else:
            self.content_centering_switch.deselect()
        self.content_centering_switch.configure(command=self._on_content_centering_toggle)
        self.content_centering_switch.grid(
            row=1, column=1, sticky="w", padx=(4, 0), pady=(10, 0)
        )

        self.status_dots_switch = customtkinter.CTkSwitch(
            switches_row,
            text="图标样式",
            font=customtkinter.CTkFont(size=FONT_BODY),
        )
        if self._status_dots:
            self.status_dots_switch.select()
        else:
            self.status_dots_switch.deselect()
        self.status_dots_switch.configure(command=self._on_status_dots_toggle)
        self.status_dots_switch.grid(row=2, column=0, sticky="w", padx=(0, 4), pady=(10, 0))

        self.doc_list_scale_fonts_switch = customtkinter.CTkSwitch(
            switches_row,
            text="列表文字缩放",
            font=customtkinter.CTkFont(size=FONT_BODY),
        )
        if self._doc_list_scale_fonts:
            self.doc_list_scale_fonts_switch.select()
        else:
            self.doc_list_scale_fonts_switch.deselect()
        self.doc_list_scale_fonts_switch.configure(
            command=self._on_doc_list_scale_fonts_toggle
        )
        self.doc_list_scale_fonts_switch.grid(
            row=2, column=1, sticky="w", padx=(4, 0), pady=(10, 0)
        )

        customtkinter.CTkLabel(
            content,
            text="失败证书目录",
            anchor="w",
            font=customtkinter.CTkFont(size=FONT_SECTION, weight="bold"),
        ).grid(row=10, column=0, sticky="ew", pady=(8, 8))

        # Description + path on separate rows so long paths aren't cropped.
        self._track_content_wrap(
            customtkinter.CTkLabel(
                content,
                text="移出未解析/失败证书时，会复制到此文件夹。",
                anchor="w",
                font=customtkinter.CTkFont(size=FONT_BODY),
                text_color="gray60",
                wraplength=CONTENT_WRAP,
                justify="left",
            )
        ).grid(row=11, column=0, sticky="ew", pady=(0, 4))
        self.failed_items_dir_label = self._track_content_wrap(
            customtkinter.CTkLabel(
                content,
                text=str(self._failed_items_dir),
                anchor="w",
                font=customtkinter.CTkFont(size=FONT_META),
                text_color="gray60",
                wraplength=CONTENT_WRAP,
                justify="left",
            )
        )
        self.failed_items_dir_label.grid(row=12, column=0, sticky="ew", pady=(0, 8))

        failed_dir_row = customtkinter.CTkFrame(content, fg_color="transparent")
        failed_dir_row.grid(row=13, column=0, sticky="ew", pady=(0, 16))
        failed_dir_row.grid_columnconfigure((0, 1), weight=1)

        customtkinter.CTkButton(
            failed_dir_row,
            corner_radius=UI_RADIUS,
            text="选择文件夹…",
            height=40,
            fg_color=PRIMARY_BTN_FG,
            hover_color=PRIMARY_BTN_HOVER,
            text_color=PRIMARY_BTN_TEXT,
            font=self._button_font(FONT_BUTTON),
            command=self._pick_failed_items_dir,
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        customtkinter.CTkButton(
            failed_dir_row,
            corner_radius=UI_RADIUS,
            text="恢复默认",
            height=40,
            fg_color=SECONDARY_BTN_FG,
            hover_color=SECONDARY_BTN_HOVER,
            text_color=SECONDARY_BTN_TEXT,
            font=self._button_font(FONT_BUTTON),
            command=self._reset_failed_items_dir,
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        customtkinter.CTkLabel(
            content,
            text="测试模式",
            anchor="w",
            font=customtkinter.CTkFont(size=FONT_SECTION, weight="bold"),
        ).grid(row=14, column=0, sticky="ew", pady=(8, 8))

        self._track_content_wrap(
            customtkinter.CTkLabel(
                content,
                text=(
                    "开启后使用 EAMS 测试环境（UAT）自动填写，"
                    "并在启动时自动加载演示证书文件夹。"
                ),
                anchor="w",
                font=customtkinter.CTkFont(size=FONT_BODY),
                text_color="gray60",
                wraplength=CONTENT_WRAP,
                justify="left",
            )
        ).grid(row=15, column=0, sticky="ew", pady=(0, 6))
        self.testing_mode_switch = customtkinter.CTkSwitch(
            content,
            text="启用测试模式（EAMS UAT）",
            font=customtkinter.CTkFont(size=FONT_BODY),
        )
        if self._testing_mode:
            self.testing_mode_switch.select()
        else:
            self.testing_mode_switch.deselect()
        self.testing_mode_switch.configure(command=self._on_testing_mode_toggle)
        self.testing_mode_switch.grid(row=16, column=0, sticky="w", pady=(0, 12))

        customtkinter.CTkLabel(
            content,
            text="演示证书文件夹",
            anchor="w",
            font=customtkinter.CTkFont(size=FONT_SECTION, weight="bold"),
        ).grid(row=17, column=0, sticky="ew", pady=(8, 8))

        self._track_content_wrap(
            customtkinter.CTkLabel(
                content,
                text="测试模式启动时自动加载此文件夹中的证书。",
                anchor="w",
                font=customtkinter.CTkFont(size=FONT_BODY),
                text_color="gray60",
                wraplength=CONTENT_WRAP,
                justify="left",
            )
        ).grid(row=18, column=0, sticky="ew", pady=(0, 4))
        self.demo_folder_label = self._track_content_wrap(
            customtkinter.CTkLabel(
                content,
                text=self._demo_folder_display(),
                anchor="w",
                font=customtkinter.CTkFont(size=FONT_META),
                text_color="gray60",
                wraplength=CONTENT_WRAP,
                justify="left",
            )
        )
        self.demo_folder_label.grid(row=19, column=0, sticky="ew", pady=(0, 8))

        demo_dir_row = customtkinter.CTkFrame(content, fg_color="transparent")
        demo_dir_row.grid(row=20, column=0, sticky="ew", pady=(0, 16))
        demo_dir_row.grid_columnconfigure((0, 1), weight=1)

        customtkinter.CTkButton(
            demo_dir_row,
            corner_radius=UI_RADIUS,
            text="选择文件夹…",
            height=40,
            fg_color=PRIMARY_BTN_FG,
            hover_color=PRIMARY_BTN_HOVER,
            text_color=PRIMARY_BTN_TEXT,
            font=self._button_font(FONT_BUTTON),
            command=self._pick_demo_folder,
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        customtkinter.CTkButton(
            demo_dir_row,
            corner_radius=UI_RADIUS,
            text="清除",
            height=40,
            fg_color=SECONDARY_BTN_FG,
            hover_color=SECONDARY_BTN_HOVER,
            text_color=SECONDARY_BTN_TEXT,
            font=self._button_font(FONT_BUTTON),
            command=self._clear_demo_folder,
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self._bind_scrollable_mousewheel(self.settings_scroll, self.settings_scroll)
        self._schedule_active_page_vcenter(force=True)

    def _demo_folder_display(self) -> str:
        return self._demo_folder or "未选择演示文件夹"

    def _update_demo_folder_label(self):
        if hasattr(self, "demo_folder_label"):
            self.demo_folder_label.configure(text=self._demo_folder_display())

    def _on_testing_mode_toggle(self):
        enabled = bool(self.testing_mode_switch.get())
        self._testing_mode = enabled
        save_testing_mode(enabled)
        if enabled:
            env_note = "EAMS 测试环境（auth.masuat.apps.ocpuat）"
            if self._demo_folder and Path(self._demo_folder).is_dir():
                self.set_status(f"测试模式已开启 · {env_note} · 启动时加载：{self._demo_folder}")
            else:
                self.set_status(f"测试模式已开启 · {env_note} · 请先选择演示证书文件夹")
                self.show_toast(
                    "已切换到 EAMS 测试环境。\n请先选择演示证书文件夹，下次启动才会自动加载。",
                    title="测试模式",
                    duration_ms=TOAST_SUCCESS_MS,
                )
        else:
            self.set_status("测试模式已关闭 · 使用 EAMS 正式环境")
            self.show_success_toast(
                "已切换到 EAMS 正式环境。",
                title="测试模式",
            )

    def _pick_demo_folder(self):
        initial = self._demo_folder if self._demo_folder and Path(self._demo_folder).is_dir() else None
        path = filedialog.askdirectory(
            parent=self,
            title="选择演示证书文件夹",
            initialdir=initial,
        )
        if not path:
            self.set_status("未更改演示证书文件夹")
            return
        self._demo_folder = save_demo_folder(path)
        self._update_demo_folder_label()
        msg = f"演示文件夹已更新：{self._demo_folder}"
        self.set_status(msg)
        self.show_success_toast(msg, title="测试模式")

    def _clear_demo_folder(self):
        self._demo_folder = save_demo_folder("")
        self._update_demo_folder_label()
        self.set_status("已清除演示证书文件夹")
        self.show_success_toast("已清除演示证书文件夹。", title="测试模式")

    def _maybe_autoload_demo_folder(self):
        """When testing mode is on, load the configured demo folder at launch."""
        if not self._testing_mode:
            return
        folder = (self._demo_folder or "").strip()
        if not folder:
            self.set_status("测试模式已开启，但未配置演示文件夹")
            self.show_toast(
                "测试模式已开启，但未配置演示证书文件夹。",
                title="测试模式",
                duration_ms=TOAST_SUCCESS_MS,
            )
            return
        path = Path(folder)
        if not path.is_dir():
            self.set_status(f"演示文件夹不存在：{folder}")
            self.show_toast(
                f"演示文件夹不存在：\n{folder}",
                title="测试模式",
            )
            return
        self.set_status(f"测试模式：正在加载演示文件夹…")
        self._load_folder(str(path))

    def _failed_items_dir_display(self) -> str:
        return str(self._failed_items_dir)

    def _failed_items_dir_short(self) -> str:
        return self._failed_items_dir.name or str(self._failed_items_dir)

    def _update_failed_items_dir_label(self):
        if hasattr(self, "failed_items_dir_label"):
            self.failed_items_dir_label.configure(text=self._failed_items_dir_display())

    def _set_failed_items_dir(self, folder: Path):
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"无法使用失败证书目录：{exc}")
            self.show_toast(
                f"无法使用该文件夹：{exc}",
                title="失败证书目录",
            )
            return False
        self._failed_items_dir = folder.resolve()
        save_failed_items_dir(self._failed_items_dir)
        self._update_failed_items_dir_label()
        return True

    def _pick_failed_items_dir(self):
        path = filedialog.askdirectory(
            parent=self,
            title="选择失败证书目录",
            initialdir=str(self._failed_items_dir),
        )
        if not path:
            self.set_status("未更改失败证书目录")
            return
        if self._set_failed_items_dir(Path(path)):
            msg = f"失败证书目录已更新：{self._failed_items_dir}"
            self.set_status(msg)
            self.show_success_toast(msg, title="失败证书目录")

    def _reset_failed_items_dir(self):
        if self._set_failed_items_dir(DEFAULT_FAILED_ITEMS_DIR):
            msg = f"已恢复默认目录：{self._failed_items_dir}"
            self.set_status(msg)
            self.show_success_toast(msg, title="失败证书目录")

    def _on_ui_zoom_toggle(self):
        self._apply_ui_zoom(bool(self.ui_zoom_switch.get()))

    def _on_buttons_bold_toggle(self):
        self._apply_buttons_bold(bool(self.buttons_bold_switch.get()))

    def _on_content_centering_toggle(self):
        self._apply_content_centering(bool(self.content_centering_switch.get()))

    def _on_status_dots_toggle(self):
        self._apply_status_dots(bool(self.status_dots_switch.get()))

    def _on_doc_list_scale_fonts_toggle(self):
        self._apply_doc_list_scale_fonts(bool(self.doc_list_scale_fonts_switch.get()))

    def _apply_content_centering(self, enabled: bool):
        self._content_centering = enabled
        save_content_centering(enabled)
        self._schedule_active_page_vcenter(force=True)
        self.set_status("已开启内容居中" if enabled else "已关闭内容居中")

    def _apply_status_dots(self, enabled: bool):
        self._status_dots = enabled
        save_status_dots(enabled)
        self._refresh_doc_list_marks()
        self.set_status("已使用圆点图标" if enabled else "已使用表情图标")

    def _apply_doc_list_scale_fonts(self, enabled: bool):
        self._doc_list_scale_fonts = enabled
        save_doc_list_scale_fonts(enabled)
        self._refresh_doc_list_fonts()
        self.set_status(
            "已开启列表文字缩放" if enabled else "已关闭列表文字缩放（固定字号）"
        )

    def _apply_buttons_bold(self, bold: bool):
        self._buttons_bold = bold
        save_buttons_bold(bold)
        weight = "bold" if bold else "normal"

        def walk(widget):
            if isinstance(widget, customtkinter.CTkButton):
                try:
                    font = widget.cget("font")
                    size = FONT_BUTTON
                    if isinstance(font, customtkinter.CTkFont):
                        size = int(font.cget("size"))
                    widget.configure(
                        font=customtkinter.CTkFont(size=size, weight=weight)
                    )
                    self._fix_ctk_button_text_vcenter(widget)
                except Exception:  # noqa: BLE001
                    pass
            for child in widget.winfo_children():
                walk(child)

        walk(self)
        self.set_status("已开启按钮加粗" if bold else "已关闭按钮加粗")

    def _on_ocr_enabled_toggle(self):
        enabled = bool(self.ocr_enabled_switch.get())
        if self._extract_busy and enabled != self._ocr_enabled:
            # Revert while OCR is running.
            if self._ocr_enabled:
                self.ocr_enabled_switch.select()
            else:
                self.ocr_enabled_switch.deselect()
            self.set_status("正在处理中，请稍后再切换 OCR")
            self.show_toast(
                "正在处理中，请稍后再切换 OCR。",
                title="OCR 提取",
                duration_ms=TOAST_SUCCESS_MS,
            )
            return
        self._apply_ocr_enabled(enabled)

    def _apply_ocr_enabled(self, enabled: bool):
        self._ocr_enabled = enabled
        save_ocr_enabled(enabled)
        self._update_extract_ocr_ui()
        self._rebuild_doc_list()
        self.set_status("已启用 OCR" if enabled else "已关闭 OCR")

    def _update_extract_ocr_ui(self):
        """Show/hide OCR progress and restyle the extract-side action button."""
        if not hasattr(self, "ocr_extract_button"):
            return
        if self._ocr_enabled:
            if hasattr(self, "ocr_progress"):
                self.ocr_progress.grid(row=0, column=0, sticky="ew", pady=(0, 8))
            if hasattr(self, "ocr_progress_label"):
                self.ocr_progress_label.grid(row=1, column=0, sticky="ew", pady=(0, 8))
            self.ocr_extract_button.configure(
                text="OCR提取",
                fg_color=SUCCESS_BTN_FG,
                hover_color=SUCCESS_BTN_HOVER,
                text_color=SUCCESS_BTN_TEXT,
                command=self._on_ocr_extract,
            )
        else:
            if hasattr(self, "ocr_progress"):
                self.ocr_progress.grid_remove()
            if hasattr(self, "ocr_progress_label"):
                self.ocr_progress_label.grid_remove()
            self.ocr_extract_button.configure(
                text="移出失败证书",
                fg_color=DANGER_BTN_FG,
                hover_color=DANGER_BTN_HOVER,
                text_color=DANGER_BTN_TEXT,
                command=self._on_remove_failed_certificates,
            )

    def _apply_ui_zoom(self, zoomed: bool):
        self._ui_zoomed = zoomed
        apply_ui_scale(zoomed)
        save_ui_zoomed(zoomed)
        # Zoom-out (esp. fullscreen) can leave an unfilled black edge unless we
        # re-assert fixed widths and force the window to relayout.
        self._refresh_layout_after_scale()
        self.set_status("已切换为放大界面" if zoomed else "已切换为标准界面")

    def _refresh_layout_after_scale(self):
        """Recompute layout after widget/window scaling changes."""
        self._controls_inner_width = -1
        self._controls_inner_height = -1
        self._apply_layout_column_minsizes()
        self._apply_min_window_size()
        self._invalidate_page_vcenters()

        if hasattr(self, "doc_sidebar"):
            # Re-apply design width so scaled pixel size updates on zoom-out.
            self.doc_sidebar.configure(width=DOC_SIDEBAR_WIDTH)
            self.doc_sidebar.grid(row=0, column=0, sticky="nsew")
        if hasattr(self, "controls_panel"):
            self.controls_panel.grid(row=0, column=1, sticky="nsew", padx=0)

        self.update_idletasks()
        self._nudge_window_geometry_after_scale()
        self.update_idletasks()
        self._sync_controls_inner_width()
        # Second pass after CTk finishes propagating the new scale.
        self.after_idle(self._sync_controls_inner_width)
        self.after(40, self._sync_controls_inner_width)
        self.after(40, self._restyle_primary_action_buttons)
        if hasattr(self, "doc_list_frame"):
            self.after(40, self._schedule_doc_list_scrollbar_sync)
            self.after(40, self._refresh_doc_list_fonts)
        self._schedule_active_page_vcenter(force=True)

    def _nudge_window_geometry_after_scale(self):
        """Force Tk/CTk to refill the window after scale-down (clears black bars)."""
        try:
            # Prefer re-applying work-area snap (never state('zoomed') — taskbar).
            if self._pdf_preview_layout_active:
                self._snap_app_left_half()
                return
            if sys.platform == "win32":
                self._apply_window_fullscreen()
                return
            width = int(self.winfo_width())
            height = int(self.winfo_height())
            if width <= 1 or height <= 1:
                return
            # macOS / other: 1px nudge clears letterboxing.
            self.geometry(f"{width}x{height + 1}")
            self.update_idletasks()
            self.geometry(f"{width}x{height}")
        except Exception:  # noqa: BLE001
            pass

    def _eams_credentials(self) -> tuple[str, str]:
        username = ""
        password = ""
        if hasattr(self, "eams_username_entry"):
            username = self.eams_username_entry.get().strip()
        if hasattr(self, "eams_password_entry"):
            password = self.eams_password_entry.get()
        return username, password

    def set_status(self, message: str):
        print(f"[VinCert] {message}")

    def _toast_target_width(self) -> int:
        """Fixed design-unit width for toasts."""
        return TOAST_WIDTH

    def _ensure_toast_host(self) -> customtkinter.CTkFrame:
        if self._toast_host is None:
            host = customtkinter.CTkFrame(self, fg_color="transparent")
            self._toast_host = host
            self._place_toast_host()
        return self._toast_host

    def _toast_host_x(self) -> int:
        """Right inset for the toast stack; shift left when autofill log is open."""
        if self._autofill_log_frame is not None:
            return -(AUTOFILL_LOG_PAD + AUTOFILL_LOG_WIDTH + TOAST_PAD)
        return -TOAST_PAD

    def _place_toast_host(self):
        host = self._toast_host
        if host is None:
            return
        # Compact toasts anchor to the top-right (shift left when terminal is open).
        host.place(
            relx=1.0,
            rely=0.0,
            x=self._toast_host_x(),
            y=TOAST_PAD,
            anchor="ne",
        )
        try:
            host.lift()
        except Exception:  # noqa: BLE001
            pass

    def _relayout_toast_stack(self):
        host = self._toast_host
        if host is None:
            return
        for entry in self._toasts:
            try:
                entry["frame"].pack_forget()
            except Exception:  # noqa: BLE001
                pass
        for i, entry in enumerate(self._toasts):
            gap = TOAST_STACK_GAP if i < len(self._toasts) - 1 else 0
            try:
                entry["frame"].pack(side="top", anchor="e", pady=(0, gap))
            except Exception:  # noqa: BLE001
                pass
        self._place_toast_host()

    def _autofill_log_height(self) -> int:
        """Terminal occupies roughly the top half of the window."""
        scale = self._widget_scaling_factor()
        try:
            win_h = int(round(self.winfo_height() / scale))
        except Exception:  # noqa: BLE001
            win_h = 720
        half = max(1, (win_h - 2 * AUTOFILL_LOG_PAD) // 2)
        return max(200, half)

    def _sync_autofill_log_geometry(self, _event=None):
        frame = self._autofill_log_frame
        if frame is None:
            return
        try:
            frame.configure(
                width=AUTOFILL_LOG_WIDTH,
                height=self._autofill_log_height(),
            )
            frame.place(
                relx=1.0,
                rely=0.0,
                x=-AUTOFILL_LOG_PAD,
                y=AUTOFILL_LOG_PAD,
                anchor="ne",
            )
            frame.lift()
        except Exception:  # noqa: BLE001
            pass
        self._place_toast_host()

    def open_autofill_log(self, *, title: str = "自动填写"):
        """Show the top-half autofill terminal panel (clears any prior log)."""
        self.close_autofill_log()
        accent = SUCCESS_BTN_FG
        h = self._autofill_log_height()
        frame = customtkinter.CTkFrame(
            self,
            width=AUTOFILL_LOG_WIDTH,
            height=h,
            corner_radius=TOAST_RADIUS,
            fg_color=TOAST_BG,
            border_width=TOAST_BORDER_WIDTH,
            border_color=accent,
        )
        frame.grid_propagate(False)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        header = customtkinter.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        header.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            header,
            text=title,
            anchor="w",
            font=customtkinter.CTkFont(size=FONT_TITLE, weight="bold"),
            text_color=TOAST_TITLE_COLOR,
        ).grid(row=0, column=0, sticky="ew")

        status = customtkinter.CTkLabel(
            header,
            text="进行中",
            anchor="e",
            font=customtkinter.CTkFont(size=FONT_META, weight="bold"),
            text_color=SUCCESS_BTN_HOVER,
        )
        status.grid(row=0, column=1, sticky="e", padx=(8, 0))

        text = customtkinter.CTkTextbox(
            frame,
            width=AUTOFILL_LOG_WIDTH - 28,
            corner_radius=UI_RADIUS,
            fg_color=("#f4f6f8", "#121212"),
            text_color=TOAST_MESSAGE_COLOR,
            border_width=0,
            font=customtkinter.CTkFont(family="Menlo", size=FONT_META),
            activate_scrollbars=False,
            wrap="word",
        )
        text.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        text.configure(state="disabled")
        # Keep view pinned to latest lines; no manual scroll.
        text.bind("<MouseWheel>", lambda _e: "break")
        text.bind("<Button-4>", lambda _e: "break")
        text.bind("<Button-5>", lambda _e: "break")
        try:
            inner = getattr(text, "_textbox", None)
            if inner is not None:
                inner.bind("<MouseWheel>", lambda _e: "break")
                inner.bind("<Button-4>", lambda _e: "break")
                inner.bind("<Button-5>", lambda _e: "break")
        except Exception:  # noqa: BLE001
            pass

        self._autofill_log_frame = frame
        self._autofill_log_text = text
        self._autofill_log_status = status
        self._sync_autofill_log_geometry()
        # Shift any already-visible compact toasts left of the terminal column.
        self._place_toast_host()
        if not getattr(self, "_autofill_log_configure_bound", False):
            self.bind("<Configure>", self._sync_autofill_log_geometry, add="+")
            self._autofill_log_configure_bound = True

    def append_autofill_log(self, message: str, *, error: bool = False):
        """Append one substep line to the autofill terminal (opens panel if needed)."""
        if self._autofill_log_frame is None or self._autofill_log_text is None:
            self.open_autofill_log()
        text = self._autofill_log_text
        if text is None:
            return
        line = (message or "").rstrip()
        if not line:
            return
        prefix = "! " if error else "> "
        try:
            text.configure(state="normal")
            text.insert("end", prefix + line + "\n")
            text.see("end")
            text.configure(state="disabled")
        except Exception:  # noqa: BLE001
            pass
        self.set_status(message)
        if self._autofill_log_frame is not None:
            try:
                self._autofill_log_frame.lift()
            except Exception:  # noqa: BLE001
                pass

    def finish_autofill_log(self, *, ok: bool = True, auto_close_ms: int = AUTOFILL_LOG_FINISH_MS):
        """Mark the autofill terminal done/failed and optionally auto-close later."""
        if self._autofill_log_status is not None:
            try:
                if ok:
                    self._autofill_log_status.configure(
                        text="完成",
                        text_color=SUCCESS_BTN_HOVER,
                    )
                    if self._autofill_log_frame is not None:
                        self._autofill_log_frame.configure(border_color=SUCCESS_BTN_FG)
                else:
                    self._autofill_log_status.configure(
                        text="失败",
                        text_color=DANGER_BTN_HOVER,
                    )
                    if self._autofill_log_frame is not None:
                        self._autofill_log_frame.configure(border_color=DANGER_BTN_FG)
            except Exception:  # noqa: BLE001
                pass
        if self._autofill_log_finish_after_id is not None:
            try:
                self.after_cancel(self._autofill_log_finish_after_id)
            except Exception:  # noqa: BLE001
                pass
            self._autofill_log_finish_after_id = None
        if auto_close_ms and auto_close_ms > 0:
            self._autofill_log_finish_after_id = self.after(
                auto_close_ms, self.close_autofill_log
            )

    def close_autofill_log(self):
        if self._autofill_log_finish_after_id is not None:
            try:
                self.after_cancel(self._autofill_log_finish_after_id)
            except Exception:  # noqa: BLE001
                pass
            self._autofill_log_finish_after_id = None
        frame = self._autofill_log_frame
        self._autofill_log_frame = None
        self._autofill_log_text = None
        self._autofill_log_status = None
        if frame is not None:
            try:
                frame.place_forget()
                frame.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._place_toast_host()

    def show_toast(
        self,
        message: str,
        *,
        title: str = "提醒",
        duration_ms: int = TOAST_DEFAULT_MS,
        action_text: str | None = "关闭",
        undo_text: str | None = None,
        on_undo=None,
        on_complete=None,
        complete_on_timeout: bool = True,
        style: str = "danger",
    ):
        """Show a top-right countdown toast; stacks below any already visible.

        Older toasts stay above; each new toast is appended at the bottom.
        Auto-closes when the countdown reaches 0 (runs on_complete if set).
        Primary action dismisses early and also runs on_complete.
        Optional grey undo button runs on_undo instead and skips on_complete.
        Pass action_text=None (and no undo_text) to hide action buttons.
        Set complete_on_timeout=False so expiry only dismisses (no on_complete).
        style: "danger" (red) or "success" (green).
        """
        while len(self._toasts) >= TOAST_STACK_MAX:
            self._dismiss_toast(self._toasts[0], run_complete=False)

        if style == "success":
            accent = SUCCESS_BTN_FG
            accent_hover = SUCCESS_BTN_HOVER
            accent_text = SUCCESS_BTN_TEXT
        else:
            accent = DANGER_BTN_FG
            accent_hover = DANGER_BTN_HOVER
            accent_text = DANGER_BTN_TEXT

        toast_w = self._toast_target_width()
        host = self._ensure_toast_host()
        toast = customtkinter.CTkFrame(
            host,
            width=toast_w,
            corner_radius=TOAST_RADIUS,
            fg_color=TOAST_BG,
            border_width=TOAST_BORDER_WIDTH,
            border_color=accent,
        )
        toast.grid_columnconfigure(0, weight=1)

        header = customtkinter.CTkFrame(toast, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 6))
        header.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            header,
            text=title,
            anchor="w",
            font=customtkinter.CTkFont(size=FONT_TITLE, weight="bold"),
            text_color=TOAST_TITLE_COLOR,
        ).grid(row=0, column=0, sticky="ew")

        duration_ms = max(int(duration_ms or 0), TOAST_MIN_MS)
        seconds = max(1, int(round(duration_ms / 1000)))
        countdown = customtkinter.CTkLabel(
            header,
            text=f"{seconds}s",
            anchor="e",
            font=customtkinter.CTkFont(size=FONT_SECTION, weight="bold"),
            text_color=accent_hover,
        )
        countdown.grid(row=0, column=1, sticky="e", padx=(8, 0))

        customtkinter.CTkLabel(
            toast,
            text=message,
            anchor="w",
            justify="left",
            wraplength=max(120, toast_w - 36),
            font=customtkinter.CTkFont(size=FONT_ENTRY),
            text_color=TOAST_MESSAGE_COLOR,
        ).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 12))

        show_actions = bool(undo_text) or action_text is not None
        progress = customtkinter.CTkProgressBar(
            toast,
            height=10,
            fg_color=TOAST_PROGRESS_TRACK,
            progress_color=accent,
        )
        progress.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=18,
            pady=(0, 16 if not show_actions else 12),
        )
        progress.set(1.0)

        self._toast_seq += 1
        entry = {
            "id": self._toast_seq,
            "frame": toast,
            "progress": progress,
            "countdown": countdown,
            "duration_ms": duration_ms,
            "deadline_ms": int(self.winfo_toplevel().tk.call("clock", "milliseconds"))
            + duration_ms,
            "on_complete": on_complete,
            "on_undo": on_undo,
            "complete_on_timeout": bool(complete_on_timeout),
            "settled": False,
        }

        if show_actions:
            btn_row = customtkinter.CTkFrame(toast, fg_color="transparent")
            btn_row.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 16))
            if undo_text:
                btn_row.grid_columnconfigure(0, weight=1)
                btn_row.grid_columnconfigure(1, weight=1)
                customtkinter.CTkButton(
                    btn_row,
                    corner_radius=UI_RADIUS,
                    text=undo_text,
                    height=TOAST_BTN_HEIGHT,
                    font=self._button_font(FONT_SECTION),
                    fg_color=SECONDARY_BTN_FG,
                    hover_color=SECONDARY_BTN_HOVER,
                    text_color=SECONDARY_BTN_TEXT,
                    command=lambda e=entry: self._toast_undo(e),
                ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
                customtkinter.CTkButton(
                    btn_row,
                    corner_radius=UI_RADIUS,
                    text=action_text or "关闭",
                    height=TOAST_BTN_HEIGHT,
                    font=self._button_font(FONT_SECTION),
                    fg_color=accent,
                    hover_color=accent_hover,
                    text_color=accent_text,
                    command=lambda e=entry: self._dismiss_toast(e, run_complete=True),
                ).grid(row=0, column=1, sticky="ew", padx=(6, 0))
            else:
                btn_row.grid_columnconfigure(0, weight=1)
                customtkinter.CTkButton(
                    btn_row,
                    corner_radius=UI_RADIUS,
                    text=action_text,
                    height=TOAST_BTN_HEIGHT,
                    font=self._button_font(FONT_SECTION),
                    fg_color=accent,
                    hover_color=accent_hover,
                    text_color=accent_text,
                    command=lambda e=entry: self._dismiss_toast(e, run_complete=True),
                ).grid(row=0, column=0, sticky="ew")

        # Size from content before packing into the stack host.
        toast.update_idletasks()
        scale = self._widget_scaling_factor()
        toast_h = max(1, int(round(toast.winfo_reqheight() / scale)))
        toast.configure(width=toast_w, height=toast_h)
        toast.grid_propagate(False)

        self._toasts.append(entry)
        self._relayout_toast_stack()
        self._schedule_toast_tick()

    def show_success_toast(self, message: str, *, title: str = "OCR提取"):
        self.show_toast(
            message,
            title=title,
            duration_ms=TOAST_SUCCESS_MS,
            action_text="关闭",
            style="success",
        )

    def _schedule_toast_tick(self):
        if self._toast_tick_after_id is not None:
            return
        if not self._toasts:
            return
        self._toast_tick_after_id = self.after(TOAST_TICK_MS, self._toast_tick)

    def _toast_tick(self):
        self._toast_tick_after_id = None
        if not self._toasts:
            return
        now = int(self.winfo_toplevel().tk.call("clock", "milliseconds"))
        expired = []
        for entry in self._toasts:
            remaining = max(0, entry["deadline_ms"] - now)
            duration = entry["duration_ms"] or 1
            ratio = remaining / duration
            try:
                entry["progress"].set(ratio)
            except Exception:  # noqa: BLE001
                pass
            try:
                secs = int((remaining + 999) // 1000)
                entry["countdown"].configure(text=f"{secs}s")
            except Exception:  # noqa: BLE001
                pass
            if remaining <= 0:
                expired.append(entry)
        for entry in expired:
            self._dismiss_toast(
                entry, run_complete=bool(entry.get("complete_on_timeout", True))
            )
        if self._toasts:
            self._toast_tick_after_id = self.after(TOAST_TICK_MS, self._toast_tick)

    def _toast_undo(self, entry: dict):
        if entry.get("settled"):
            return
        entry["settled"] = True
        cb = entry.get("on_undo")
        entry["on_complete"] = None
        entry["on_undo"] = None
        self._dismiss_toast(entry, run_complete=False)
        if cb is not None:
            cb()

    def _dismiss_toast(self, entry: dict, *, run_complete: bool = False):
        if entry not in self._toasts:
            return
        self._toasts.remove(entry)
        frame = entry.get("frame")
        if frame is not None:
            try:
                frame.pack_forget()
                frame.destroy()
            except Exception:  # noqa: BLE001
                pass

        complete_cb = None
        if run_complete and not entry.get("settled"):
            entry["settled"] = True
            complete_cb = entry.get("on_complete")
        entry["on_complete"] = None
        entry["on_undo"] = None

        if self._toasts:
            self._relayout_toast_stack()
        elif self._toast_host is not None:
            try:
                self._toast_host.place_forget()
                self._toast_host.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._toast_host = None

        if complete_cb is not None:
            complete_cb()

    def hide_toast(self, *, run_complete: bool = False):
        """Dismiss all stacked toasts."""
        if self._toast_tick_after_id is not None:
            try:
                self.after_cancel(self._toast_tick_after_id)
            except Exception:  # noqa: BLE001
                pass
            self._toast_tick_after_id = None
        for entry in list(self._toasts):
            self._dismiss_toast(entry, run_complete=run_complete)

    def _apply_min_window_size(self):
        scale = self._widget_scaling_factor()
        min_h = (
            WINDOW_MIN_HEIGHT_ZOOMED
            if self._ui_zoomed
            else WINDOW_MIN_HEIGHT_NORMAL
        )
        self.minsize(
            int(WINDOW_MIN_WIDTH * scale),
            int(min_h * scale),
        )

    def _build_cert_nav_row(self, parent, row: int) -> customtkinter.CTkLabel:
        nav_row = customtkinter.CTkFrame(parent, fg_color="transparent")
        nav_row.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        nav_row.grid_columnconfigure(1, weight=1)

        customtkinter.CTkButton(
            nav_row,
            corner_radius=UI_RADIUS,
            text="上一份",
            width=72,
            height=SMALL_BTN_HEIGHT,
            fg_color=PRIMARY_BTN_FG,
            hover_color=PRIMARY_BTN_HOVER,
            text_color=PRIMARY_BTN_TEXT,
            font=self._button_font(FONT_BUTTON),
            command=self._on_prev_certificate,
        ).grid(row=0, column=0, padx=(0, 6))

        nav_label = customtkinter.CTkLabel(
            nav_row,
            text="0/0",
            font=customtkinter.CTkFont(size=FONT_LABEL),
        )
        nav_label.grid(row=0, column=1)

        customtkinter.CTkButton(
            nav_row,
            corner_radius=UI_RADIUS,
            text="下一份",
            width=72,
            height=SMALL_BTN_HEIGHT,
            fg_color=PRIMARY_BTN_FG,
            hover_color=PRIMARY_BTN_HOVER,
            text_color=PRIMARY_BTN_TEXT,
            font=self._button_font(FONT_BUTTON),
            command=self._on_next_certificate,
        ).grid(row=0, column=2, padx=(6, 0))

        return nav_label

    # ---------------------------------------------------------------- extract
    def _build_extract_controls(self, parent: customtkinter.CTkFrame):
        # No header row (unlike review). Building without one avoids the CTk
        # grid_remove weight bug that left fields centered in the wrong band.
        _header, content, footer = self._make_pinned_footer_layout(
            parent, with_header=False
        )

        # Parsed fields — match keys for webpage verification + autofill targets
        match_header_label = customtkinter.CTkLabel(
            content,
            text="比对字段",
            font=customtkinter.CTkFont(size=FONT_SECTION, weight="bold"),
            anchor="w",
        )
        match_header_label.grid(row=0, column=0, sticky="ew", pady=(0, 2))

        self.extract_match_frame = customtkinter.CTkFrame(content, fg_color="transparent")
        self.extract_match_frame.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        self.extract_match_frame.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            content,
            text="填写字段",
            font=customtkinter.CTkFont(size=FONT_SECTION, weight="bold"),
            anchor="w",
        ).grid(row=2, column=0, sticky="ew", pady=(0, 6))

        self.extract_autofill_frame = customtkinter.CTkFrame(content, fg_color="transparent")
        self.extract_autofill_frame.grid(row=3, column=0, sticky="ew", pady=(0, 14))
        self.extract_autofill_frame.grid_columnconfigure(0, weight=1)

        self.extract_field_entries: dict[str, customtkinter.CTkEntry] = {}
        for frame, field_defs in (
            (self.extract_match_frame, MATCH_FIELDS),
            (self.extract_autofill_frame, METROLOGY_FIELDS),
        ):
            for row, (key, label) in enumerate(field_defs):
                customtkinter.CTkLabel(
                    frame,
                    text=label,
                    anchor="w",
                    width=96,
                    font=customtkinter.CTkFont(size=FONT_LABEL),
                    text_color="gray60",
                ).grid(row=row, column=0, sticky="w", pady=4)

                entry = self._make_field_entry(frame, placeholder=f"请输入{label}")
                entry.grid(row=row, column=0, sticky="ew", padx=(104, 0), pady=4)
                self.extract_field_entries[key] = entry

        self.extract_errors_label = self._track_content_wrap(
            customtkinter.CTkLabel(
                content,
                text="",
                anchor="w",
                justify="left",
                wraplength=CONTENT_WRAP,
                font=customtkinter.CTkFont(size=FONT_META),
                text_color="#c0392b",
            )
        )
        self.extract_errors_label.grid(row=4, column=0, sticky="ew", pady=(0, 6))

        self.ocr_progress = customtkinter.CTkProgressBar(footer, height=10)
        self.ocr_progress.set(0)

        self.ocr_progress_label = customtkinter.CTkLabel(
            footer,
            text="OCR 进度 0/0",
            font=customtkinter.CTkFont(size=FONT_META),
            text_color="gray60",
            anchor="w",
        )
        # Defer gridding to _update_extract_ocr_ui so a disabled OCR preference
        # never flashes the progress row on fresh launch.

        actions = customtkinter.CTkFrame(footer, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew")
        actions.grid_columnconfigure(0, weight=1)

        self.ocr_extract_button = customtkinter.CTkButton(
            actions,
            corner_radius=UI_RADIUS,
            text="OCR提取",
            height=PRIMARY_ACTION_BTN_HEIGHT,
            round_height_to_even_numbers=False,
            font=self._button_font(FONT_SECTION),
            fg_color=SUCCESS_BTN_FG,
            hover_color=SUCCESS_BTN_HOVER,
            text_color=SUCCESS_BTN_TEXT,
            command=self._on_ocr_extract,
        )
        self.ocr_extract_button.grid(row=0, column=0, sticky="ew")
        self._style_primary_action_button(self.ocr_extract_button)

        self._update_extract_ocr_ui()

    def _schedule_doc_list_scrollbar_sync(self, _event=None):
        if getattr(self, "_doc_list_scroll_after", None) is not None:
            try:
                self.after_cancel(self._doc_list_scroll_after)
            except Exception:  # noqa: BLE001
                pass
        self._doc_list_scroll_after = self.after(20, self._update_doc_list_scrollbar)

    def _bind_scrollable_mousewheel(self, scrollable, widget):
        """Make trackpad / mouse-wheel scroll work over content, not only the bar."""
        handler = lambda e, sf=scrollable: self._on_scrollable_mousewheel(sf, e)
        if sys.platform == "darwin":
            widget.bind("<MouseWheel>", handler, add="+")
        elif sys.platform.startswith("win"):
            widget.bind("<MouseWheel>", handler, add="+")
        else:
            widget.bind("<Button-4>", handler, add="+")
            widget.bind("<Button-5>", handler, add="+")
        for child in widget.winfo_children():
            self._bind_scrollable_mousewheel(scrollable, child)

    def _bind_doc_list_mousewheel(self, widget):
        if hasattr(self, "doc_list_frame"):
            self._bind_scrollable_mousewheel(self.doc_list_frame, widget)

    def _mousewheel_scroll_steps(self, event) -> int:
        """Canvas yview units for one wheel/trackpad event (tuned for usable speed)."""
        # Default Tk/CTk is ~1 unit per notch — feels like a crawl on long lists.
        speed = 5
        if sys.platform == "darwin":
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:
                return 0
            # Trackpad often sends ±1; multiply. Larger deltas (mouse) scale less.
            if abs(delta) <= 1:
                return -delta * speed
            return int(-delta * max(2, speed // 2))
        if sys.platform.startswith("win"):
            # Windows path uses fraction-of-view scrolling in _on_scrollable_mousewheel.
            delta = int(getattr(event, "delta", 0) or 0)
            notches = int(delta / 120) if delta else 0
            if notches == 0 and delta:
                notches = 1 if delta > 0 else -1
            return -notches
        return (-speed) if getattr(event, "num", 0) == 4 else speed

    def _on_scrollable_mousewheel(self, scrollable, event):
        if (
            self._autofill_busy
            and hasattr(self, "doc_list_frame")
            and scrollable is self.doc_list_frame
        ):
            return "break"
        if (
            hasattr(self, "doc_list_frame")
            and scrollable is self.doc_list_frame
            and self._doc_name_tip is not None
        ):
            # Tip overlays the list and would block / drift while rows move.
            self._hide_doc_name_tip()
        try:
            canvas = scrollable._parent_canvas
        except Exception:  # noqa: BLE001
            return
        if canvas.yview() == (0.0, 1.0):
            return
        if sys.platform.startswith("win"):
            # Unit scrolling is tiny on Win32/CTk; move ~half the visible page per notch.
            delta = int(getattr(event, "delta", 0) or 0)
            notches = int(delta / 120) if delta else 0
            if notches == 0 and delta:
                notches = 1 if delta > 0 else -1
            if notches:
                first, last = canvas.yview()
                view = max(float(last) - float(first), 0.08)
                canvas.yview_moveto(
                    max(0.0, min(1.0, float(first) - notches * view * 0.55))
                )
            return "break"
        steps = self._mousewheel_scroll_steps(event)
        if steps:
            canvas.yview_scroll(steps, "units")
        return "break"

    def _on_doc_list_mousewheel(self, event):
        if not hasattr(self, "doc_list_frame"):
            return
        # Overlay would otherwise stay pinned while rows move under it.
        if self._doc_name_tip is not None:
            self._hide_doc_name_tip()
        return self._on_scrollable_mousewheel(self.doc_list_frame, event)

    def _update_scrollable_scrollbar(
        self, scrollable, *, after_attr: str, vcenter_when_fits: bool = True
    ):
        """Doc-list scroll sync (auto-hide bar; optional vertical centering)."""
        setattr(self, after_attr, None)
        if scrollable is None:
            return
        canvas = scrollable._parent_canvas
        scrollbar = scrollable._scrollbar
        scrollable.update_idletasks()
        bbox = canvas.bbox("all")
        window_id = getattr(scrollable, "_create_window_id", None)
        if bbox is None:
            canvas.configure(scrollregion=(0, 0, 0, 0))
            scrollbar.grid_remove()
            if window_id is not None:
                canvas.coords(window_id, 0, 0)
            return

        _x1, y1, x2, y2 = bbox
        content_height = max(0, y2 - y1)
        canvas_height = max(canvas.winfo_height(), 1)

        if content_height > canvas_height + 1:
            if window_id is not None:
                canvas.coords(window_id, 0, 0)
            canvas.configure(scrollregion=(0, 0, max(0, x2), max(y2, content_height)))
            scrollable._create_grid()
            top, _bottom = canvas.yview()
            if top < 0 or top > 1:
                canvas.yview_moveto(0)
        else:
            scrollbar.grid_remove()
            y_off = (
                max(0, (canvas_height - content_height) // 2) if vcenter_when_fits else 0
            )
            if window_id is not None:
                canvas.coords(window_id, 0, y_off)
            canvas.configure(
                scrollregion=(0, 0, max(0, x2), max(canvas_height, y_off + content_height))
            )
            canvas.yview_moveto(0)

    def _update_doc_list_scrollbar(self, _event=None):
        if not hasattr(self, "doc_list_frame"):
            self._doc_list_scroll_after = None
            return
        self._update_scrollable_scrollbar(
            self.doc_list_frame,
            after_attr="_doc_list_scroll_after",
            vcenter_when_fits=False,
        )

    def _pick_folder(self):
        path = filedialog.askdirectory(parent=self, title="选择证书文件夹")
        if not path:
            self.set_status("未选择文件夹")
            return
        self.show_step("extract")
        self._reset_workflow_to_extract()
        self._load_folder(path)

    def _clear_extract(self):
        """Ask for confirmation before wiping the document list."""
        if self._autofill_busy:
            self.set_status("自动填写进行中，请先退出后再清空…")
            return
        if self._extract_busy:
            self.set_status("正在处理中，请稍候…")
            return
        n = len(self._imported_files)
        if n == 0 and not self._source_folder:
            self.set_status("列表已空")
            return
        self.show_toast(
            f"将清空文档列表中的 {n} 份证书及解析结果。\n此操作不可撤销。",
            title="清空文档列表",
            action_text="确认清空",
            undo_text="取消",
            on_complete=self._confirm_clear_extract,
            complete_on_timeout=False,
            duration_ms=TOAST_DEFAULT_MS,
            style="danger",
        )
        self.set_status("确认清空文档列表？")

    def _confirm_clear_extract(self):
        self.show_step("extract")
        self._extract_busy = False
        self._source_folder = None
        self._imported_files.clear()
        self._parse_results.clear()
        self._autofill_queue.clear()
        self._removed_paths.clear()
        self._current_cert_index = 0
        self._selected_path = None
        self._rebuild_doc_list()
        self.folder_label.configure(text="未选择文件夹")
        self._clear_extract_fields_display()
        self._reset_ocr_progress()
        self._update_cert_nav_labels()
        if hasattr(self, "field_entries"):
            self._clear_approve_fields()
        self._update_autofill_button()
        self._sync_pdf_preview()
        self.set_status("已清空提取列表")
        self.show_success_toast("已清空文档列表。", title="清空")
        self._reset_workflow_to_extract()

    def _load_folder(self, folder: str):
        if self._extract_busy:
            self.set_status("正在处理中，请稍候…")
            return

        self._reset_workflow_to_extract()
        self.folder_label.configure(text=folder)
        self.set_status("正在扫描文件夹…")
        self.update_idletasks()

        try:
            pdfs = find_pdfs_in_folder(folder, recursive=False)
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"文件夹加载失败：{exc}")
            return

        paths = [str(p) for p in pdfs]
        self._source_folder = folder
        self._imported_files = paths
        self._parse_results.clear()
        self._autofill_queue.clear()
        self._removed_paths.clear()
        self._current_cert_index = 0
        self._rebuild_doc_list()
        self._update_cert_nav_labels()

        if not paths:
            self._selected_path = None
            self._sync_pdf_preview()
            self.set_status("文件夹中没有 PDF")
            return

        # Embedded-text parse only — OCR is manual via 「OCR提取」.
        self._extract_busy = True
        results: dict[str, ParseResult] = {}
        try:
            for i, path in enumerate(paths, start=1):
                self.set_status(f"解析进度 {i}/{len(paths)}")
                self.update_idletasks()
                results[path] = parse_certificate(path, use_ocr_fallback=False, force_ocr=False)
            self._on_folder_loaded(folder, paths, results)
        except Exception as exc:  # noqa: BLE001
            self._on_folder_fail(str(exc))
        finally:
            self._extract_busy = False

    def _on_folder_fail(self, message: str):
        self._extract_busy = False
        self.set_status(f"文件夹加载失败：{message}")

    def _on_folder_loaded(
        self,
        folder: str,
        paths: list[str],
        results: dict[str, ParseResult],
    ):
        self._extract_busy = False
        self._source_folder = folder
        self._imported_files = paths
        self._parse_results = results
        self._autofill_queue.clear()
        self._removed_paths.clear()
        self._sort_imported_files()
        self._current_cert_index = 0
        ok = sum(1 for r in results.values() if r.ok)
        pending = sum(1 for p in self._imported_files if self._cert_needs_ocr(p))
        self._rebuild_doc_list()
        self._update_cert_nav_labels()
        self._reset_ocr_progress()
        if self._imported_files:
            self._select_document(self._imported_files[0])
        if pending:
            if self._ocr_enabled:
                self.set_status(f"文件夹加载完成：已解析 {ok} · 待 OCR {pending}")
            else:
                self.set_status(f"文件夹加载完成：已解析 {ok} · 未解析 {pending}")
        else:
            self.set_status(f"文件夹加载完成：{len(paths)} PDF")

    def _cert_is_parsed(self, path: str) -> bool:
        result = self._parse_results.get(path)
        return bool(result and result.ok)

    def _cert_needs_ocr(self, path: str) -> bool:
        """True when embedded-text parse left the cert incomplete / empty."""
        return not self._cert_is_parsed(path)

    def _sort_imported_files(self):
        """Parsed certificates first, then unparsed / pending OCR."""
        parsed = [p for p in self._imported_files if self._cert_is_parsed(p)]
        pending = [p for p in self._imported_files if not self._cert_is_parsed(p)]
        self._imported_files = parsed + pending
        if self._selected_path in self._imported_files:
            self._current_cert_index = self._imported_files.index(self._selected_path)
        elif self._imported_files:
            self._current_cert_index = 0
            self._selected_path = self._imported_files[0]
        else:
            self._current_cert_index = 0
            self._selected_path = None

    def _reset_ocr_progress(self):
        if hasattr(self, "ocr_progress"):
            self.ocr_progress.set(0)
        if hasattr(self, "ocr_progress_label"):
            self.ocr_progress_label.configure(text="OCR 进度 0/0")

    def _set_ocr_progress(self, current: int, total: int, name: str = ""):
        total = max(total, 1)
        if hasattr(self, "ocr_progress"):
            self.ocr_progress.set(current / total)
        label = f"OCR 进度 {current}/{total}"
        if name:
            label += f" · {name}"
        if hasattr(self, "ocr_progress_label"):
            self.ocr_progress_label.configure(text=label)

    def _unique_failed_path(self, src: Path) -> Path:
        out_dir = self._failed_items_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / src.name
        if not dest.exists():
            return dest
        stem, suffix = src.stem, src.suffix
        n = 1
        while True:
            candidate = out_dir / f"{stem}_{n}{suffix}"
            if not candidate.exists():
                return candidate
            n += 1

    def _quarantine_failed_paths(self, paths: list[str]) -> int:
        """Copy failed PDFs to the configured failed-items folder and remove them from the queue."""
        moved = 0
        for path in list(paths):
            src = Path(path)
            try:
                dest = self._unique_failed_path(src)
                shutil.copy2(src, dest)
                moved += 1
            except Exception as exc:  # noqa: BLE001
                self.set_status(f"无法保存失败项 {src.name}：{exc}")
                continue

            if path in self._imported_files:
                self._imported_files.remove(path)
            self._parse_results.pop(path, None)
            if path in self._autofill_queue:
                self._autofill_queue.remove(path)
            self._removed_paths.discard(path)
            if self._selected_path == path:
                self._selected_path = None

        self._sort_imported_files()
        return moved

    def _on_ocr_extract(self):
        """Manually run PaddleOCR on certificates that still need it."""
        if not self._ocr_enabled:
            self._on_remove_failed_certificates()
            return
        if self._extract_busy or self._autofill_busy:
            self.set_status("正在处理中，请稍候…")
            return
        if not self._imported_files:
            self.set_status("请先选择文件夹")
            return

        targets = [p for p in self._imported_files if self._cert_needs_ocr(p)]
        if not targets:
            self.set_status("全部证书已有文本解析结果，无需 OCR")
            self.show_success_toast("没有需要 OCR 的证书。")
            return

        self._extract_busy = True
        if hasattr(self, "ocr_extract_button"):
            self.ocr_extract_button.configure(state="disabled")
        self._set_ocr_progress(0, len(targets))
        self.set_status(f"OCR提取开始：{len(targets)} 份")

        def worker():
            done = 0
            failed_paths: list[str] = []
            try:
                for i, path in enumerate(targets, start=1):
                    name = Path(path).name
                    self.after(
                        0,
                        lambda i=i, name=name, n=len(targets): self._set_ocr_progress(i, n, name),
                    )
                    try:
                        result = parse_certificate(
                            path,
                            use_ocr_fallback=False,
                            force_ocr=True,
                        )
                        self._parse_results[path] = result
                        if result.ok:
                            done += 1
                        else:
                            failed_paths.append(path)
                    except Exception:  # noqa: BLE001
                        failed_paths.append(path)
                self.after(
                    0,
                    lambda: self._on_ocr_extract_done(done, failed_paths, len(targets)),
                )
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: self._on_ocr_extract_fail(str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_remove_failed_certificates(self):
        """When OCR is off: quarantine unparsed / failed certificates."""
        if self._extract_busy or self._autofill_busy:
            self.set_status("正在处理中，请稍候…")
            return
        if not self._imported_files:
            self.set_status("请先选择文件夹")
            return

        targets = [p for p in self._imported_files if self._cert_needs_ocr(p)]
        if not targets:
            self.set_status("没有未解析或失败的证书 · 进入核对填写")
            self.show_success_toast("没有需要移出的证书。", title="移出失败证书")
            self._advance_to_review()
            return

        self._pending_quarantine_paths = list(targets)
        msg = f"即将移出 {len(targets)} 份未解析/失败证书"
        self.set_status(msg)
        self.show_toast(
            f"{msg}\n倒计时结束后移至 {self._failed_items_dir_short()}/，可点撤销保留。",
            title="移出失败证书",
            action_text="立即移出",
            undo_text="撤销",
            on_undo=self._undo_pending_quarantine,
            on_complete=self._commit_pending_quarantine,
        )

    def _on_ocr_extract_done(self, done: int, failed_paths: list[str], total: int):
        self._extract_busy = False
        if hasattr(self, "ocr_extract_button"):
            self.ocr_extract_button.configure(state="normal")

        self._sort_imported_files()
        self._rebuild_doc_list()
        self._update_cert_nav_labels()
        self._update_autofill_button()
        if self._imported_files:
            select = self._selected_path if self._selected_path in self._imported_files else self._imported_files[0]
            self._select_document(select)
        else:
            self._selected_path = None
            self._clear_extract_fields_display()
            self._sync_pdf_preview()

        if hasattr(self, "ocr_progress"):
            self.ocr_progress.set(1 if total else 0)
        if hasattr(self, "ocr_progress_label"):
            self.ocr_progress_label.configure(text=f"OCR 进度 {total}/{total}")

        msg = f"OCR 完成 · 成功 {done}/{total}"
        if failed_paths:
            pending = list(failed_paths)
            self._pending_quarantine_paths = pending
            msg += f" · 失败 {len(pending)} 份将移出队列"
            self.set_status(msg)
            self.show_toast(
                f"{msg}\n倒计时结束后移至 {self._failed_items_dir_short()}/，可点撤销保留。",
                title="OCR提取",
                action_text="立即移出",
                undo_text="撤销",
                on_undo=self._undo_pending_quarantine,
                on_complete=self._commit_pending_quarantine,
            )
            return

        self.set_status(msg)
        self.show_success_toast(msg)
        self._advance_to_review()

    def _undo_pending_quarantine(self):
        count = len(self._pending_quarantine_paths)
        self._pending_quarantine_paths = []
        msg = f"已撤销移出 · 失败 {count} 份仍留在队列"
        self.set_status(msg)

    def _cancel_pending_quarantine(self):
        """Drop a pending fail-folder countdown without copying files."""
        if self._pending_quarantine_paths:
            self._pending_quarantine_paths = []
        # Dismiss only the toast(s) that would quarantine on complete.
        for entry in list(self._toasts):
            if entry.get("on_complete") is self._commit_pending_quarantine:
                self._dismiss_toast(entry, run_complete=False)

    def _commit_pending_quarantine(self):
        paths = list(self._pending_quarantine_paths)
        self._pending_quarantine_paths = []
        if not paths:
            self.show_success_toast("没有需要移出的证书。")
            self._advance_to_review()
            return
        moved = self._quarantine_failed_paths(paths)
        self._sort_imported_files()
        self._rebuild_doc_list()
        self._update_cert_nav_labels()
        self._update_autofill_button()
        if self._imported_files:
            select = self._selected_path if self._selected_path in self._imported_files else self._imported_files[0]
            self._select_document(select)
        else:
            self._selected_path = None
            self._clear_extract_fields_display()
            self._sync_pdf_preview()
        msg = f"失败 {moved} 份已移出队列 · 已复制到 {self._failed_items_dir_short()}/"
        self.set_status(msg)
        self.show_success_toast(msg)
        self._advance_to_review()

    def _on_ocr_extract_fail(self, message: str):
        self._extract_busy = False
        if hasattr(self, "ocr_extract_button"):
            self.ocr_extract_button.configure(state="normal")
        self._reset_ocr_progress()
        self.set_status(f"OCR 失败：{message}")
        self.show_toast(f"OCR 失败：{message}", title="OCR提取")

    def _doc_status_kind(self, path: str) -> str:
        """Return 'bad', 'ok', or '' for document list status indicators."""
        if path in self._removed_paths:
            return "bad"
        if path in self._autofill_queue:
            return "ok"
        return ""

    def _doc_status_mark(self, path: str) -> str:
        kind = self._doc_status_kind(path)
        if not kind:
            return ""
        if self._status_dots:
            return DOC_STATUS_DOT
        return "❌" if kind == "bad" else "✅"

    def _doc_leading_label(self, path: str, index: int) -> str:
        """Left column: status emoji overrides the index number when present."""
        mark = self._doc_status_mark(path)
        return mark if mark else str(index + 1)

    def _doc_name_label(self, path: str, index: int) -> str:
        return Path(path).name

    def _appearance_color(self, color) -> str:
        """Resolve a CTk (light, dark) color tuple for the current mode."""
        if isinstance(color, (tuple, list)) and len(color) >= 2:
            mode = customtkinter.get_appearance_mode()
            return color[1] if str(mode).lower() == "dark" else color[0]
        return str(color)

    def _doc_row_surface_bg(self, surface) -> str:
        """Solid bg for canvas cells — never leave system default (black/white flash)."""
        if surface is None or surface == "transparent":
            return self._appearance_color(EMBED_BG_COLOR)
        return self._appearance_color(surface)

    def _doc_list_font_scale(self) -> float:
        """Scale factor for doc-list canvas text (1.0 = pre-zoom-font-fix sizes)."""
        if self._doc_list_scale_fonts:
            return self._widget_scaling_factor()
        return 1.0

    def _doc_list_ctk_font_size(self, size: int) -> int:
        """CTk design-unit size so rendered text matches doc-list canvas fonts.

        CTk multiplies font size by widget scaling; doc-list canvas text applies
        scaling only when the list-text switch is on.
        """
        if self._doc_list_scale_fonts:
            return max(1, int(size))
        scale = self._widget_scaling_factor()
        return max(1, int(round(size / scale)))

    def _doc_tk_font(self, size: int) -> tkfont.Font:
        """Tk font for doc-list cells; optionally follows CTk UI zoom."""
        scale = self._doc_list_font_scale()
        scaled = max(1, int(round(size * scale)))
        return tkfont.Font(family="SF Pro Text", size=scaled)

    def _make_doc_text_canvas(
        self,
        parent,
        *,
        text: str,
        fill,
        size: int,
        width: int | None = None,
        anchor: str = "w",
    ) -> tuple[tk.Canvas, int]:
        """Plain Canvas text cell — no tk.Label, so macOS long-press has nothing to drag."""
        scale = self._doc_list_font_scale()
        # Leave a few px so the parent CTkFrame rounded corners stay visible.
        h = max(18, int(round((DOC_ROW_HEIGHT - 8) * scale)))
        canvas = tk.Canvas(
            parent,
            height=h,
            highlightthickness=0,
            bd=0,
            relief="flat",
            cursor="hand2",
            bg=self._doc_row_surface_bg("transparent"),
        )
        if width is not None:
            canvas.configure(width=max(1, int(round(width * scale))))
        fill_color = self._appearance_color(fill)
        font = self._doc_tk_font(size)
        pad_x = int(round(10 * scale)) if anchor != "center" else 0
        x = (int(round(width * scale)) // 2) if (width is not None and anchor == "center") else pad_x
        text_id = canvas.create_text(
            x,
            h // 2,
            text=text,
            fill=fill_color,
            font=font,
            anchor="center" if anchor == "center" else "w",
        )
        canvas._vincert_text_id = text_id
        canvas._vincert_anchor = anchor
        canvas._vincert_font = font
        canvas._vincert_design_size = size
        canvas._vincert_pad_x = pad_x

        def _on_configure(event, c=canvas, tid=text_id, anc=anchor):
            if event.width <= 1 or event.height <= 1:
                return
            if anc == "center":
                c.coords(tid, event.width / 2, event.height / 2)
            else:
                c.coords(tid, getattr(c, "_vincert_pad_x", 10), event.height / 2)

        canvas.bind("<Configure>", _on_configure, add="+")
        return canvas, text_id

    def _set_doc_canvas_text(
        self,
        canvas: tk.Canvas,
        text: str,
        *,
        fill,
        size: int | None = None,
    ) -> None:
        text_id = getattr(canvas, "_vincert_text_id", None)
        if text_id is None:
            return
        fill_color = self._appearance_color(fill)
        kwargs = {"text": text, "fill": fill_color}
        if size is not None:
            canvas._vincert_design_size = size
            font = self._doc_tk_font(size)
            canvas._vincert_font = font
            kwargs["font"] = font
        canvas.itemconfigure(text_id, **kwargs)

    def _refresh_doc_list_fonts(self):
        """Re-apply fonts/heights after UI zoom or list-text-scale toggle."""
        if not self._doc_rows:
            return
        scale = self._doc_list_font_scale()
        h = max(18, int(round((DOC_ROW_HEIGHT - 8) * scale)))
        pad_x = int(round(10 * scale))
        mark_w = max(1, int(round(DOC_MARK_COL_WIDTH * scale)))
        for path, row in self._doc_rows.items():
            mark = row.get("mark")
            name = row.get("name")
            if mark is not None:
                try:
                    mark.configure(height=h, width=mark_w)
                    mark._vincert_pad_x = 0
                except Exception:  # noqa: BLE001
                    pass
            if name is not None:
                try:
                    name.configure(height=h)
                    name._vincert_pad_x = pad_x
                except Exception:  # noqa: BLE001
                    pass
        self._highlight_selected_doc()
        self._schedule_doc_list_scrollbar_sync()
        self._refresh_doc_name_tip_font()

    def _refresh_doc_name_tip_font(self):
        """Rebuild an open name hover tip so it follows the list text-size switch."""
        path = self._doc_name_tip_path
        if path is None or self._doc_name_tip is None:
            return
        self._hide_doc_name_tip()
        self._show_doc_name_tip(path)

    def _set_doc_canvas_surface(self, canvas: tk.Canvas, surface) -> None:
        try:
            canvas.configure(bg=self._doc_row_surface_bg(surface))
        except Exception:  # noqa: BLE001
            pass

    def _is_doc_name_truncated(self, row: dict) -> bool:
        """True when the visible name canvas width crops the full filename."""
        try:
            canvas = row["name"]
            canvas.update_idletasks()
            visible = int(canvas.winfo_width())
            if visible <= 1:
                return False
            font = getattr(canvas, "_vincert_font", None)
            text = row.get("full_name") or ""
            if font is not None:
                needed = int(font.measure(text))
            else:
                bbox = canvas.bbox(row["name_id"])
                needed = int(bbox[2] - bbox[0]) if bbox else 0
            return needed > visible - 4
        except Exception:  # noqa: BLE001
            return False

    def _hide_doc_name_tip(self):
        tip = self._doc_name_tip
        self._doc_name_tip = None
        self._doc_name_tip_path = None
        if tip is None:
            return
        try:
            tip.place_forget()
            tip.destroy()
        except Exception:  # noqa: BLE001
            pass

    def _clear_doc_row_hovers(self, *, keep: str | None = None):
        """Clear sticky hover washes; optionally keep one path hovered."""
        for path, row in self._doc_rows.items():
            if path == keep or path == self._selected_path:
                continue
            try:
                row["frame"].configure(fg_color="transparent")
                self._sync_doc_row_surfaces(path, "transparent")
            except Exception:  # noqa: BLE001
                pass
        self._doc_hover_path = keep

    def _show_doc_name_tip(self, path: str):
        if self._autofill_busy:
            self._hide_doc_name_tip()
            return
        row = self._doc_rows.get(path)
        if row is None:
            self._hide_doc_name_tip()
            return
        name_canvas = row["name"]
        full_name = row.get("full_name") or self._doc_name_label(path, row.get("index", 0))
        if not self._is_doc_name_truncated(row):
            if self._doc_name_tip_path == path:
                self._hide_doc_name_tip()
            return
        if self._doc_name_tip_path == path and self._doc_name_tip is not None:
            return

        self._hide_doc_name_tip()
        selected = path == self._selected_path
        tip = customtkinter.CTkFrame(
            self,
            corner_radius=UI_RADIUS,
            fg_color=DOC_ROW_ACTIVE if selected else TOAST_BG,
            border_width=0 if selected else TOAST_BORDER_WIDTH,
            border_color=ACTIVE_OUTLINE,
        )
        label = customtkinter.CTkLabel(
            tip,
            text=full_name,
            anchor="w",
            justify="left",
            font=customtkinter.CTkFont(size=self._doc_list_ctk_font_size(FONT_BODY)),
            text_color=DOC_ROW_ACTIVE_TEXT if selected else TOAST_TITLE_COLOR,
            fg_color="transparent",
        )
        label.pack(padx=DOC_NAME_TIP_PADX, pady=DOC_NAME_TIP_PADY)

        tip.bind("<Leave>", lambda _e, p=path: self._on_doc_name_tip_leave(p))
        label.bind("<Leave>", lambda _e, p=path: self._on_doc_name_tip_leave(p))
        tip.bind("<ButtonPress-1>", lambda _e, p=path: self._on_doc_row_press(p))
        label.bind("<ButtonPress-1>", lambda _e, p=path: self._on_doc_row_press(p))
        # Tip sits above the list — forward wheel so scrolling still works.
        self._bind_doc_list_mousewheel(tip)
        self._bind_doc_list_mousewheel(label)

        tip.update_idletasks()
        scale = self._widget_scaling_factor()
        tip_w = max(1, int(round(tip.winfo_reqwidth() / scale)))
        tip_h = max(1, int(round(tip.winfo_reqheight() / scale)))
        tip.configure(width=tip_w, height=tip_h)

        root_x = self.winfo_rootx()
        root_y = self.winfo_rooty()
        x = int(round((name_canvas.winfo_rootx() - root_x) / scale))
        y = int(round((name_canvas.winfo_rooty() - root_y) / scale))
        win_w = max(1, int(round(self.winfo_width() / scale)))
        win_h = max(1, int(round(self.winfo_height() / scale)))
        x = max(DOC_SIDEBAR_MARGIN, min(x, win_w - tip_w - DOC_SIDEBAR_MARGIN))
        y = max(DOC_SIDEBAR_MARGIN, min(y, win_h - tip_h - DOC_SIDEBAR_MARGIN))

        tip.place(x=x, y=y, anchor="nw")
        tip.lift()
        self._doc_name_tip = tip
        self._doc_name_tip_path = path
        self._set_doc_row_hover(path, False)

    def _on_doc_name_tip_leave(self, path: str):
        row = self._doc_rows.get(path)
        if row is not None:
            frame = row["frame"]
            try:
                under = frame.winfo_containing(
                    frame.winfo_pointerx(), frame.winfo_pointery()
                )
            except Exception:  # noqa: BLE001
                under = None
            if under is not None and self._is_descendant(under, frame):
                return
        tip = self._doc_name_tip
        if tip is not None:
            try:
                under = tip.winfo_containing(tip.winfo_pointerx(), tip.winfo_pointery())
            except Exception:  # noqa: BLE001
                under = None
            if under is not None and self._is_descendant(under, tip):
                return
        if self._doc_name_tip_path == path:
            self._hide_doc_name_tip()
        self._clear_doc_row_hovers()

    def _sync_doc_row_surfaces(self, path: str, surface) -> None:
        row = self._doc_rows.get(path)
        if row is None:
            return
        for key in ("mark", "name"):
            canvas = row.get(key)
            if canvas is not None:
                self._set_doc_canvas_surface(canvas, surface)

    def _on_doc_row_press(self, path: str, _event=None):
        if self._autofill_busy:
            return "break"
        self._select_document(path)
        return "break"

    def _bind_doc_row_interactions(self, widget, path: str):
        widget.bind("<ButtonPress-1>", lambda _e, p=path: self._on_doc_row_press(p))
        widget.bind("<B1-Motion>", lambda _e: "break")
        widget.bind("<ButtonRelease-1>", lambda _e: "break")
        widget.bind("<Enter>", lambda _e, p=path: self._hover_doc_row(p, True))
        widget.bind("<Leave>", lambda e, p=path: self._on_doc_row_leave(e, p))
        try:
            widget.configure(cursor="hand2")
        except Exception:  # noqa: BLE001
            pass
        for child in widget.winfo_children():
            self._bind_doc_row_interactions(child, path)

    def _on_doc_row_leave(self, event, path: str):
        row = self._doc_rows.get(path)
        if row is None:
            return
        frame = row["frame"]
        under = frame.winfo_containing(event.x_root, event.y_root)
        if under is not None and self._is_descendant(under, frame):
            return
        tip = self._doc_name_tip
        if tip is not None and self._doc_name_tip_path == path:
            tip_under = tip.winfo_containing(event.x_root, event.y_root)
            if tip_under is not None and self._is_descendant(tip_under, tip):
                self._set_doc_row_hover(path, False)
                return
        self._hover_doc_row(path, False)

    def _set_doc_row_hover(self, path: str, hovering: bool):
        if self._autofill_busy or self._autofill_ui_chrome_locked:
            return
        if path == self._selected_path:
            if not hovering and self._doc_hover_path == path:
                self._doc_hover_path = None
            return
        row = self._doc_rows.get(path)
        if row is None:
            return
        if hovering:
            self._clear_doc_row_hovers(keep=path)
            row["frame"].configure(fg_color=TILE_BG_HOVER)
            self._sync_doc_row_surfaces(path, TILE_BG_HOVER)
            self._doc_hover_path = path
            return
        row["frame"].configure(fg_color="transparent")
        self._sync_doc_row_surfaces(path, "transparent")
        if self._doc_hover_path == path:
            self._doc_hover_path = None

    def _hover_doc_row(self, path: str, entering: bool):
        if self._autofill_busy:
            return
        if entering:
            if self._doc_name_tip_path and self._doc_name_tip_path != path:
                self._hide_doc_name_tip()
            self._clear_doc_row_hovers(keep=path)
            self._show_doc_name_tip(path)
            if self._doc_name_tip_path == path:
                self._set_doc_row_hover(path, False)
            else:
                self._set_doc_row_hover(path, True)
            return
        if self._doc_name_tip_path == path:
            self._hide_doc_name_tip()
        self._set_doc_row_hover(path, False)
        if self._doc_hover_path is None:
            self._clear_doc_row_hovers()

    def _doc_mark_text_color(self, path: str, *, selected: bool):
        """Numbers are muted (~50% opacity); status marks stay full strength."""
        kind = self._doc_status_kind(path)
        if kind and self._status_dots:
            return DOC_STATUS_DOT_OK if kind == "ok" else DOC_STATUS_DOT_BAD
        if kind:
            return DOC_ROW_ACTIVE_TEXT if selected else ("gray10", "gray90")
        return DOC_MARK_NUMBER_ACTIVE if selected else DOC_MARK_NUMBER_COLOR

    def _doc_mark_font_size(self, path: str) -> int:
        return DOC_STATUS_DOT_SIZE if (self._status_dots and self._doc_status_kind(path)) else FONT_BODY

    def _style_doc_row(self, path: str, *, selected: bool):
        row = self._doc_rows.get(path)
        if row is None:
            return
        mark_color = self._doc_mark_text_color(path, selected=selected)
        mark_size = self._doc_mark_font_size(path)
        mark_text = self._doc_leading_label(path, row.get("index", 0))
        name_text = row.get("full_name") or self._doc_name_label(path, row.get("index", 0))
        if self._autofill_busy or self._autofill_ui_chrome_locked:
            # Mute selection blue + keep rows non-interactive looking.
            surface = UI_LOCK_DOC_ROW if selected else "transparent"
            text = UI_LOCK_DOC_TEXT
            row["frame"].configure(fg_color=surface)
            self._set_doc_canvas_text(row["mark"], mark_text, fill=text, size=mark_size)
            self._set_doc_canvas_text(row["name"], name_text, fill=text, size=FONT_BODY)
            self._sync_doc_row_surfaces(path, surface)
            return
        if selected:
            row["frame"].configure(fg_color=DOC_ROW_ACTIVE)
            self._set_doc_canvas_text(row["mark"], mark_text, fill=mark_color, size=mark_size)
            self._set_doc_canvas_text(
                row["name"], name_text, fill=DOC_ROW_ACTIVE_TEXT, size=FONT_BODY
            )
            self._sync_doc_row_surfaces(path, DOC_ROW_ACTIVE)
        else:
            row["frame"].configure(fg_color="transparent")
            self._set_doc_canvas_text(row["mark"], mark_text, fill=mark_color, size=mark_size)
            self._set_doc_canvas_text(
                row["name"], name_text, fill=("gray10", "gray90"), size=FONT_BODY
            )
            self._sync_doc_row_surfaces(path, "transparent")

    def _rebuild_doc_list(self):
        self._hide_doc_name_tip()
        self._doc_hover_path = None
        for child in self.doc_list_frame.winfo_children():
            child.destroy()
        self._doc_rows.clear()
        # Drop any leftover scroll offset from the previous list.
        self.doc_list_frame._parent_canvas.yview_moveto(0)

        if not self._imported_files:
            self.doc_list_empty = customtkinter.CTkLabel(
                self.doc_list_frame,
                text="选择文件夹后，\nPDF 将显示在这里",
                text_color="gray50",
                justify="center",
            )
            self.doc_list_empty.grid(row=0, column=0, sticky="ew", pady=20)
            self._bind_doc_list_mousewheel(self.doc_list_empty)
            self._schedule_doc_list_scrollbar_sync()
            return

        parsed = [p for p in self._imported_files if self._cert_is_parsed(p)]
        pending = [p for p in self._imported_files if not self._cert_is_parsed(p)]
        row = 0
        index = 0

        def add_section(title: str):
            nonlocal row
            label = customtkinter.CTkLabel(
                self.doc_list_frame,
                text=title,
                anchor="w",
                font=customtkinter.CTkFont(size=FONT_META, weight="bold"),
                text_color="gray60",
            )
            label.grid(row=row, column=0, sticky="ew", pady=(8 if row else 2, 4))
            self._bind_doc_list_mousewheel(label)
            row += 1

        def add_docs(paths: list[str]):
            nonlocal row, index
            for path in paths:
                frame = customtkinter.CTkFrame(
                    self.doc_list_frame,
                    height=DOC_ROW_HEIGHT,
                    corner_radius=UI_RADIUS,
                    fg_color="transparent",
                    border_width=0,
                )
                frame.grid(row=row, column=0, sticky="ew", pady=2)
                frame.grid_propagate(False)
                frame.grid_columnconfigure(0, weight=0, minsize=DOC_MARK_COL_WIDTH)
                frame.grid_columnconfigure(1, weight=1)
                frame.grid_rowconfigure(0, weight=1)

                full_name = self._doc_name_label(path, index)
                mark, mark_id = self._make_doc_text_canvas(
                    frame,
                    text=self._doc_leading_label(path, index),
                    fill=self._doc_mark_text_color(path, selected=False),
                    size=self._doc_mark_font_size(path),
                    width=DOC_MARK_COL_WIDTH,
                    anchor="center",
                )
                # Inset so CTkFrame corner_radius remains visible on hover/select.
                mark.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=4)

                name, name_id = self._make_doc_text_canvas(
                    frame,
                    text=full_name,
                    fill=("gray10", "gray90"),
                    size=FONT_BODY,
                    anchor="w",
                )
                name.grid(row=0, column=1, sticky="nsew", padx=(4, 10), pady=4)

                self._doc_rows[path] = {
                    "frame": frame,
                    "mark": mark,
                    "mark_id": mark_id,
                    "name": name,
                    "name_id": name_id,
                    "full_name": full_name,
                    "index": index,
                }
                self._bind_doc_row_interactions(frame, path)
                self._bind_doc_list_mousewheel(frame)
                row += 1
                index += 1

        if parsed:
            add_section(f"已解析（{len(parsed)}）")
            add_docs(parsed)
        if pending:
            title = (
                f"待OCR（{len(pending)}）"
                if self._ocr_enabled
                else f"解析失败（{len(pending)}）"
            )
            add_section(title)
            add_docs(pending)

        self._highlight_selected_doc()
        self._schedule_doc_list_scrollbar_sync()

    def _refresh_doc_list_marks(self):
        for i, path in enumerate(self._imported_files):
            row = self._doc_rows.get(path)
            if row is None:
                continue
            full_name = self._doc_name_label(path, i)
            row["full_name"] = full_name
            row["index"] = i
        self._highlight_selected_doc()

    def _highlight_selected_doc(self):
        for path in self._doc_rows:
            self._style_doc_row(path, selected=(path == self._selected_path))

    def _select_document(self, path: str):
        if path not in self._imported_files:
            return
        if self._autofill_busy:
            return
        self._hide_doc_name_tip()
        self._clear_doc_row_hovers()
        # From settings, resume the step that was open before settings.
        self._leave_settings_if_open()
        if self._selected_path and self._selected_path != path:
            self._save_fields_before_navigate()
        self._selected_path = path
        self._current_cert_index = self._imported_files.index(path)
        self._sync_cert_index_to_list()
        self._show_parse_result(path)
        if self._current_step == "review" and hasattr(self, "field_entries"):
            self._load_approve_fields_for_current()
            self._update_review_cert_status()
        self._sync_pdf_preview()

    def _save_extract_fields_to_result(self):
        path = self._selected_path
        if path is None or path not in self._parse_results:
            return
        existing = self._parse_results[path]
        base = existing.fields
        fields = CertificateFields(
            name=base.name,
            serial_num=base.serial_num,
            model=base.model,
            measurement_unit=base.measurement_unit,
            measurement_date=base.measurement_date,
            measurement_type=base.measurement_type,
            certificate_no=base.certificate_no,
            client_name=base.client_name,
            manufacturer=base.manufacturer,
            due_date=base.due_date,
            issue_date=base.issue_date,
            result_info=base.result_info,
        )
        for key in self.extract_field_entries:
            setattr(fields, key, self.extract_field_entries[key].get().strip())
        self._parse_results[path] = ParseResult(
            source_path=existing.source_path,
            page_count=existing.page_count,
            raw_text=existing.raw_text,
            lines=existing.lines,
            fields=fields,
            method=existing.method,
            errors=list(existing.errors),
        )

    @staticmethod
    def _reset_ctk_entry(entry: customtkinter.CTkEntry) -> None:
        """Clear a CTkEntry and restore its placeholder text.

        CTkEntry.delete() removes visible placeholder characters while leaving
        `_placeholder_text_active` True, so a second clear can leave the field
        blank with no “请输入…” hint. Always deactivate first, then re-activate.
        """
        if getattr(entry, "_placeholder_text_active", False):
            entry._deactivate_placeholder()
        else:
            entry._entry.delete(0, "end")
        # Force placeholder even if the widget still thinks it is focused.
        entry._is_focused = False
        entry._activate_placeholder()

    def _clear_extract_fields_display(self):
        # Blur entries first so placeholder restore is not fighting FocusIn state.
        self.focus_set()
        for entry in self.extract_field_entries.values():
            self._reset_ctk_entry(entry)
        self.extract_errors_label.configure(text="")

    def _show_parse_result(self, path: str):
        result = self._parse_results.get(path)
        if result is None:
            self._clear_extract_fields_display()
            self.extract_errors_label.configure(text="⚠ 尚未解析此文档")
            return

        fields = result.fields
        for key, entry in self.extract_field_entries.items():
            value = getattr(fields, key, "") or ""
            if value:
                if getattr(entry, "_placeholder_text_active", False):
                    entry._deactivate_placeholder()
                else:
                    entry.delete(0, "end")
                entry.insert(0, value)
            else:
                self._reset_ctk_entry(entry)

        if result.errors:
            # Some parse failures are actionable via OCR; hide that specific hint to reduce noise.
            filtered_errors = [
                e for e in result.errors if "封面无嵌入文本" not in str(e)
            ]
            if filtered_errors:
                self.extract_errors_label.configure(
                    text="⚠ " + "\n".join(filtered_errors)
                )
            else:
                self.extract_errors_label.configure(text="")
        else:
            self.extract_errors_label.configure(text="")

    # ----------------------------------------------------------- review + fill
    def _build_review_controls(self, parent: customtkinter.CTkFrame):
        header, content, footer = self._make_pinned_footer_layout(parent)

        self.cert_nav_label = self._build_cert_nav_row(header, row=0)

        match_header_label = customtkinter.CTkLabel(
            content,
            text="比对字段",
            font=customtkinter.CTkFont(size=FONT_SECTION, weight="bold"),
            anchor="w",
        )
        match_header_label.grid(row=0, column=0, sticky="ew", pady=(0, 4))

        self.field_entries: dict[str, customtkinter.CTkEntry | customtkinter.CTkTextbox] = {}
        for row, (key, label) in enumerate(MATCH_FIELDS, start=1):
            customtkinter.CTkLabel(
                content,
                text=label,
                anchor="w",
                width=96,
                font=customtkinter.CTkFont(size=FONT_LABEL),
                text_color="gray60",
            ).grid(row=row, column=0, sticky="w", pady=4)

            entry = self._make_field_entry(content, placeholder=f"请输入{label}")
            entry.grid(row=row, column=0, sticky="ew", padx=(104, 0), pady=4)
            self.field_entries[key] = entry

        fill_header_row = 1 + len(MATCH_FIELDS)
        customtkinter.CTkLabel(
            content,
            text="填写字段",
            font=customtkinter.CTkFont(size=FONT_SECTION, weight="bold"),
            anchor="w",
        ).grid(row=fill_header_row, column=0, sticky="ew", pady=(14, 8))

        for offset, (key, label) in enumerate(METROLOGY_FIELDS):
            row = fill_header_row + 1 + offset
            customtkinter.CTkLabel(
                content,
                text=label,
                anchor="w",
                width=96,
                font=customtkinter.CTkFont(size=FONT_LABEL),
                text_color="gray60",
            ).grid(row=row, column=0, sticky="w", pady=4)

            entry = self._make_field_entry(content, placeholder=f"请输入{label}")
            entry.grid(row=row, column=0, sticky="ew", padx=(104, 0), pady=4)
            self.field_entries[key] = entry

        result_row = fill_header_row + 1 + len(METROLOGY_FIELDS)
        result_label_wrap = customtkinter.CTkFrame(
            content,
            fg_color="transparent",
            width=96,
            height=ENTRY_HEIGHT,
        )
        result_label_wrap.grid(row=result_row, column=0, sticky="nw", pady=4)
        result_label_wrap.grid_propagate(False)
        result_label_wrap.grid_rowconfigure(0, weight=1)
        result_label_wrap.grid_columnconfigure(0, weight=1)
        customtkinter.CTkLabel(
            result_label_wrap,
            text="计量结果信息",
            anchor="w",
            font=customtkinter.CTkFont(size=FONT_LABEL),
            text_color="gray60",
        ).grid(row=0, column=0, sticky="w")

        result_box = customtkinter.CTkTextbox(
            content,
            height=RESULT_INFO_HEIGHT,
            corner_radius=UI_RADIUS,
            border_width=FIELD_BORDER_WIDTH,
            border_color=FIELD_FG_COLOR,
            fg_color=FIELD_FG_COLOR,
            text_color=FIELD_TEXT_COLOR,
            activate_scrollbars=False,
            font=customtkinter.CTkFont(size=FONT_ENTRY),
        )
        result_box.grid(row=result_row, column=0, sticky="ew", padx=(104, 0), pady=4)
        self.field_entries["result_info"] = result_box

        actions = customtkinter.CTkFrame(footer, fg_color="transparent")
        actions.grid(row=0, column=0, sticky="ew")
        actions.grid_columnconfigure((0, 1), weight=1)

        self.approve_toggle_button = customtkinter.CTkButton(
            actions,
            corner_radius=UI_RADIUS,
            text="批准",
            height=PRIMARY_ACTION_BTN_HEIGHT,
            round_height_to_even_numbers=False,
            fg_color=SUCCESS_BTN_FG,
            hover_color=SUCCESS_BTN_HOVER,
            text_color=SUCCESS_BTN_TEXT,
            font=self._button_font(FONT_SECTION),
            command=self._on_toggle_approve_entry,
        )
        self.approve_toggle_button.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        self._style_primary_action_button(self.approve_toggle_button)

        self.remove_toggle_button = customtkinter.CTkButton(
            actions,
            corner_radius=UI_RADIUS,
            text="移除",
            height=PRIMARY_ACTION_BTN_HEIGHT,
            round_height_to_even_numbers=False,
            fg_color=DANGER_BTN_FG,
            hover_color=DANGER_BTN_HOVER,
            text_color=DANGER_BTN_TEXT,
            font=self._button_font(FONT_SECTION),
            command=self._on_toggle_remove_entry,
        )
        self.remove_toggle_button.grid(row=0, column=1, padx=(4, 0), sticky="ew")
        self._style_primary_action_button(self.remove_toggle_button)

        self.export_excel_button = customtkinter.CTkButton(
            footer,
            corner_radius=UI_RADIUS,
            text="导出 Excel (0)",
            height=PRIMARY_ACTION_BTN_HEIGHT,
            round_height_to_even_numbers=False,
            font=self._button_font(FONT_SECTION),
            fg_color=SECONDARY_BTN_FG,
            hover_color=SECONDARY_BTN_HOVER,
            text_color=SECONDARY_BTN_TEXT,
            command=self._on_export_excel,
        )
        self.export_excel_button.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self._style_primary_action_button(self.export_excel_button)

        self.autofill_button = customtkinter.CTkButton(
            footer,
            corner_radius=UI_RADIUS,
            text="导出并自动填写 (0)",
            height=PRIMARY_ACTION_BTN_HEIGHT,
            round_height_to_even_numbers=False,
            font=self._button_font(FONT_SECTION),
            fg_color=PRIMARY_BTN_FG,
            hover_color=PRIMARY_BTN_HOVER,
            text_color=PRIMARY_BTN_TEXT,
            command=self._on_master_autofill,
        )
        self.autofill_button.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self._style_primary_action_button(self.autofill_button)

        self.autofill_controls_frame = customtkinter.CTkFrame(
            footer, fg_color="transparent"
        )
        self.autofill_controls_frame.grid_columnconfigure((0, 1), weight=1)
        self.autofill_pause_button = customtkinter.CTkButton(
            self.autofill_controls_frame,
            corner_radius=UI_RADIUS,
            text="暂停",
            height=PRIMARY_ACTION_BTN_HEIGHT,
            round_height_to_even_numbers=False,
            font=self._button_font(FONT_SECTION),
            fg_color=SECONDARY_BTN_FG,
            hover_color=SECONDARY_BTN_HOVER,
            text_color=SECONDARY_BTN_TEXT,
            command=self._on_autofill_pause_toggle,
        )
        self.autofill_pause_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._style_primary_action_button(self.autofill_pause_button)

        self.autofill_exit_button = customtkinter.CTkButton(
            self.autofill_controls_frame,
            corner_radius=UI_RADIUS,
            text="退出",
            height=PRIMARY_ACTION_BTN_HEIGHT,
            round_height_to_even_numbers=False,
            font=self._button_font(FONT_SECTION),
            fg_color=DANGER_BTN_FG,
            hover_color=DANGER_BTN_HOVER,
            text_color=DANGER_BTN_TEXT,
            command=self._on_autofill_exit,
        )
        self.autofill_exit_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self._style_primary_action_button(self.autofill_exit_button)

    def _get_field_widget_value(self, widget) -> str:
        if isinstance(widget, customtkinter.CTkTextbox):
            return widget.get("1.0", "end-1c").strip()
        return widget.get().strip()

    def _set_field_widget_value(self, widget, value: str):
        if isinstance(widget, customtkinter.CTkTextbox):
            widget.delete("1.0", "end")
            if value:
                widget.insert("1.0", value)
            return
        if value:
            if getattr(widget, "_placeholder_text_active", False):
                widget._deactivate_placeholder()
            else:
                widget.delete(0, "end")
            widget.insert(0, value)
        else:
            self._reset_ctk_entry(widget)

    def _clear_approve_fields(self):
        self.focus_set()
        for widget in self.field_entries.values():
            self._set_field_widget_value(widget, "")

    def _review_fields_locked(self) -> bool:
        path = self._current_cert_path()
        if path is None:
            return False
        return path in self._autofill_queue or path in self._removed_paths

    def _set_review_fields_locked(self, locked: bool):
        if not hasattr(self, "field_entries"):
            return
        if locked:
            self.focus_set()
        state = "disabled" if locked else "normal"
        fg_color = FIELD_FG_COLOR_DISABLED if locked else FIELD_FG_COLOR
        text_color = FIELD_TEXT_COLOR_DISABLED if locked else FIELD_TEXT_COLOR
        for widget in self.field_entries.values():
            kwargs = {
                "state": state,
                "fg_color": fg_color,
                "text_color": text_color,
                "border_color": fg_color,
            }
            widget.configure(**kwargs)

    def _update_review_fields_state(self):
        self._set_review_fields_locked(self._review_fields_locked())

    def _current_cert_path(self) -> str | None:
        if not self._imported_files:
            return None
        return self._imported_files[self._current_cert_index]

    def _read_fields_from_entries(self, base: CertificateFields | None = None) -> CertificateFields:
        fields = CertificateFields() if base is None else CertificateFields(
            name=base.name,
            serial_num=base.serial_num,
            model=base.model,
            measurement_unit=base.measurement_unit,
            measurement_date=base.measurement_date,
            measurement_type=base.measurement_type,
            certificate_no=base.certificate_no,
            client_name=base.client_name,
            manufacturer=base.manufacturer,
            due_date=base.due_date,
            issue_date=base.issue_date,
            result_info=base.result_info,
        )
        for key, _ in REVIEW_DISPLAY_FIELDS:
            if key in self.field_entries:
                setattr(fields, key, self._get_field_widget_value(self.field_entries[key]))
        return fields

    def _empty_review_field_labels(self) -> list[str]:
        if not hasattr(self, "field_entries"):
            return []
        empty: list[str] = []
        for key, label in REVIEW_DISPLAY_FIELDS:
            widget = self.field_entries.get(key)
            if widget is None:
                continue
            if not self._get_field_widget_value(widget):
                empty.append(label)
        return empty

    def _save_current_fields_to_result(self) -> ParseResult | None:
        path = self._current_cert_path()
        if path is None:
            return None
        existing = self._parse_results.get(path)
        base = existing.fields if existing else None
        fields = self._read_fields_from_entries(base)
        if existing is None:
            result = ParseResult(
                source_path=path,
                page_count=0,
                raw_text="",
                lines=[],
                fields=fields,
            )
        else:
            result = ParseResult(
                source_path=existing.source_path,
                page_count=existing.page_count,
                raw_text=existing.raw_text,
                lines=existing.lines,
                fields=fields,
                method=existing.method,
                errors=list(existing.errors),
            )
        self._parse_results[path] = result
        return result

    def _load_approve_fields_for_current(self):
        self._set_review_fields_locked(False)
        self._clear_approve_fields()
        path = self._current_cert_path()
        if path is None:
            self._update_review_cert_status()
            return
        result = self._parse_results.get(path)
        if result is None:
            self._update_review_cert_status()
            return
        fields: CertificateFields = result.fields
        for key, widget in self.field_entries.items():
            value = getattr(fields, key, "") or ""
            if not value and key == "result_info":
                value = DEFAULT_RESULT_INFO
            self._set_field_widget_value(widget, value)
        self._update_review_cert_status()

    def _update_review_cert_status(self):
        self._update_approve_toggle_button()
        self._update_remove_toggle_button()
        self._update_review_fields_state()

    def _update_approve_toggle_button(self):
        if not hasattr(self, "approve_toggle_button"):
            return
        if self._autofill_busy or self._autofill_ui_chrome_locked:
            return
        path = self._current_cert_path()
        if path is not None and path in self._autofill_queue:
            self.approve_toggle_button.configure(
                text="撤销批准",
                fg_color=SECONDARY_BTN_FG,
                hover_color=SECONDARY_BTN_HOVER,
                text_color=SECONDARY_BTN_TEXT,
            )
        else:
            self.approve_toggle_button.configure(
                text="批准",
                fg_color=SUCCESS_BTN_FG,
                hover_color=SUCCESS_BTN_HOVER,
                text_color=SUCCESS_BTN_TEXT,
            )

    def _update_remove_toggle_button(self):
        if not hasattr(self, "remove_toggle_button"):
            return
        if self._autofill_busy or self._autofill_ui_chrome_locked:
            return
        path = self._current_cert_path()
        if path is not None and path in self._removed_paths:
            self.remove_toggle_button.configure(
                text="撤销移除",
                fg_color=SECONDARY_BTN_FG,
                hover_color=SECONDARY_BTN_HOVER,
                text_color=SECONDARY_BTN_TEXT,
            )
        else:
            self.remove_toggle_button.configure(
                text="移除",
                fg_color=DANGER_BTN_FG,
                hover_color=DANGER_BTN_HOVER,
                text_color=DANGER_BTN_TEXT,
            )

    def _update_autofill_button(self):
        if not hasattr(self, "autofill_button"):
            return
        n = len(self._autofill_queue)
        if not self._autofill_busy:
            self.autofill_button.configure(text=f"导出并自动填写 ({n})")
        if hasattr(self, "export_excel_button"):
            self.export_excel_button.configure(text=f"导出 Excel ({n})")

    def _show_autofill_run_controls(self):
        """Swap the autofill CTA for 暂停 / 退出 while a run is active."""
        if hasattr(self, "autofill_button"):
            self.autofill_button.grid_remove()
        if hasattr(self, "autofill_controls_frame"):
            self.autofill_pause_button.configure(
                text="暂停",
                state="normal",
                command=self._on_autofill_pause_toggle,
            )
            self.autofill_exit_button.configure(
                text="退出",
                state="normal",
                command=self._on_autofill_exit,
            )
            self.autofill_controls_frame.grid(
                row=2, column=0, sticky="ew", pady=(10, 0)
            )
        self._lock_ui_for_autofill()

    def _restore_autofill_button(self):
        """Restore the original autofill CTA after a run finishes or exits."""
        self._unlock_ui_after_autofill()
        self._autofill_control = None
        self._autofill_exit_confirming = False
        self._autofill_was_paused_before_exit = False
        if hasattr(self, "autofill_controls_frame"):
            self.autofill_controls_frame.grid_remove()
        if hasattr(self, "autofill_pause_button"):
            try:
                self.autofill_pause_button.configure(
                    text="暂停",
                    state="normal",
                    command=self._on_autofill_pause_toggle,
                )
            except Exception:  # noqa: BLE001
                pass
        if hasattr(self, "autofill_exit_button"):
            try:
                self.autofill_exit_button.configure(
                    text="退出",
                    state="normal",
                    command=self._on_autofill_exit,
                )
            except Exception:  # noqa: BLE001
                pass
        if hasattr(self, "autofill_button"):
            self.autofill_button.configure(state="normal")
            self.autofill_button.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        if hasattr(self, "export_excel_button"):
            self.export_excel_button.configure(state="normal")
        self._update_autofill_button()

    def _autofill_interactive_types(self) -> tuple:
        types = [
            customtkinter.CTkButton,
            customtkinter.CTkEntry,
            customtkinter.CTkTextbox,
            customtkinter.CTkSwitch,
            customtkinter.CTkCheckBox,
            customtkinter.CTkSlider,
            customtkinter.CTkComboBox,
            customtkinter.CTkOptionMenu,
            customtkinter.CTkSegmentedButton,
        ]
        return tuple(types)

    def _widget_config_snapshot(self, widget, keys: tuple[str, ...]) -> dict:
        snap: dict = {}
        for key in keys:
            try:
                snap[key] = widget.cget(key)
            except Exception:  # noqa: BLE001
                pass
        return snap

    def _set_widget_tree_cursor(self, widget, cursor: str):
        try:
            widget.configure(cursor=cursor)
        except Exception:  # noqa: BLE001
            pass
        try:
            children = widget.winfo_children()
        except Exception:  # noqa: BLE001
            return
        for child in children:
            self._set_widget_tree_cursor(child, cursor)

    def _lock_step_tiles_chrome(self):
        """Mute module tiles (fill, outline, badge, label) while autofill runs."""
        for key, tile in getattr(self, "step_tiles", {}).items():
            try:
                tile.configure(
                    fg_color=UI_LOCK_TILE_FG,
                    border_color=UI_LOCK_TILE_FG,
                    border_width=0,
                )
            except Exception:  # noqa: BLE001
                pass
            for child in tile.winfo_children():
                try:
                    if isinstance(child, customtkinter.CTkFrame):
                        child.configure(fg_color=UI_LOCK_BADGE_FG)
                        for sub in child.winfo_children():
                            if isinstance(sub, customtkinter.CTkLabel):
                                sub.configure(text_color=("#ffffff", "#ffffff"))
                    elif isinstance(child, customtkinter.CTkLabel):
                        child.configure(text_color=UI_LOCK_LABEL)
                except Exception:  # noqa: BLE001
                    pass
            self._set_widget_tree_cursor(tile, "")

    def _unlock_step_tiles_chrome(self):
        for key, tile in getattr(self, "step_tiles", {}).items():
            for child in tile.winfo_children():
                try:
                    if isinstance(child, customtkinter.CTkFrame):
                        child.configure(fg_color=ACTIVE_OUTLINE)
                        for sub in child.winfo_children():
                            if isinstance(sub, customtkinter.CTkLabel):
                                sub.configure(text_color=("#ffffff", "#ffffff"))
                    elif isinstance(child, customtkinter.CTkLabel):
                        child.configure(text_color=SECONDARY_BTN_TEXT)
                except Exception:  # noqa: BLE001
                    pass
            self._set_widget_tree_cursor(tile, "")
        self._update_step_tiles(self._current_step)

    def _keep_autofill_run_controls_active(self):
        """Pause/Exit must stay full-color and clickable during the UI lock."""
        if hasattr(self, "autofill_pause_button"):
            try:
                self.autofill_pause_button.configure(
                    state="normal",
                    fg_color=SECONDARY_BTN_FG,
                    hover_color=SECONDARY_BTN_HOVER,
                    text_color=SECONDARY_BTN_TEXT,
                    border_width=0,
                )
                self._style_primary_action_button(self.autofill_pause_button)
            except Exception:  # noqa: BLE001
                pass
        if hasattr(self, "autofill_exit_button"):
            try:
                self.autofill_exit_button.configure(
                    state="normal",
                    fg_color=DANGER_BTN_FG,
                    hover_color=DANGER_BTN_HOVER,
                    text_color=DANGER_BTN_TEXT,
                    border_width=0,
                )
                self._style_primary_action_button(self.autofill_exit_button)
            except Exception:  # noqa: BLE001
                pass

    def _lock_ui_for_autofill(self):
        """Grey out / disable interactables; keep 暂停 / 退出 enabled."""
        self._unlock_ui_after_autofill(reapply_intent=False)
        try:
            self.focus_set()
        except Exception:  # noqa: BLE001
            pass

        allow = set()
        for name in ("autofill_pause_button", "autofill_exit_button"):
            widget = getattr(self, name, None)
            if widget is not None:
                allow.add(widget)

        # Same locked look as validated / removed certificates.
        self._set_review_fields_locked(True)

        field_widgets = set()
        if hasattr(self, "field_entries"):
            field_widgets = set(self.field_entries.values())

        disabled: list = []
        interactive = self._autofill_interactive_types()
        button_keys = (
            "state",
            "fg_color",
            "hover_color",
            "text_color",
            "border_color",
            "border_width",
        )
        plain_keys = ("state",)

        def walk(widget):
            for child in widget.winfo_children():
                walk(child)
            if widget in allow or widget in field_widgets:
                return
            if not isinstance(widget, interactive):
                return
            try:
                current = str(widget.cget("state"))
            except Exception:  # noqa: BLE001
                current = "normal"
            if current == "disabled":
                # Still force-grey colored buttons that were already disabled
                # with a vivid fg_color (批准 / 移除 after style refresh).
                if not isinstance(widget, customtkinter.CTkButton):
                    return
            try:
                if isinstance(widget, customtkinter.CTkButton):
                    snap = self._widget_config_snapshot(widget, button_keys)
                    widget.configure(
                        state="disabled",
                        fg_color=UI_LOCK_BTN_FG,
                        hover_color=UI_LOCK_BTN_FG,
                        text_color=UI_LOCK_BTN_TEXT,
                        border_width=0,
                    )
                else:
                    snap = self._widget_config_snapshot(widget, plain_keys)
                    widget.configure(state="disabled")
                disabled.append((widget, snap))
            except Exception:  # noqa: BLE001
                pass

        walk(self)
        self._autofill_disabled_widgets = disabled

        self._autofill_ui_chrome_locked = True
        self._lock_step_tiles_chrome()
        self._hide_doc_name_tip()
        self._clear_doc_row_hovers()
        self._highlight_selected_doc()
        if hasattr(self, "doc_list_frame"):
            try:
                self.doc_list_frame._scrollbar.grid_remove()
            except Exception:  # noqa: BLE001
                pass
            self._set_widget_tree_cursor(self.doc_list_frame, "")

        self._keep_autofill_run_controls_active()

    def _unlock_ui_after_autofill(self, *, reapply_intent: bool = True):
        for item in self._autofill_disabled_widgets:
            if isinstance(item, tuple):
                widget, snap = item
            else:
                widget, snap = item, {"state": "normal"}
            try:
                if widget.winfo_exists() and snap:
                    widget.configure(**snap)
            except Exception:  # noqa: BLE001
                pass
        self._autofill_disabled_widgets = []

        was_chrome_locked = self._autofill_ui_chrome_locked
        self._autofill_ui_chrome_locked = False
        if was_chrome_locked:
            self._unlock_step_tiles_chrome()
            if hasattr(self, "doc_list_frame"):
                self._set_widget_tree_cursor(self.doc_list_frame, "hand2")
            self._highlight_selected_doc()
            self._schedule_doc_list_scrollbar_sync()

        if not reapply_intent:
            return

        # Re-apply intentional disabled states / colors.
        try:
            self._update_review_fields_state()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._update_extract_ocr_ui()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._update_review_cert_status()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._update_settings_button(self._current_step == "settings")
        except Exception:  # noqa: BLE001
            pass
        try:
            self._restyle_primary_action_buttons()
        except Exception:  # noqa: BLE001
            pass

    def _on_autofill_pause_toggle(self):
        control = self._autofill_control
        if control is None or not self._autofill_busy:
            return
        if control.is_paused():
            control.resume()
            try:
                self.autofill_pause_button.configure(text="暂停", state="normal")
            except Exception:  # noqa: BLE001
                pass
            self.append_autofill_log("已继续自动填写")
            self.set_status("自动填写已继续")
        else:
            control.pause()
            try:
                self.autofill_pause_button.configure(text="继续", state="normal")
            except Exception:  # noqa: BLE001
                pass
            self.append_autofill_log("已暂停自动填写")
            self.set_status("自动填写已暂停")

    def _on_autofill_exit(self):
        """Pause first, then ask for confirmation; second click exits immediately."""
        control = self._autofill_control
        if control is None or not self._autofill_busy:
            return
        if control.cancelled():
            return
        # Second click (or click while toast is up) → exit now, no more waiting.
        if self._autofill_exit_confirming:
            self._confirm_autofill_exit()
            return

        self._autofill_was_paused_before_exit = control.is_paused()
        if not control.is_paused():
            control.pause()
            try:
                self.autofill_pause_button.configure(text="继续")
            except Exception:  # noqa: BLE001
                pass
            self.append_autofill_log("已暂停（等待确认退出）")

        self._autofill_exit_confirming = True
        try:
            self.autofill_exit_button.configure(text="立即退出", state="normal")
        except Exception:  # noqa: BLE001
            pass
        self.set_status("再点一次「立即退出」可马上结束；或等待 10 秒自动退出")
        self.append_autofill_log("退出确认 — 再点「立即退出」马上结束；点取消可继续")
        self.show_toast(
            "自动填写已暂停。\n再点「立即退出」马上结束。\n点「取消」继续填写；10 秒后也会自动退出。",
            title="退出自动填写",
            action_text="立即退出",
            undo_text="取消",
            on_undo=self._cancel_autofill_exit,
            on_complete=self._confirm_autofill_exit,
            duration_ms=AUTOFILL_EXIT_WARN_MS,
            style="danger",
        )

    def _cancel_autofill_exit(self):
        """User declined exit — resume unless they were already paused."""
        self._autofill_exit_confirming = False
        control = self._autofill_control
        if control is None or not self._autofill_busy or control.cancelled():
            return
        try:
            self.autofill_exit_button.configure(text="退出", state="normal")
        except Exception:  # noqa: BLE001
            pass
        if self._autofill_was_paused_before_exit:
            try:
                self.autofill_pause_button.configure(text="继续")
            except Exception:  # noqa: BLE001
                pass
            self.append_autofill_log("已取消退出，保持暂停")
            self.set_status("自动填写已暂停")
            return
        control.resume()
        try:
            self.autofill_pause_button.configure(text="暂停")
        except Exception:  # noqa: BLE001
            pass
        self.append_autofill_log("已取消退出，继续自动填写")
        self.set_status("自动填写已继续")

    def _confirm_autofill_exit(self):
        """Confirmed exit (button, second click, or toast countdown)."""
        if not self._autofill_busy:
            return
        self._autofill_exit_confirming = False
        control = self._autofill_control
        if control is None:
            return
        if control.cancelled():
            # Already exiting — still tear down UI/terminal.
            self.close_autofill_log()
            return
        control.request_exit()
        try:
            self.autofill_pause_button.configure(state="disabled")
            self.autofill_exit_button.configure(text="退出中…", state="disabled")
        except Exception:  # noqa: BLE001
            pass
        self.append_autofill_log("正在退出自动填写…", error=True)
        self.set_status("正在退出自动填写…")
        # Close the terminal immediately — browser stays open for manual review.
        self.close_autofill_log()
        # Safety net: if the worker never returns, restore controls anyway.
        self.after(2500, self._autofill_exit_watchdog)

    def _autofill_exit_watchdog(self):
        if not self._autofill_busy:
            return
        control = self._autofill_control
        if control is None or not control.cancelled():
            return
        self._autofill_busy = False
        self._autofill_exit_confirming = False
        self.close_autofill_log()
        self._restore_autofill_button()
        self.set_status("已停止自动填写（浏览器保持打开）")
        self.show_toast(
            "已停止自动填写。浏览器保持打开。",
            title="导出并自动填写",
            duration_ms=TOAST_SUCCESS_MS,
        )

    def _save_fields_before_navigate(self):
        if (
            self._current_step == "review"
            and hasattr(self, "field_entries")
            and not self._review_fields_locked()
        ):
            self._save_current_fields_to_result()
        elif self._selected_path:
            self._save_extract_fields_to_result()

    def _sync_cert_index_to_list(self):
        path = self._current_cert_path()
        if path is None:
            return
        self._selected_path = path
        self._highlight_selected_doc()
        self._update_cert_nav_labels()

    def _on_prev_certificate(self):
        if not self._imported_files:
            return
        self._save_fields_before_navigate()
        if self._current_cert_index > 0:
            self._current_cert_index -= 1
        self._sync_cert_index_to_list()
        self._load_approve_fields_for_current()
        self.set_status("上一份")

    def _on_next_certificate(self):
        if not self._imported_files:
            return
        self._save_fields_before_navigate()
        if self._current_cert_index < len(self._imported_files) - 1:
            self._current_cert_index += 1
        self._sync_cert_index_to_list()
        self._load_approve_fields_for_current()
        self.set_status("下一份")

    def _leave_settings_if_open(self, key: str | None = None):
        """If settings is showing, leave to `key` or the step that opened settings."""
        if self._current_step != "settings":
            return
        target = key or getattr(self, "_step_before_settings", None) or "extract"
        if target == "settings":
            target = "extract"
        self.show_step(target)

    def _on_open_settings(self):
        if self._autofill_busy:
            self.set_status("自动填写进行中，请先暂停或退出…")
            return
        if self._current_step != "settings":
            self._step_before_settings = self._current_step
        self.show_step("settings")

    def _save_eams_login_info(self):
        username, password = self._eams_credentials()
        if not username or not password:
            self.set_status("请先填写 EAMS 用户名和密码")
            self.show_toast(
                "请先填写 EAMS 用户名和密码。",
                title="EAMS 登录",
                duration_ms=TOAST_SUCCESS_MS,
            )
            return
        try:
            save_credentials(username, password)
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"保存登录信息失败：{exc}")
            self.show_toast(
                f"保存登录信息失败：{exc}",
                title="EAMS 登录",
                duration_ms=TOAST_DEFAULT_MS,
            )
            return
        self.set_status("EAMS 登录信息已保存")
        self.show_success_toast("登录信息已保存。", title="EAMS 登录")

    def _save_autofill_step_delay(self):
        raw = ""
        if hasattr(self, "autofill_step_delay_entry"):
            raw = self.autofill_step_delay_entry.get().strip()
        if not raw:
            raw = str(DEFAULT_AUTOFILL_STEP_DELAY_SEC)
        try:
            value = float(raw)
        except ValueError:
            self.set_status("步骤间隔必须是数字（秒）")
            self.show_toast(
                "请输入有效的秒数，例如 1 或 1.5。",
                title="自动填写",
                duration_ms=TOAST_SUCCESS_MS,
            )
            return
        self._autofill_step_delay_sec = save_autofill_step_delay_sec(value)
        if hasattr(self, "autofill_step_delay_entry"):
            self.autofill_step_delay_entry.delete(0, "end")
            self.autofill_step_delay_entry.insert(0, f"{self._autofill_step_delay_sec:g}")
        msg = f"步骤间隔已设为 {self._autofill_step_delay_sec:g} 秒"
        self.set_status(msg)
        self.show_success_toast(msg, title="自动填写")

    def _cert_nav_text(self) -> str:
        total = len(self._imported_files)
        if total == 0:
            return "0/0"
        return f"{self._current_cert_index + 1}/{total}"

    def _update_cert_nav_labels(self):
        if hasattr(self, "cert_nav_label"):
            self.cert_nav_label.configure(text=self._cert_nav_text())

    def _advance_to_next_document_after_review_action(self):
        """After approve/remove, select the next item in the document list."""
        if not self._imported_files:
            self._update_review_cert_status()
            self._refresh_doc_list_marks()
            return
        nxt = self._current_cert_index + 1
        if nxt < len(self._imported_files):
            self._select_document(self._imported_files[nxt])
            if self._current_step == "review" and hasattr(self, "field_entries"):
                self._load_approve_fields_for_current()
            return
        # Last item — refresh marks/status on the current selection.
        self._update_review_cert_status()
        self._refresh_doc_list_marks()

    def _on_toggle_approve_entry(self):
        path = self._current_cert_path()
        if path is None:
            self.set_status("没有可批准的证书")
            return

        if path in self._autofill_queue:
            self._autofill_queue.remove(path)
            self._update_autofill_button()
            name = Path(path).name
            msg = f"已撤销批准 · {name}"
            # Keep UI anchored on the revoked cert so fields match the current selection.
            if path in self._imported_files:
                self._current_cert_index = self._imported_files.index(path)
                self._sync_cert_index_to_list()
                self._load_approve_fields_for_current()
            else:
                self._update_review_cert_status()
            self._refresh_doc_list_marks()
            self.set_status(msg)
            return

        self._save_current_fields_to_result()
        empty_labels = self._empty_review_field_labels()
        if empty_labels:
            missing = "、".join(empty_labels)
            self.set_status(f"批准失败：{missing}")
            self.show_toast(
                f"以下字段不能为空：\n{missing}",
                title="核对填写",
                duration_ms=TOAST_DEFAULT_MS,
            )
            return

        self._removed_paths.discard(path)
        self._autofill_queue.append(path)
        self._update_autofill_button()
        self.set_status(f"已批准，队列 {len(self._autofill_queue)} 份")
        self._refresh_doc_list_marks()
        self._advance_to_next_document_after_review_action()

    def _on_toggle_remove_entry(self):
        path = self._current_cert_path()
        if path is None:
            self.set_status("没有可操作的证书")
            return

        if path in self._removed_paths:
            self._removed_paths.discard(path)
            self._update_autofill_button()
            self._load_approve_fields_for_current()
            self._refresh_doc_list_marks()
            name = Path(path).name
            msg = f"已撤销移除 · {name}"
            self.set_status(msg)
            return

        if path not in self._imported_files:
            self.set_status("没有需要移除的证书")
            return

        self._removed_paths.add(path)
        if path in self._autofill_queue:
            self._autofill_queue.remove(path)
        self._update_autofill_button()
        name = Path(path).name
        msg = f"已移出核对队列 · {name}"
        self.set_status(msg)
        self._refresh_doc_list_marks()
        self._advance_to_next_document_after_review_action()

    def _on_master_autofill(self):
        """Approve-queue → Excel in exports/ → Playwright MAS batch import + fill."""
        # Cancel any fail-folder countdown so export doesn't archive leftovers
        # (including review-removed certs that were still in a pending toast).
        self._cancel_pending_quarantine()
        self._save_fields_before_navigate()
        n = len(self._autofill_queue)
        if n == 0:
            self.set_status("自动填写：队列为空")
            self.show_toast(
                "请先批准至少一份证书。",
                title="导出并自动填写",
                duration_ms=TOAST_SUCCESS_MS,
            )
            return
        if self._autofill_busy:
            self.set_status("自动填写进行中，请稍候…")
            return

        items: list[AutofillItem] = []
        for path in self._autofill_queue:
            result = self._parse_results.get(path)
            if result is None:
                continue
            items.append(AutofillItem(fields=result.fields, pdf_path=path))
        if not items:
            self.set_status("队列中没有可填写的解析结果")
            self.show_toast(
                "队列中没有可填写的解析结果。",
                title="导出并自动填写",
                duration_ms=TOAST_SUCCESS_MS,
            )
            return

        excel_rows = self._export_rows()
        excel_headers = [label for _key, label in EXPORT_COLUMNS]
        excel_path = next_export_path()

        try:
            write_batch_excel(excel_rows, excel_headers, excel_path)
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"自动填写失败：{exc}")
            self.show_toast(
                f"生成 Excel 失败：{exc}",
                title="导出并自动填写",
            )
            return

        self._autofill_busy = True
        self._autofill_control = AutofillControl()
        self._show_autofill_run_controls()
        browser_bounds = self._prepare_side_browser_layout()
        step_delay = float(self._autofill_step_delay_sec)
        export_msg = f"已导出 {len(excel_rows)} 份到 exports/{excel_path.name}"
        start_msg = f"开始自动填写 {len(items)} 份…"
        self.set_status(f"自动填写开始：{len(items)} 份 → {excel_path.name}")
        self.show_success_toast(export_msg, title="导出 Excel")
        self.show_success_toast(start_msg, title="自动填写")
        self.open_autofill_log()
        self.append_autofill_log(
            f"浏览器侧栏布局 · 步骤间隔 {step_delay:g}s · "
            f"{'测试环境' if self._testing_mode else '正式环境'}"
        )

        username, password = self._eams_credentials()
        if not username or not password:
            username, password = load_credentials()
        if username and password:
            try:
                save_credentials(username, password)
            except Exception:  # noqa: BLE001
                pass

        control = self._autofill_control

        def worker():
            try:
                report = run_mas_autofill(
                    items,
                    excel_path=excel_path,
                    excel_headers=excel_headers,
                    excel_rows=excel_rows,
                    username=username,
                    password=password,
                    batch_import=True,
                    fill_details=True,
                    upload_pdf=True,  # compulsory: corresponding PDF always uploaded
                    submit_workflow=False,
                    status=lambda msg: self.after(0, lambda m=msg: self._on_autofill_progress(m)),
                    control=control,
                    testing=bool(self._testing_mode),
                    window_bounds=browser_bounds,
                    step_delay_sec=step_delay,
                )
                self.after(0, lambda: self._on_autofill_done(report, excel_path))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: self._on_autofill_fail(str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_autofill_progress(self, message: str, *, duration_ms: int | None = None):
        del duration_ms  # Kept for call-site compat; lines live in the terminal panel.
        self.append_autofill_log(
            message,
            error=message.startswith("失败"),
        )

    def _restore_layout_after_browser_session(self):
        """Keep side-by-side layout; autofill Chromium is left open on purpose."""
        # Do not reopen PDF preview or fullscreen — that would fight the open
        # EAMS window. Stay snapped left beside it.
        try:
            self._snap_app_left_half()
        except Exception:  # noqa: BLE001
            pass

    def _on_autofill_done(self, report, excel_path: Path | None = None):
        self._autofill_busy = False
        self._restore_autofill_button()
        self._restore_layout_after_browser_session()

        quarantine = [
            p
            for p in (report.quarantine_paths or [])
            if p in self._imported_files or p in self._autofill_queue
        ]
        # Unique while preserving order.
        seen: set[str] = set()
        quarantine_unique = []
        for path in quarantine:
            if path in seen:
                continue
            seen.add(path)
            quarantine_unique.append(path)
        moved = 0
        if quarantine_unique:
            moved = self._quarantine_failed_paths(quarantine_unique)
            self._rebuild_doc_list()
            self._update_cert_nav_labels()
            self._update_autofill_button()
            if self._imported_files:
                select = (
                    self._selected_path
                    if self._selected_path in self._imported_files
                    else self._imported_files[0]
                )
                self._select_document(select)
            else:
                self._selected_path = None
                if hasattr(self, "field_entries"):
                    self._clear_approve_fields()
                self._sync_pdf_preview()

        err_n = len(report.errors or [])
        cancelled = bool(getattr(report, "cancelled", False))
        excel_note = f" · {excel_path.name}" if excel_path else ""
        summary = (
            f"自动填写完成{excel_note}"
            f" · 导入Excel {'是' if report.imported_excel else '否'}"
            f" · 填写 {report.filled} · 附件 {report.uploaded}"
        )
        if cancelled:
            summary = (
                f"自动填写已退出{excel_note}"
                f" · 填写 {report.filled} · 附件 {report.uploaded}"
            )
        if moved:
            summary += f" · 移出失败 {moved} → {self._failed_items_dir_short()}/"
        if err_n:
            summary += f" · 失败 {err_n}"
            if report.errors:
                summary += f"：{report.errors[0]}"
        self.set_status(summary)
        if cancelled:
            # Exit already closed the terminal; don't reopen via append.
            self.close_autofill_log()
            self.show_toast(
                summary,
                title="导出并自动填写",
                duration_ms=TOAST_SUCCESS_MS,
            )
        else:
            self.append_autofill_log(summary, error=err_n > 0 or moved > 0)
            self.finish_autofill_log(ok=err_n == 0 and moved == 0)
            if err_n == 0 and moved == 0:
                self.show_success_toast("Successfully done", title="自动填写")
            else:
                self.show_toast(summary, title="导出并自动填写")

    def _on_autofill_fail(self, message: str):
        self._autofill_busy = False
        self._restore_autofill_button()
        self._restore_layout_after_browser_session()
        self.set_status(f"自动填写失败：{message}")
        self.append_autofill_log(f"自动填写失败：{message}", error=True)
        self.finish_autofill_log(ok=False, auto_close_ms=4000)
        self.show_toast(f"自动填写失败：{message}", title="导出并自动填写")

    def _export_rows(self) -> list[list[str]]:
        rows: list[list[str]] = []
        for path in self._autofill_queue:
            result = self._parse_results.get(path)
            if result is None:
                continue
            fields = result.fields
            rows.append([getattr(fields, key, "") or "" for key, _label in EXPORT_COLUMNS])
        return rows

    def _on_export_excel(self):
        """Save approved queue Excel into project exports/ (no autofill)."""
        self._cancel_pending_quarantine()
        self._save_fields_before_navigate()
        if self._autofill_busy:
            self.set_status("自动填写进行中，请稍候再导出…")
            return
        rows = self._export_rows()
        if not rows:
            self.set_status("导出 Excel：队列为空")
            self.show_toast(
                "请先批准至少一份证书。",
                title="导出 Excel",
                duration_ms=TOAST_SUCCESS_MS,
            )
            return

        target = next_export_path()
        try:
            write_batch_excel(
                rows,
                [label for _key, label in EXPORT_COLUMNS],
                target,
            )
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"导出 Excel 失败：{exc}")
            self.show_toast(f"导出失败：{exc}", title="导出 Excel")
            return

        msg = f"已导出 {len(rows)} 份到 exports/{target.name}"
        self.set_status(msg)
        self.show_success_toast(msg, title="导出 Excel")

    def _on_close(self):
        if self._autofill_busy:
            self.show_toast(
                "自动填写进行中，请先退出后再关闭窗口。",
                title="自动填写",
                duration_ms=TOAST_SUCCESS_MS,
            )
            return
        try:
            self._pdf_preview.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            close_keepalive_browser()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()

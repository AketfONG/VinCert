"""
VinCert — certificate OCR / parse desktop app.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import threading

import customtkinter
from tkinter import filedialog

from vincert.models import CertificateFields, ParseResult
from vincert.pipeline import parse_certificate
from vincert.folder_import import find_pdfs_in_folder
from vincert.mas_autofill import (
    AutofillItem,
    FAILED_ITEMS_DIR,
    load_credentials,
    next_export_path,
    run_mas_autofill,
    save_credentials,
    write_batch_excel,
)

customtkinter.set_appearance_mode("System")
customtkinter.set_default_color_theme("blue")

SIDEBAR_WIDTH = 188
DOC_SIDEBAR_WIDTH = 400
DOC_SIDEBAR_MARGIN = 10
MAIN_MIN_WIDTH = 480
CONTENT_WRAP = MAIN_MIN_WIDTH - 48
DOC_WRAP = DOC_SIDEBAR_WIDTH - 32
STEP_TILE_SIZE = 144
STEP_TILE_PADX = 18
SETTINGS_BTN_HEIGHT = 40
BUILD_VERSION = "v0.3"
BUILD_DATE = "00/08/2026"

STEPS = [
    ("extract", "批量提取", "📂"),
    ("review", "核对填写", "✍️"),
]

# Sidebar fixed chrome above/below the vertically centered step tiles.
SIDEBAR_TOP_HEIGHT = 90
SIDEBAR_BOTTOM_HEIGHT = 5 + 5 + (SETTINGS_BTN_HEIGHT * 2) + 12 + 100
SIDEBAR_MIN_HEIGHT = (
    SIDEBAR_TOP_HEIGHT
    + len(STEPS) * (STEP_TILE_SIZE + 12)
    + SIDEBAR_BOTTOM_HEIGHT
)

_theme = customtkinter.ThemeManager.theme
APP_BG_COLOR = _theme["CTk"]["fg_color"]
_theme_frame = _theme["CTkFrame"]
EMBED_BG_COLOR = _theme_frame["fg_color"]
TILE_BG_NORMAL = _theme_frame["fg_color"]
TILE_BG_HOVER = _theme_frame["top_fg_color"]
TILE_BG_ACTIVE = ("gray72", "gray26")
TILE_ICON_NORMAL = _theme_frame["top_fg_color"]
TILE_ICON_HOVER = ("gray76", "gray28")
TILE_ICON_ACTIVE = ("gray70", "gray32")
SECONDARY_BTN_FG = ("#c9d1d9", "#3d444b")
SECONDARY_BTN_HOVER = ("#dde4eb", "#556068")
SECONDARY_BTN_TEXT = ("gray10", "gray90")
PRIMARY_BTN_FG = ("#3B8ED0", "#1F6AA5")
PRIMARY_BTN_HOVER = ("#5BA3DB", "#2E83C4")
PRIMARY_BTN_TEXT = ("#DCE4EE", "#DCE4EE")
SUCCESS_BTN_FG = ("#2d8a4e", "#2d8a4e")
SUCCESS_BTN_HOVER = ("#38a460", "#38a460")
SUCCESS_BTN_TEXT = ("#ffffff", "#ffffff")
DANGER_BTN_FG = ("#c0392b", "#c0392b")
DANGER_BTN_HOVER = ("#e74c3c", "#e74c3c")
DANGER_BTN_TEXT = ("#ffffff", "#ffffff")
TOAST_BG = ("#2b2b2b", "#1a1a1a")
TOAST_WIDTH = MAIN_MIN_WIDTH  # cover the right ops column / bottom actions
TOAST_PAD = 12
TOAST_BTN_HEIGHT = 40
TOAST_DEFAULT_MS = 5000
TOAST_SUCCESS_MS = 2000
TOAST_TICK_MS = 50
DOC_ROW_ACTIVE = ("#3b8ed0", "#1f6aa5")
RESULT_INFO_HEIGHT = 112  # ~4 entry rows
_theme_textbox = _theme.get("CTkTextbox", {})
FIELD_FG_COLOR = _theme_textbox.get("fg_color", ("#F9F9FA", "#1D1E1E"))
FIELD_TEXT_COLOR = _theme_textbox.get("text_color", ("gray10", "#DCE4EE"))

MATCH_FIELDS = [
    ("name", "计量器具名称"),
    ("serial_num", "计量器具编号"),
    ("manufacturer", "制造厂"),
]

METROLOGY_FIELDS = [
    ("measurement_type", "检验方式"),
    ("measurement_date", "本次检测日期"),
    ("due_date", "本次检测有效期至"),
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


class App(customtkinter.CTk):
    def __init__(self):
        super().__init__()

        self.title("VinCert")
        self.geometry(
            f"{SIDEBAR_WIDTH + DOC_SIDEBAR_WIDTH + MAIN_MIN_WIDTH + 40}x820"
        )

        self._imported_files: list[str] = []
        self._parse_results: dict[str, ParseResult] = {}
        self._autofill_queue: list[str] = []
        self._removed_paths: set[str] = set()
        self._current_cert_index = 0
        self._current_step = "extract"

        self._source_folder: str | None = None
        self._selected_path: str | None = None
        self._doc_buttons: dict[str, customtkinter.CTkButton] = {}
        self._extract_busy = False
        self._autofill_busy = False
        self._toast_frame: customtkinter.CTkFrame | None = None
        self._toast_hide_after_id: str | None = None
        self._toast_tick_after_id: str | None = None
        self._toast_progress: customtkinter.CTkProgressBar | None = None
        self._toast_countdown_label: customtkinter.CTkLabel | None = None
        self._toast_deadline_ms: int = 0
        self._toast_duration_ms: int = TOAST_DEFAULT_MS
        self._toast_on_complete = None
        self._toast_on_undo = None
        self._toast_settled = False
        self._pending_quarantine_paths: list[str] = []
        self._pending_remove_path: str | None = None
        self._fully_automated = False

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0, minsize=SIDEBAR_WIDTH)
        self.grid_columnconfigure(1, weight=0, minsize=DOC_SIDEBAR_WIDTH)
        self.grid_columnconfigure(2, weight=1, minsize=MAIN_MIN_WIDTH)

        self._build_sidebar()
        self._build_doc_sidebar()
        self._build_controls_panel()
        self._apply_min_window_size()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.show_step("extract")

    # ------------------------------------------------------------------ layout
    def _build_sidebar(self):
        self.sidebar = customtkinter.CTkFrame(
            self, width=SIDEBAR_WIDTH, corner_radius=0, fg_color=APP_BG_COLOR
        )
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(3, weight=1)
        self.sidebar.grid_rowconfigure(5, weight=1)

        customtkinter.CTkLabel(
            self.sidebar,
            text="VinCert",
            font=customtkinter.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=STEP_TILE_PADX, pady=(20, 2), sticky="w")

        customtkinter.CTkLabel(
            self.sidebar,
            text="证件识别与填写",
            font=customtkinter.CTkFont(size=11),
            text_color="gray60",
        ).grid(row=1, column=0, padx=STEP_TILE_PADX, pady=(0, 12), sticky="w")

        self._build_step_progress()

        tiles_container = customtkinter.CTkFrame(self.sidebar, fg_color="transparent")
        tiles_container.grid(row=4, column=0)
        tiles_container.grid_columnconfigure(0, weight=1)

        self.step_tiles: dict[str, customtkinter.CTkFrame] = {}
        self.step_icon_areas: dict[str, customtkinter.CTkFrame] = {}
        for row, (key, label, emoji) in enumerate(STEPS):
            tile, icon_area = self._create_step_tile(tiles_container, key, label, emoji)
            tile.grid(row=row, column=0, padx=STEP_TILE_PADX, pady=6)
            self.step_tiles[key] = tile
            self.step_icon_areas[key] = icon_area

        customtkinter.CTkLabel(
            self.sidebar,
            text=f"{BUILD_VERSION} · {BUILD_DATE}",
            font=customtkinter.CTkFont(size=10),
            text_color="gray60",
        ).grid(row=6, column=0, padx=STEP_TILE_PADX, pady=(0, 6), sticky="w")

        customtkinter.CTkButton(
            self.sidebar,
            text="设置",
            height=SETTINGS_BTN_HEIGHT,
            fg_color=SECONDARY_BTN_FG,
            hover_color=SECONDARY_BTN_HOVER,
            text_color=SECONDARY_BTN_TEXT,
            command=self._on_open_settings,
        ).grid(row=7, column=0, padx=STEP_TILE_PADX, pady=(0, 8), sticky="ew")

        customtkinter.CTkButton(
            self.sidebar,
            text="测试提醒",
            height=SETTINGS_BTN_HEIGHT,
            fg_color=DANGER_BTN_FG,
            hover_color=DANGER_BTN_HOVER,
            text_color=DANGER_BTN_TEXT,
            command=self._on_test_toast,
        ).grid(row=8, column=0, padx=STEP_TILE_PADX, pady=(0, 18), sticky="ew")

    def _build_step_progress(self):
        progress_wrap = customtkinter.CTkFrame(self.sidebar, fg_color="transparent")
        progress_wrap.grid(row=2, column=0, padx=STEP_TILE_PADX, pady=(0, 8), sticky="ew")
        progress_wrap.grid_columnconfigure(tuple(range(len(STEPS))), weight=1, uniform="progress")

        self.progress_step_label = customtkinter.CTkLabel(
            progress_wrap,
            text=f"第 1/{len(STEPS)} 步",
            font=customtkinter.CTkFont(size=11),
            text_color="gray60",
        )
        self.progress_step_label.grid(row=0, column=0, columnspan=len(STEPS), sticky="w", pady=(0, 6))

        self.progress_segments: list[customtkinter.CTkFrame] = []
        for col in range(len(STEPS)):
            segment = customtkinter.CTkFrame(progress_wrap, height=6, corner_radius=3)
            segment.grid(row=1, column=col, padx=(0 if col == 0 else 3, 0), sticky="ew")
            segment.grid_propagate(False)
            self.progress_segments.append(segment)

    def _create_step_tile(
        self,
        parent: customtkinter.CTkFrame,
        key: str,
        label: str,
        emoji: str,
    ) -> tuple[customtkinter.CTkFrame, customtkinter.CTkFrame]:
        tile = customtkinter.CTkFrame(
            parent,
            width=STEP_TILE_SIZE,
            height=STEP_TILE_SIZE,
            corner_radius=12,
            fg_color=TILE_BG_NORMAL,
            border_width=2,
            border_color=_theme_frame["border_color"],
        )
        tile.grid_propagate(False)
        tile.grid_columnconfigure(0, weight=1)
        tile.grid_rowconfigure(0, weight=1)

        icon_area = customtkinter.CTkFrame(
            tile,
            width=STEP_TILE_SIZE - 28,
            height=STEP_TILE_SIZE - 52,
            corner_radius=8,
            fg_color=TILE_ICON_NORMAL,
        )
        icon_area.grid(row=0, column=0, padx=14, pady=(14, 6), sticky="nsew")
        icon_area.grid_propagate(False)

        customtkinter.CTkLabel(
            icon_area,
            text=emoji,
            font=customtkinter.CTkFont(size=36),
        ).place(relx=0.5, rely=0.5, anchor="center")

        customtkinter.CTkLabel(
            tile,
            text=label,
            font=customtkinter.CTkFont(size=13, weight="bold"),
        ).grid(row=1, column=0, pady=(0, 12))

        self._bind_step_tile_click(tile, key)
        self._bind_step_tile_hover(tile, key)

        return tile, icon_area

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

    def _bind_step_tile_click(self, widget, key: str):
        widget.bind("<Button-1>", lambda _e, k=key: self.show_step(k))
        widget.configure(cursor="hand2")
        for child in widget.winfo_children():
            self._bind_step_tile_click(child, key)

    def _hover_step_tile(self, key: str, entering: bool):
        if key == self._current_step:
            return
        if entering:
            self._apply_tile_style(key, "hover")
        else:
            self._apply_tile_style(key, "normal")

    def _apply_tile_style(self, key: str, style: str):
        tile = self.step_tiles[key]
        icon_area = self.step_icon_areas[key]
        styles = {
            "normal": {
                "tile_fg": TILE_BG_NORMAL,
                "tile_border": _theme_frame["border_color"],
                "icon_fg": TILE_ICON_NORMAL,
            },
            "hover": {
                "tile_fg": TILE_BG_HOVER,
                "tile_border": ("gray58", "gray35"),
                "icon_fg": TILE_ICON_HOVER,
            },
            "active": {
                "tile_fg": TILE_BG_ACTIVE,
                "tile_border": ("#3b8ed0", "#1f6aa5"),
                "icon_fg": TILE_ICON_ACTIVE,
            },
        }
        colors = styles[style]
        tile.configure(fg_color=colors["tile_fg"], border_color=colors["tile_border"])
        icon_area.configure(fg_color=colors["icon_fg"])

    def _update_step_progress(self, active_index: int):
        self.progress_step_label.configure(text=f"第 {active_index + 1}/{len(STEPS)} 步")
        active_color = ("#3b8ed0", "#1f6aa5")
        inactive_color = ("gray80", "gray35")
        for index, segment in enumerate(self.progress_segments):
            segment.configure(fg_color=active_color if index <= active_index else inactive_color)

    def _update_step_tiles(self, active_key: str):
        for key in self.step_tiles:
            if key == active_key:
                self._apply_tile_style(key, "active")
            else:
                self._apply_tile_style(key, "normal")

    # ------------------------------------------------------------- doc sidebar
    def _build_doc_sidebar(self):
        self.doc_sidebar = customtkinter.CTkFrame(
            self,
            width=DOC_SIDEBAR_WIDTH,
            corner_radius=0,
            fg_color=APP_BG_COLOR,
        )
        self.doc_sidebar.grid(row=0, column=1, sticky="nsew")
        self.doc_sidebar.grid_propagate(False)
        self.doc_sidebar.grid_columnconfigure(0, weight=1)
        self.doc_sidebar.grid_rowconfigure(0, weight=1)

        self.doc_panel = customtkinter.CTkFrame(
            self.doc_sidebar,
            corner_radius=10,
            fg_color=EMBED_BG_COLOR,
        )
        self.doc_panel.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=DOC_SIDEBAR_MARGIN,
            pady=DOC_SIDEBAR_MARGIN,
        )
        self.doc_panel.grid_columnconfigure(0, weight=1)
        self.doc_panel.grid_rowconfigure(4, weight=1)

        customtkinter.CTkLabel(
            self.doc_panel,
            text="文档列表",
            font=customtkinter.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, padx=16, pady=(16, 8), sticky="ew")

        btn_row = customtkinter.CTkFrame(self.doc_panel, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        btn_row.grid_columnconfigure((0, 1), weight=1)

        customtkinter.CTkButton(
            btn_row,
            text="选择文件夹…",
            fg_color=PRIMARY_BTN_FG,
            hover_color=PRIMARY_BTN_HOVER,
            text_color=PRIMARY_BTN_TEXT,
            command=self._pick_folder,
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        customtkinter.CTkButton(
            btn_row,
            text="清空",
            fg_color=SECONDARY_BTN_FG,
            hover_color=SECONDARY_BTN_HOVER,
            text_color=SECONDARY_BTN_TEXT,
            command=self._clear_extract,
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self.folder_label = customtkinter.CTkLabel(
            self.doc_panel,
            text="未选择文件夹",
            font=customtkinter.CTkFont(size=11),
            text_color="gray60",
            anchor="w",
            wraplength=DOC_WRAP,
            justify="left",
        )
        self.folder_label.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))

        customtkinter.CTkLabel(
            self.doc_panel,
            text="根目录 PDF",
            font=customtkinter.CTkFont(size=12, weight="bold"),
            text_color="gray60",
            anchor="w",
        ).grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 6))

        self.doc_list_frame = customtkinter.CTkScrollableFrame(
            self.doc_panel, fg_color=EMBED_BG_COLOR
        )
        self.doc_list_frame.grid(row=4, column=0, sticky="nsew", padx=12, pady=(0, 14))
        self.doc_list_frame.grid_columnconfigure(0, weight=1)
        self.doc_list_frame.bind("<Configure>", self._update_doc_list_scrollbar)
        self.doc_list_frame._parent_canvas.bind(
            "<Configure>", self._update_doc_list_scrollbar, add="+"
        )

        self.doc_list_empty = customtkinter.CTkLabel(
            self.doc_list_frame,
            text="选择文件夹后，\nPDF 将显示在这里",
            text_color="gray50",
            justify="center",
        )
        self.doc_list_empty.grid(row=0, column=0, pady=24)
        self._update_doc_list_scrollbar()

    # ------------------------------------------------------------- main panel
    def _build_controls_panel(self):
        self.controls_panel = customtkinter.CTkFrame(
            self, corner_radius=0, fg_color=APP_BG_COLOR
        )
        self.controls_panel.grid(row=0, column=2, sticky="nsew", padx=0)
        self.controls_panel.grid_rowconfigure(1, weight=1)
        self.controls_panel.grid_columnconfigure(0, weight=1)

        self.controls_header = customtkinter.CTkLabel(
            self.controls_panel,
            text="",
            font=customtkinter.CTkFont(size=16, weight="bold"),
            anchor="w",
        )
        self.controls_header.grid(row=0, column=0, padx=24, pady=(16, 8), sticky="ew")

        self.controls_body = customtkinter.CTkFrame(self.controls_panel, fg_color="transparent")
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
            builder(frame)
            self.step_views[key] = frame

    def show_step(self, key: str):
        if key in ("extract", "review"):
            self._current_step = key
            step_index = next(i for i, (k, _, _) in enumerate(STEPS) if k == key)
            self._update_step_progress(step_index)
            self._update_step_tiles(key)

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
            self._load_approve_fields_for_current()
            self._update_autofill_button()

    def _build_settings_controls(self, parent: customtkinter.CTkFrame):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(10, weight=1)

        customtkinter.CTkLabel(
            parent,
            text="自动化",
            anchor="w",
            font=customtkinter.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self.fully_automated_switch = customtkinter.CTkSwitch(
            parent,
            text="全自动模式",
            command=self._on_toggle_fully_automated,
        )
        self.fully_automated_switch.grid(row=1, column=0, sticky="w")
        if self._fully_automated:
            self.fully_automated_switch.select()
        else:
            self.fully_automated_switch.deselect()

        customtkinter.CTkLabel(
            parent,
            text="占位选项 · 开启后将跳过人工核对（尚未接入）。",
            anchor="w",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTENT_WRAP,
            justify="left",
        ).grid(row=2, column=0, sticky="ew", pady=(8, 24))

        customtkinter.CTkLabel(
            parent,
            text="EAMS 登录",
            anchor="w",
            font=customtkinter.CTkFont(size=14, weight="bold"),
        ).grid(row=3, column=0, sticky="ew", pady=(0, 8))

        customtkinter.CTkLabel(
            parent,
            text="填写账号密码并保存，自动填写时会代为登录。",
            anchor="w",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTENT_WRAP,
            justify="left",
        ).grid(row=4, column=0, sticky="ew", pady=(0, 12))

        saved_user, saved_pass = load_credentials()

        customtkinter.CTkLabel(parent, text="用户名", anchor="w").grid(
            row=5, column=0, sticky="ew", pady=(0, 4)
        )
        self.eams_username_entry = customtkinter.CTkEntry(
            parent,
            placeholder_text="EAMS 用户名",
            fg_color=FIELD_FG_COLOR,
            text_color=FIELD_TEXT_COLOR,
            border_width=0,
        )
        self.eams_username_entry.grid(row=6, column=0, sticky="ew", pady=(0, 10))
        if saved_user:
            self.eams_username_entry.insert(0, saved_user)

        customtkinter.CTkLabel(parent, text="密码", anchor="w").grid(
            row=7, column=0, sticky="ew", pady=(0, 4)
        )
        self.eams_password_entry = customtkinter.CTkEntry(
            parent,
            placeholder_text="EAMS 密码",
            show="•",
            fg_color=FIELD_FG_COLOR,
            text_color=FIELD_TEXT_COLOR,
            border_width=0,
        )
        self.eams_password_entry.grid(row=8, column=0, sticky="ew", pady=(0, 12))
        if saved_pass:
            self.eams_password_entry.insert(0, saved_pass)

        customtkinter.CTkButton(
            parent,
            text="保存登录信息",
            height=40,
            fg_color=PRIMARY_BTN_FG,
            hover_color=PRIMARY_BTN_HOVER,
            text_color=PRIMARY_BTN_TEXT,
            command=self._save_eams_login_info,
        ).grid(row=9, column=0, sticky="ew")

    def _on_toggle_fully_automated(self):
        self._fully_automated = bool(self.fully_automated_switch.get())
        state = "已开启" if self._fully_automated else "已关闭"
        self.set_status(f"全自动模式{state}（占位，尚未接入）")

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

    def show_toast(
        self,
        message: str,
        *,
        title: str = "提醒",
        duration_ms: int = TOAST_DEFAULT_MS,
        action_text: str = "关闭",
        undo_text: str | None = None,
        on_undo=None,
        on_complete=None,
        style: str = "danger",
    ):
        """Show a bottom-right countdown toast with a draining progress bar.

        Auto-closes when the countdown reaches 0 (runs on_complete if set).
        Primary action dismisses early and also runs on_complete.
        Optional grey undo button runs on_undo instead and skips on_complete.
        style: "danger" (red) or "success" (green).
        """
        self.hide_toast(run_complete=False)

        self._toast_on_complete = on_complete
        self._toast_on_undo = on_undo
        self._toast_settled = False

        if style == "success":
            accent = SUCCESS_BTN_FG
            accent_hover = SUCCESS_BTN_HOVER
            accent_text = SUCCESS_BTN_TEXT
        else:
            accent = DANGER_BTN_FG
            accent_hover = DANGER_BTN_HOVER
            accent_text = DANGER_BTN_TEXT

        toast = customtkinter.CTkFrame(
            self,
            width=TOAST_WIDTH,
            corner_radius=12,
            fg_color=TOAST_BG,
            border_width=1,
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
            font=customtkinter.CTkFont(size=16, weight="bold"),
            text_color=("#ffffff", "#ffffff"),
        ).grid(row=0, column=0, sticky="ew")

        seconds = max(1, int(round(duration_ms / 1000)))
        countdown = customtkinter.CTkLabel(
            header,
            text=f"{seconds}s",
            anchor="e",
            font=customtkinter.CTkFont(size=14, weight="bold"),
            text_color=accent_hover,
        )
        countdown.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self._toast_countdown_label = countdown

        customtkinter.CTkLabel(
            toast,
            text=message,
            anchor="w",
            justify="left",
            wraplength=TOAST_WIDTH - 36,
            font=customtkinter.CTkFont(size=14),
            text_color=("#f0f0f0", "#f0f0f0"),
        ).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 12))

        progress = customtkinter.CTkProgressBar(
            toast,
            height=10,
            progress_color=accent,
        )
        progress.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 12))
        progress.set(1.0)
        self._toast_progress = progress

        btn_row = customtkinter.CTkFrame(toast, fg_color="transparent")
        btn_row.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 16))
        if undo_text:
            btn_row.grid_columnconfigure(0, weight=1)
            btn_row.grid_columnconfigure(1, weight=1)
            customtkinter.CTkButton(
                btn_row,
                text=undo_text,
                height=TOAST_BTN_HEIGHT,
                font=customtkinter.CTkFont(size=14, weight="bold"),
                fg_color=SECONDARY_BTN_FG,
                hover_color=SECONDARY_BTN_HOVER,
                text_color=SECONDARY_BTN_TEXT,
                command=self._toast_undo,
            ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
            customtkinter.CTkButton(
                btn_row,
                text=action_text,
                height=TOAST_BTN_HEIGHT,
                font=customtkinter.CTkFont(size=14, weight="bold"),
                fg_color=accent,
                hover_color=accent_hover,
                text_color=accent_text,
                command=lambda: self.hide_toast(run_complete=True),
            ).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        else:
            btn_row.grid_columnconfigure(0, weight=1)
            customtkinter.CTkButton(
                btn_row,
                text=action_text,
                height=TOAST_BTN_HEIGHT,
                font=customtkinter.CTkFont(size=14, weight="bold"),
                fg_color=accent,
                hover_color=accent_hover,
                text_color=accent_text,
                command=lambda: self.hide_toast(run_complete=True),
            ).grid(row=0, column=0, sticky="ew")

        toast.place(relx=1.0, rely=1.0, x=-TOAST_PAD, y=-TOAST_PAD, anchor="se")
        toast.lift()
        toast.tkraise()
        self._toast_frame = toast

        self._toast_duration_ms = max(duration_ms, TOAST_TICK_MS)
        self._toast_deadline_ms = int(self.winfo_toplevel().tk.call("clock", "milliseconds")) + self._toast_duration_ms
        self._toast_tick()

    def show_success_toast(self, message: str, *, title: str = "OCR提取"):
        self.show_toast(
            message,
            title=title,
            duration_ms=TOAST_SUCCESS_MS,
            action_text="关闭",
            style="success",
        )

    def _toast_tick(self):
        if self._toast_frame is None:
            return
        now = int(self.winfo_toplevel().tk.call("clock", "milliseconds"))
        remaining = max(0, self._toast_deadline_ms - now)
        ratio = remaining / self._toast_duration_ms if self._toast_duration_ms else 0
        if self._toast_progress is not None:
            self._toast_progress.set(ratio)
        if self._toast_countdown_label is not None:
            secs = int((remaining + 999) // 1000)  # ceil seconds
            self._toast_countdown_label.configure(text=f"{secs}s")
        if remaining <= 0:
            self.hide_toast(run_complete=True)
            return
        self._toast_tick_after_id = self.after(TOAST_TICK_MS, self._toast_tick)

    def _toast_undo(self):
        if self._toast_settled:
            return
        self._toast_settled = True
        cb = self._toast_on_undo
        self._toast_on_complete = None
        self._toast_on_undo = None
        self.hide_toast(run_complete=False)
        if cb is not None:
            cb()

    def hide_toast(self, *, run_complete: bool = False):
        if self._toast_tick_after_id is not None:
            try:
                self.after_cancel(self._toast_tick_after_id)
            except Exception:  # noqa: BLE001
                pass
            self._toast_tick_after_id = None
        if self._toast_hide_after_id is not None:
            try:
                self.after_cancel(self._toast_hide_after_id)
            except Exception:  # noqa: BLE001
                pass
            self._toast_hide_after_id = None
        self._toast_progress = None
        self._toast_countdown_label = None
        if self._toast_frame is not None:
            try:
                self._toast_frame.place_forget()
                self._toast_frame.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._toast_frame = None

        complete_cb = None
        if run_complete and not self._toast_settled:
            self._toast_settled = True
            complete_cb = self._toast_on_complete
        self._toast_on_complete = None
        self._toast_on_undo = None
        if complete_cb is not None:
            complete_cb()

    def _on_test_toast(self):
        self.show_toast(
            "倒计时提醒测试。\n进度条走完后自动关闭，也可点红色按钮提前关闭。",
            title="测试提醒",
            duration_ms=TOAST_DEFAULT_MS,
            undo_text="撤销",
            on_undo=lambda: self.set_status("已撤销测试提醒"),
        )

    def _apply_min_window_size(self):
        self.minsize(
            SIDEBAR_WIDTH + DOC_SIDEBAR_WIDTH + MAIN_MIN_WIDTH,
            SIDEBAR_MIN_HEIGHT,
        )

    def _build_cert_nav_row(self, parent, row: int) -> customtkinter.CTkLabel:
        nav_row = customtkinter.CTkFrame(parent, fg_color="transparent")
        nav_row.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        nav_row.grid_columnconfigure(1, weight=1)

        customtkinter.CTkButton(
            nav_row,
            text="上一份",
            width=72,
            fg_color=PRIMARY_BTN_FG,
            hover_color=PRIMARY_BTN_HOVER,
            text_color=PRIMARY_BTN_TEXT,
            command=self._on_prev_certificate,
        ).grid(row=0, column=0, padx=(0, 6))

        nav_label = customtkinter.CTkLabel(
            nav_row,
            text="0/0",
            font=customtkinter.CTkFont(size=13),
        )
        nav_label.grid(row=0, column=1)

        customtkinter.CTkButton(
            nav_row,
            text="下一份",
            width=72,
            fg_color=PRIMARY_BTN_FG,
            hover_color=PRIMARY_BTN_HOVER,
            text_color=PRIMARY_BTN_TEXT,
            command=self._on_next_certificate,
        ).grid(row=0, column=2, padx=(6, 0))

        return nav_label

    # ---------------------------------------------------------------- extract
    def _build_extract_controls(self, parent: customtkinter.CTkFrame):
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=0)
        parent.grid_columnconfigure(0, weight=1)

        content = customtkinter.CTkFrame(parent, fg_color="transparent")
        content.grid(row=0, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            content,
            text="从左侧文档列表点选证书，核对提取结果后前往填写。",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTENT_WRAP,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        # Parsed fields — match keys for webpage verification + autofill targets
        customtkinter.CTkLabel(
            content,
            text="比对字段",
            font=customtkinter.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(0, 2))

        customtkinter.CTkLabel(
            content,
            text="用于与网页元素比对，确保填写到正确记录",
            font=customtkinter.CTkFont(size=11),
            text_color="gray60",
            anchor="w",
            wraplength=CONTENT_WRAP,
            justify="left",
        ).grid(row=2, column=0, sticky="ew", pady=(0, 6))

        self.extract_match_frame = customtkinter.CTkFrame(content, fg_color="transparent")
        self.extract_match_frame.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        self.extract_match_frame.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            content,
            text="填写字段",
            font=customtkinter.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=4, column=0, sticky="ew", pady=(0, 6))

        self.extract_autofill_frame = customtkinter.CTkFrame(content, fg_color="transparent")
        self.extract_autofill_frame.grid(row=5, column=0, sticky="ew", pady=(0, 8))
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
                    font=customtkinter.CTkFont(size=12),
                    text_color="gray60",
                ).grid(row=row, column=0, sticky="w", pady=4)

                entry = customtkinter.CTkEntry(
                    frame,
                    placeholder_text=f"请输入{label}",
                    border_width=0,
                    fg_color=FIELD_FG_COLOR,
                    text_color=FIELD_TEXT_COLOR,
                )
                entry.grid(row=row, column=0, sticky="ew", padx=(104, 0), pady=4)
                self.extract_field_entries[key] = entry

        self.extract_errors_label = customtkinter.CTkLabel(
            content,
            text="",
            anchor="w",
            justify="left",
            wraplength=CONTENT_WRAP,
            font=customtkinter.CTkFont(size=11),
            text_color="#c0392b",
        )
        self.extract_errors_label.grid(row=6, column=0, sticky="ew", pady=(0, 6))

        self.extract_status_label = customtkinter.CTkLabel(
            content,
            text="等待选择文件夹",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTENT_WRAP,
            justify="left",
            anchor="w",
        )
        self.extract_status_label.grid(row=7, column=0, sticky="ew", pady=(0, 8))

        footer = customtkinter.CTkFrame(parent, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        footer.grid_columnconfigure(0, weight=1)

        self.ocr_progress = customtkinter.CTkProgressBar(footer, height=10)
        self.ocr_progress.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.ocr_progress.set(0)

        self.ocr_progress_label = customtkinter.CTkLabel(
            footer,
            text="OCR 进度 0/0",
            font=customtkinter.CTkFont(size=11),
            text_color="gray60",
            anchor="w",
        )
        self.ocr_progress_label.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        self.ocr_extract_button = customtkinter.CTkButton(
            footer,
            text="OCR提取",
            height=40,
            fg_color=SUCCESS_BTN_FG,
            hover_color=SUCCESS_BTN_HOVER,
            text_color=SUCCESS_BTN_TEXT,
            command=self._on_ocr_extract,
        )
        self.ocr_extract_button.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        customtkinter.CTkButton(
            footer,
            text="前往核对填写 →",
            height=40,
            fg_color=PRIMARY_BTN_FG,
            hover_color=PRIMARY_BTN_HOVER,
            text_color=PRIMARY_BTN_TEXT,
            command=self._go_to_review,
        ).grid(row=3, column=0, sticky="ew")

    def _update_doc_list_scrollbar(self, _event=None):
        canvas = self.doc_list_frame._parent_canvas
        scrollbar = self.doc_list_frame._scrollbar
        self.update_idletasks()
        bbox = canvas.bbox("all")
        content_height = 0 if bbox is None else bbox[3] - bbox[1]
        needs_scroll = content_height > canvas.winfo_height()
        if needs_scroll:
            scrollbar.grid()
        else:
            scrollbar.grid_remove()

    def _pick_folder(self):
        path = filedialog.askdirectory(parent=self, title="选择证书文件夹")
        if not path:
            self.set_status("未选择文件夹")
            return
        self._load_folder(path)

    def _clear_extract(self):
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
        self.extract_status_label.configure(text="等待选择文件夹")
        self._reset_ocr_progress()
        self._update_cert_nav_labels()
        if hasattr(self, "field_entries"):
            self._clear_approve_fields()
        self._update_autofill_button()
        self.set_status("已清空提取列表")

    def _load_folder(self, folder: str):
        if self._extract_busy:
            self.extract_status_label.configure(text="正在处理中，请稍候…")
            return

        self.folder_label.configure(text=folder)
        self.extract_status_label.configure(text="正在扫描文件夹…")
        self.update_idletasks()

        try:
            pdfs = find_pdfs_in_folder(folder, recursive=False)
        except Exception as exc:  # noqa: BLE001
            self.extract_status_label.configure(text=f"失败：{exc}")
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
            self.extract_status_label.configure(text="该文件夹根目录下没有 PDF")
            self.set_status("文件夹中没有 PDF")
            return

        # Embedded-text parse only — OCR is manual via 「OCR提取」.
        self._extract_busy = True
        results: dict[str, ParseResult] = {}
        try:
            for i, path in enumerate(paths, start=1):
                self.extract_status_label.configure(text=f"解析进度 {i}/{len(paths)}")
                self.update_idletasks()
                results[path] = parse_certificate(path, use_ocr_fallback=False, force_ocr=False)
            self._on_folder_loaded(folder, paths, results)
        except Exception as exc:  # noqa: BLE001
            self._on_folder_fail(str(exc))
        finally:
            self._extract_busy = False

    def _on_folder_fail(self, message: str):
        self._extract_busy = False
        self.extract_status_label.configure(text=f"失败：{message}")
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
            self.extract_status_label.configure(
                text=f"已加载 {len(paths)} 份 · 已解析 {ok} · 待 OCR {pending}"
            )
            self.set_status(f"文件夹加载完成：已解析 {ok} · 待 OCR {pending}")
        else:
            self.extract_status_label.configure(text=f"已加载 {len(paths)} 份 · 全部已解析")
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
        if hasattr(self, "extract_status_label"):
            self.extract_status_label.configure(text=label)

    def _unique_failed_path(self, src: Path) -> Path:
        FAILED_ITEMS_DIR.mkdir(parents=True, exist_ok=True)
        dest = FAILED_ITEMS_DIR / src.name
        if not dest.exists():
            return dest
        stem, suffix = src.stem, src.suffix
        n = 1
        while True:
            candidate = FAILED_ITEMS_DIR / f"{stem}_{n}{suffix}"
            if not candidate.exists():
                return candidate
            n += 1

    def _quarantine_failed_paths(self, paths: list[str]) -> int:
        """Copy failed PDFs to project failed_items/ and remove them from the queue."""
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
        if self._extract_busy or self._autofill_busy:
            self.extract_status_label.configure(text="正在处理中，请稍候…")
            return
        if not self._imported_files:
            self.extract_status_label.configure(text="请先选择文件夹")
            return

        targets = [p for p in self._imported_files if self._cert_needs_ocr(p)]
        if not targets:
            self.extract_status_label.configure(text="全部证书已有文本解析结果，无需 OCR")
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

        if hasattr(self, "ocr_progress"):
            self.ocr_progress.set(1 if total else 0)
        if hasattr(self, "ocr_progress_label"):
            self.ocr_progress_label.configure(text=f"OCR 进度 {total}/{total}")

        msg = f"OCR 完成 · 成功 {done}/{total}"
        if failed_paths:
            pending = list(failed_paths)
            self._pending_quarantine_paths = pending
            msg += f" · 失败 {len(pending)} 份将移出队列"
            self.extract_status_label.configure(text=msg)
            self.set_status(msg)
            self.show_toast(
                f"{msg}\n倒计时结束后移至 failed_items/，可点撤销保留。",
                title="OCR提取",
                action_text="立即移出",
                undo_text="撤销",
                on_undo=self._undo_pending_quarantine,
                on_complete=self._commit_pending_quarantine,
            )
            return

        self.extract_status_label.configure(text=msg)
        self.set_status(msg)
        self.show_success_toast(msg)

    def _undo_pending_quarantine(self):
        count = len(self._pending_quarantine_paths)
        self._pending_quarantine_paths = []
        msg = f"已撤销移出 · 失败 {count} 份仍留在队列"
        self.extract_status_label.configure(text=msg)
        self.set_status(msg)

    def _commit_pending_quarantine(self):
        paths = list(self._pending_quarantine_paths)
        self._pending_quarantine_paths = []
        if not paths:
            self.show_success_toast("没有需要移出的证书。")
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
        msg = f"失败 {moved} 份已移出队列 · 已复制到 failed_items/"
        self.extract_status_label.configure(text=msg)
        self.set_status(msg)
        self.show_success_toast(msg)

    def _on_ocr_extract_fail(self, message: str):
        self._extract_busy = False
        if hasattr(self, "ocr_extract_button"):
            self.ocr_extract_button.configure(state="normal")
        self._reset_ocr_progress()
        self.extract_status_label.configure(text=f"OCR 失败：{message}")
        self.set_status(f"OCR 失败：{message}")
        self.show_toast(f"OCR 失败：{message}", title="OCR提取")

    def _doc_status_mark(self, path: str) -> str:
        if path in self._removed_paths:
            return "❌"
        if path in self._autofill_queue:
            return "✅"
        # Fullwidth spaces approximate emoji width so numbers stay aligned.
        return "　　"

    def _doc_list_label(self, path: str, index: int) -> str:
        return f"{self._doc_status_mark(path)} {index + 1}. {Path(path).name}"

    def _rebuild_doc_list(self):
        for child in self.doc_list_frame.winfo_children():
            child.destroy()
        self._doc_buttons.clear()

        if not self._imported_files:
            self.doc_list_empty = customtkinter.CTkLabel(
                self.doc_list_frame,
                text="选择文件夹后，\nPDF 将显示在这里",
                text_color="gray50",
                justify="center",
            )
            self.doc_list_empty.grid(row=0, column=0, pady=20)
            self._update_doc_list_scrollbar()
            return

        parsed = [p for p in self._imported_files if self._cert_is_parsed(p)]
        pending = [p for p in self._imported_files if not self._cert_is_parsed(p)]
        row = 0
        index = 0

        def add_section(title: str):
            nonlocal row
            customtkinter.CTkLabel(
                self.doc_list_frame,
                text=title,
                anchor="w",
                font=customtkinter.CTkFont(size=11, weight="bold"),
                text_color="gray60",
            ).grid(row=row, column=0, sticky="ew", pady=(8 if row else 2, 4))
            row += 1

        def add_docs(paths: list[str]):
            nonlocal row, index
            for path in paths:
                btn = customtkinter.CTkButton(
                    self.doc_list_frame,
                    text=self._doc_list_label(path, index),
                    anchor="w",
                    height=32,
                    fg_color="transparent",
                    text_color=("gray10", "gray90"),
                    hover_color=TILE_BG_HOVER,
                    command=lambda p=path: self._select_document(p),
                )
                btn.grid(row=row, column=0, sticky="ew", pady=2)
                self._doc_buttons[path] = btn
                row += 1
                index += 1

        if parsed:
            add_section(f"已解析（{len(parsed)}）")
            add_docs(parsed)
        if pending:
            add_section(f"待 OCR / 未解析（{len(pending)}）")
            add_docs(pending)

        self._highlight_selected_doc()
        self._update_doc_list_scrollbar()

    def _refresh_doc_list_marks(self):
        for i, path in enumerate(self._imported_files):
            btn = self._doc_buttons.get(path)
            if btn is not None:
                btn.configure(text=self._doc_list_label(path, i))
        self._highlight_selected_doc()

    def _highlight_selected_doc(self):
        for path, btn in self._doc_buttons.items():
            if path == self._selected_path:
                btn.configure(
                    fg_color=DOC_ROW_ACTIVE,
                    hover_color=DOC_ROW_ACTIVE,
                    text_color=("white", "white"),
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    hover_color=TILE_BG_HOVER,
                    text_color=("gray10", "gray90"),
                )

    def _select_document(self, path: str):
        if path not in self._imported_files:
            return
        if self._selected_path and self._selected_path != path:
            self._save_fields_before_navigate()
        self._selected_path = path
        self._current_cert_index = self._imported_files.index(path)
        self._sync_cert_index_to_list()
        self._show_parse_result(path)
        if self._current_step == "review" and hasattr(self, "field_entries"):
            self._load_approve_fields_for_current()
            self._update_review_cert_status()

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

    def _clear_extract_fields_display(self):
        for entry in self.extract_field_entries.values():
            entry.delete(0, "end")
        self.extract_errors_label.configure(text="")

    def _show_parse_result(self, path: str):
        result = self._parse_results.get(path)
        if result is None:
            self._clear_extract_fields_display()
            self.extract_errors_label.configure(text="⚠ 尚未解析此文档")
            return

        fields = result.fields
        for key, entry in self.extract_field_entries.items():
            entry.delete(0, "end")
            value = getattr(fields, key, "")
            if value:
                entry.insert(0, value)

        if result.errors:
            self.extract_errors_label.configure(text="⚠ " + "\n".join(result.errors))
        else:
            self.extract_errors_label.configure(text="")

    def _go_to_review(self):
        if not self._imported_files:
            self.extract_status_label.configure(text="请先选择文件夹并完成提取")
            return
        self._save_extract_fields_to_result()
        self._load_approve_fields_for_current()
        self.show_step("review")

    # ----------------------------------------------------------- review + fill
    def _build_review_controls(self, parent: customtkinter.CTkFrame):
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=0)
        parent.grid_columnconfigure(0, weight=1)

        content = customtkinter.CTkFrame(parent, fg_color="transparent")
        content.grid(row=0, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            content,
            text="核对识别结果；批准后计入自动填写队列，可用主按钮批量写入网页。",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTENT_WRAP,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        self.cert_nav_label = self._build_cert_nav_row(content, row=1)

        self.review_cert_status = customtkinter.CTkLabel(
            content,
            text="当前：未批准",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            anchor="w",
        )
        self.review_cert_status.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        customtkinter.CTkLabel(
            content,
            text="比对字段",
            font=customtkinter.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).grid(row=3, column=0, sticky="ew", pady=(0, 4))

        customtkinter.CTkLabel(
            content,
            text="与网页元素比对，确保填写到正确记录",
            font=customtkinter.CTkFont(size=11),
            text_color="gray60",
            anchor="w",
            wraplength=CONTENT_WRAP,
            justify="left",
        ).grid(row=4, column=0, sticky="ew", pady=(0, 6))

        self.field_entries: dict[str, customtkinter.CTkEntry | customtkinter.CTkTextbox] = {}
        for row, (key, label) in enumerate(MATCH_FIELDS, start=5):
            customtkinter.CTkLabel(
                content,
                text=label,
                anchor="w",
                width=96,
                font=customtkinter.CTkFont(size=12),
                text_color="gray60",
            ).grid(row=row, column=0, sticky="w", pady=4)

            entry = customtkinter.CTkEntry(
                content,
                placeholder_text=f"请输入{label}",
                border_width=0,
                fg_color=FIELD_FG_COLOR,
                text_color=FIELD_TEXT_COLOR,
            )
            entry.grid(row=row, column=0, sticky="ew", padx=(104, 0), pady=4)
            self.field_entries[key] = entry

        fill_header_row = 5 + len(MATCH_FIELDS)
        customtkinter.CTkLabel(
            content,
            text="填写字段",
            font=customtkinter.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).grid(row=fill_header_row, column=0, sticky="ew", pady=(10, 8))

        for offset, (key, label) in enumerate(METROLOGY_FIELDS):
            row = fill_header_row + 1 + offset
            customtkinter.CTkLabel(
                content,
                text=label,
                anchor="w",
                width=96,
                font=customtkinter.CTkFont(size=12),
                text_color="gray60",
            ).grid(row=row, column=0, sticky="w", pady=4)

            entry = customtkinter.CTkEntry(
                content,
                placeholder_text=f"请输入{label}",
                border_width=0,
                fg_color=FIELD_FG_COLOR,
                text_color=FIELD_TEXT_COLOR,
            )
            entry.grid(row=row, column=0, sticky="ew", padx=(104, 0), pady=4)
            self.field_entries[key] = entry

        result_row = fill_header_row + 1 + len(METROLOGY_FIELDS)
        customtkinter.CTkLabel(
            content,
            text="计量结果信息",
            anchor="nw",
            width=96,
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
        ).grid(row=result_row, column=0, sticky="nw", pady=4)

        result_box = customtkinter.CTkTextbox(
            content,
            height=RESULT_INFO_HEIGHT,
            border_width=0,
            fg_color=FIELD_FG_COLOR,
            text_color=FIELD_TEXT_COLOR,
            activate_scrollbars=False,
        )
        result_box.grid(row=result_row, column=0, sticky="ew", padx=(104, 0), pady=4)
        self.field_entries["result_info"] = result_box

        footer = customtkinter.CTkFrame(parent, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        footer.grid_columnconfigure(0, weight=1)

        actions = customtkinter.CTkFrame(footer, fg_color="transparent")
        actions.grid(row=0, column=0, sticky="ew")
        actions.grid_columnconfigure((0, 1), weight=1)

        customtkinter.CTkButton(
            actions,
            text="批准",
            height=40,
            fg_color=SUCCESS_BTN_FG,
            hover_color=SUCCESS_BTN_HOVER,
            text_color=SUCCESS_BTN_TEXT,
            command=self._on_approve_entry,
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        self.remove_toggle_button = customtkinter.CTkButton(
            actions,
            text="移除",
            height=40,
            fg_color=DANGER_BTN_FG,
            hover_color=DANGER_BTN_HOVER,
            text_color=DANGER_BTN_TEXT,
            command=self._on_toggle_remove_entry,
        )
        self.remove_toggle_button.grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self.export_excel_button = customtkinter.CTkButton(
            footer,
            text="导出 Excel (0)",
            height=40,
            fg_color=SECONDARY_BTN_FG,
            hover_color=SECONDARY_BTN_HOVER,
            text_color=SECONDARY_BTN_TEXT,
            command=self._on_export_excel,
        )
        self.export_excel_button.grid(row=1, column=0, sticky="ew", pady=(10, 0))

        self.autofill_button = customtkinter.CTkButton(
            footer,
            text="自动填写 (0)",
            height=44,
            font=customtkinter.CTkFont(size=14, weight="bold"),
            fg_color=PRIMARY_BTN_FG,
            hover_color=PRIMARY_BTN_HOVER,
            text_color=PRIMARY_BTN_TEXT,
            command=self._on_master_autofill,
        )
        self.autofill_button.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        self.autofill_status_label = customtkinter.CTkLabel(
            footer,
            text="批准证书后计入填写队列",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTENT_WRAP,
            justify="left",
            anchor="w",
        )
        self.autofill_status_label.grid(row=3, column=0, sticky="ew", pady=(10, 0))

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
        widget.delete(0, "end")
        if value:
            widget.insert(0, value)

    def _clear_approve_fields(self):
        for widget in self.field_entries.values():
            self._set_field_widget_value(widget, "")

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
        if not hasattr(self, "review_cert_status"):
            return
        path = self._current_cert_path()
        if path is None:
            self.review_cert_status.configure(text="当前：无证书", text_color="gray60")
            self._update_remove_toggle_button()
            return
        if path in self._removed_paths:
            self.review_cert_status.configure(
                text="当前：已移除",
                text_color=("#c0392b", "#e74c3c"),
            )
        elif path in self._autofill_queue:
            pos = self._autofill_queue.index(path) + 1
            self.review_cert_status.configure(
                text=f"当前：已批准 · 队列第 {pos}/{len(self._autofill_queue)} 份",
                text_color=("#2d8a4e", "#3dd68c"),
            )
        else:
            self.review_cert_status.configure(text="当前：未批准", text_color="gray60")
        self._update_remove_toggle_button()

    def _update_remove_toggle_button(self):
        if not hasattr(self, "remove_toggle_button"):
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
        self.autofill_button.configure(text=f"自动填写 ({n})")
        if hasattr(self, "export_excel_button"):
            self.export_excel_button.configure(text=f"导出 Excel ({n})")
        if n == 0:
            self.autofill_status_label.configure(text="批准证书后计入填写队列")
        else:
            self.autofill_status_label.configure(
                text=f"队列中共 {n} 份已批准证书，点击主按钮开始自动填写"
            )
        self._refresh_doc_list_marks()
        self._update_review_cert_status()

    def _save_fields_before_navigate(self):
        if self._current_step == "review" and hasattr(self, "field_entries"):
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

    def _on_open_settings(self):
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

    def _cert_nav_text(self) -> str:
        total = len(self._imported_files)
        if total == 0:
            return "0/0"
        return f"{self._current_cert_index + 1}/{total}"

    def _update_cert_nav_labels(self):
        if hasattr(self, "cert_nav_label"):
            self.cert_nav_label.configure(text=self._cert_nav_text())

    def _on_approve_entry(self):
        path = self._current_cert_path()
        if path is None:
            self.set_status("没有可批准的证书")
            return
        self._save_current_fields_to_result()
        self._removed_paths.discard(path)
        if path not in self._autofill_queue:
            self._autofill_queue.append(path)
        self._update_autofill_button()
        self.set_status(f"已批准，队列 {len(self._autofill_queue)} 份")
        for i in range(self._current_cert_index + 1, len(self._imported_files)):
            candidate = self._imported_files[i]
            if candidate not in self._autofill_queue and candidate not in self._removed_paths:
                self._current_cert_index = i
                self._sync_cert_index_to_list()
                self._load_approve_fields_for_current()
                return
        self._update_review_cert_status()

    def _on_toggle_remove_entry(self):
        path = self._current_cert_path()
        if path is None:
            self.set_status("没有可操作的证书")
            self.show_success_toast("没有可操作的证书。", title="人工核对")
            return

        if path in self._removed_paths:
            self._removed_paths.discard(path)
            self._update_autofill_button()
            self._update_review_cert_status()
            name = Path(path).name
            msg = f"已撤销移除 · {name}"
            self.set_status(msg)
            self.show_success_toast(msg, title="人工核对")
            return

        self._pending_remove_path = path
        name = Path(path).name
        self.set_status(f"即将移除：{name}")
        self.show_toast(
            f"即将移除：{name}\n倒计时结束后移出核对队列，可点撤销保留。",
            title="人工核对",
            action_text="立即移除",
            undo_text="撤销",
            on_undo=self._undo_pending_remove,
            on_complete=self._commit_pending_remove,
        )

    def _undo_pending_remove(self):
        path = self._pending_remove_path
        self._pending_remove_path = None
        name = Path(path).name if path else ""
        msg = f"已撤销移除 · {name}" if name else "已撤销移除"
        self.set_status(msg)

    def _commit_pending_remove(self):
        path = self._pending_remove_path
        self._pending_remove_path = None
        if not path:
            self.show_success_toast("没有需要移除的证书。", title="人工核对")
            return
        if path not in self._imported_files:
            self.show_success_toast("没有需要移除的证书。", title="人工核对")
            return

        self._removed_paths.add(path)
        if path in self._autofill_queue:
            self._autofill_queue.remove(path)
        self._update_autofill_button()
        self._update_review_cert_status()
        name = Path(path).name
        msg = f"已移出核对队列 · {name}"
        self.set_status(msg)
        self.show_success_toast(msg, title="人工核对")

    def _on_master_autofill(self):
        """Approve-queue → Excel in exports/ → Playwright MAS batch import + fill."""
        self._save_fields_before_navigate()
        n = len(self._autofill_queue)
        if n == 0:
            self.autofill_status_label.configure(text="队列为空，请先批准至少一份证书")
            self.set_status("自动填写：队列为空")
            return
        if self._autofill_busy:
            self.autofill_status_label.configure(text="自动填写进行中，请稍候…")
            return

        items: list[AutofillItem] = []
        for path in self._autofill_queue:
            result = self._parse_results.get(path)
            if result is None:
                continue
            items.append(AutofillItem(fields=result.fields, pdf_path=path))
        if not items:
            self.autofill_status_label.configure(text="队列中没有可填写的解析结果")
            return

        excel_rows = self._export_rows()
        excel_headers = [label for _key, label in EXPORT_COLUMNS]
        excel_path = next_export_path()

        try:
            write_batch_excel(excel_rows, excel_headers, excel_path)
        except Exception as exc:  # noqa: BLE001
            self.autofill_status_label.configure(text=f"生成 Excel 失败：{exc}")
            self.set_status(f"自动填写失败：{exc}")
            return

        self._autofill_busy = True
        self.autofill_button.configure(state="disabled")
        self.export_excel_button.configure(state="disabled")
        self.autofill_status_label.configure(
            text=f"已生成 {excel_path.name}，正在启动自动填写…"
        )
        self.set_status(f"自动填写开始：{len(items)} 份 → {excel_path.name}")

        username, password = self._eams_credentials()
        if not username or not password:
            username, password = load_credentials()
        if username and password:
            try:
                save_credentials(username, password)
            except Exception:  # noqa: BLE001
                pass

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
                    upload_pdf=True,
                    submit_workflow=False,
                    status=lambda msg: self.after(0, lambda m=msg: self._on_autofill_progress(m)),
                )
                self.after(0, lambda: self._on_autofill_done(report, excel_path))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: self._on_autofill_fail(str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_autofill_progress(self, message: str):
        self.autofill_status_label.configure(text=message)
        self.set_status(message)

    def _on_autofill_done(self, report, excel_path: Path | None = None):
        self._autofill_busy = False
        if hasattr(self, "autofill_button"):
            self.autofill_button.configure(state="normal")
        if hasattr(self, "export_excel_button"):
            self.export_excel_button.configure(state="normal")
        err_n = len(report.errors or [])
        excel_note = f" · {excel_path.name}" if excel_path else ""
        summary = (
            f"自动填写完成{excel_note}"
            f" · 导入Excel {'是' if report.imported_excel else '否'}"
            f" · 填写 {report.filled} · 附件 {report.uploaded}"
        )
        if err_n:
            summary += f" · 失败 {err_n}"
            if report.errors:
                summary += f"：{report.errors[0]}"
        self.autofill_status_label.configure(text=summary)
        self.set_status(summary)

    def _on_autofill_fail(self, message: str):
        self._autofill_busy = False
        if hasattr(self, "autofill_button"):
            self.autofill_button.configure(state="normal")
        if hasattr(self, "export_excel_button"):
            self.export_excel_button.configure(state="normal")
        self.autofill_status_label.configure(text=f"自动填写失败：{message}")
        self.set_status(f"自动填写失败：{message}")

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
        """Save approved queue Excel into project exports/ (no save dialog)."""
        self._save_fields_before_navigate()
        if self._autofill_busy:
            self.autofill_status_label.configure(text="自动填写进行中，请稍候再导出…")
            return
        rows = self._export_rows()
        if not rows:
            self.autofill_status_label.configure(text="没有可导出的已批准证书")
            self.set_status("导出 Excel：队列为空")
            return

        target = next_export_path()
        try:
            write_batch_excel(
                rows,
                [label for _key, label in EXPORT_COLUMNS],
                target,
            )
        except Exception as exc:  # noqa: BLE001
            self.autofill_status_label.configure(text=f"导出失败：{exc}")
            self.set_status(f"导出 Excel 失败：{exc}")
            return

        self.autofill_status_label.configure(
            text=f"已导出 {len(rows)} 份到 exports/{target.name}"
        )
        self.set_status(f"已导出 Excel：exports/{target.name}")

    def _on_close(self):
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()

"""
VinCert — certificate OCR / parse desktop app.
"""

from __future__ import annotations

from pathlib import Path

import customtkinter
from tkinter import filedialog

from vincert.models import CertificateFields, ParseResult
from vincert.pipeline import parse_certificate
from vincert.folder_import import find_pdfs_in_folder

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
BUILD_VERSION = "v0.2"
BUILD_DATE = "29/07/2026"

STEPS = [
    ("extract", "批量提取", "📂"),
    ("review", "核对填写", "✍️"),
]

# Sidebar fixed chrome above/below the vertically centered step tiles.
SIDEBAR_TOP_HEIGHT = 90
SIDEBAR_BOTTOM_HEIGHT = 5 + 5 + SETTINGS_BTN_HEIGHT + 100
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
SECONDARY_BTN_HOVER = ("#b4bec8", "#4a525a")
SECONDARY_BTN_TEXT = ("gray10", "gray90")
DOC_ROW_ACTIVE = ("#3b8ed0", "#1f6aa5")

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
        ).grid(row=7, column=0, padx=STEP_TILE_PADX, pady=(0, 18), sticky="ew")

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
        ]:
            frame = customtkinter.CTkFrame(self.controls_body, fg_color="transparent")
            frame.grid(row=0, column=0, sticky="nsew")
            frame.grid_columnconfigure(0, weight=1)
            builder(frame)
            self.step_views[key] = frame

    def show_step(self, key: str):
        self._current_step = key

        step_index = next(i for i, (k, _, _) in enumerate(STEPS) if k == key)
        self._update_step_progress(step_index)
        self._update_step_tiles(key)

        titles = {
            "extract": "批量提取",
            "review": "核对填写",
        }
        self.controls_header.configure(text=titles[key])

        for name, frame in self.step_views.items():
            if name == key:
                frame.tkraise()

        if key == "review":
            self._load_approve_fields_for_current()
            self._update_autofill_button()

    def set_status(self, message: str):
        print(f"[VinCert] {message}")

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
            command=self._on_next_certificate,
        ).grid(row=0, column=2, padx=(6, 0))

        return nav_label

    # ---------------------------------------------------------------- extract
    def _build_extract_controls(self, parent: customtkinter.CTkFrame):
        customtkinter.CTkLabel(
            parent,
            text="从左侧文档列表点选证书，核对提取结果后前往填写。",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTENT_WRAP,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        # Parsed fields — match keys for webpage verification + autofill targets
        customtkinter.CTkLabel(
            parent,
            text="比对字段",
            font=customtkinter.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(0, 2))

        customtkinter.CTkLabel(
            parent,
            text="用于与网页元素比对，确保填写到正确记录",
            font=customtkinter.CTkFont(size=11),
            text_color="gray60",
            anchor="w",
            wraplength=CONTENT_WRAP,
            justify="left",
        ).grid(row=2, column=0, sticky="ew", pady=(0, 6))

        self.extract_match_frame = customtkinter.CTkFrame(parent, fg_color="transparent")
        self.extract_match_frame.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        self.extract_match_frame.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            parent,
            text="填写字段",
            font=customtkinter.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=4, column=0, sticky="ew", pady=(0, 6))

        self.extract_autofill_frame = customtkinter.CTkFrame(parent, fg_color="transparent")
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
                )
                entry.grid(row=row, column=0, sticky="ew", padx=(104, 0), pady=4)
                self.extract_field_entries[key] = entry

        self.extract_errors_label = customtkinter.CTkLabel(
            parent,
            text="",
            anchor="w",
            justify="left",
            wraplength=CONTENT_WRAP,
            font=customtkinter.CTkFont(size=11),
            text_color="#c0392b",
        )
        self.extract_errors_label.grid(row=6, column=0, sticky="ew", pady=(0, 6))

        self.extract_status_label = customtkinter.CTkLabel(
            parent,
            text="等待选择文件夹",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTENT_WRAP,
            justify="left",
            anchor="w",
        )
        self.extract_status_label.grid(row=7, column=0, sticky="ew", pady=(0, 8))

        customtkinter.CTkButton(
            parent,
            text="前往核对填写 →",
            height=40,
            command=self._go_to_review,
        ).grid(row=8, column=0, sticky="ew")

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

        # Parse on the main thread — Tk after() from worker threads is unreliable on macOS.
        self._extract_busy = True
        results: dict[str, ParseResult] = {}
        try:
            for i, path in enumerate(paths, start=1):
                self.extract_status_label.configure(text=f"解析进度 {i}/{len(paths)}")
                self.update_idletasks()
                results[path] = parse_certificate(path)
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
        self._current_cert_index = 0
        ok = sum(1 for r in results.values() if r.ok)
        self.extract_status_label.configure(text=f"已加载 {len(paths)} 份 · 解析成功 {ok}")
        self._rebuild_doc_list()
        self._update_cert_nav_labels()
        if paths:
            self._select_document(paths[0])
        self.set_status(f"文件夹加载完成：{len(paths)} PDF")

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

        for i, path in enumerate(self._imported_files):
            btn = customtkinter.CTkButton(
                self.doc_list_frame,
                text=self._doc_list_label(path, i),
                anchor="w",
                height=32,
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=TILE_BG_HOVER,
                command=lambda p=path: self._select_document(p),
            )
            btn.grid(row=i, column=0, sticky="ew", pady=2)
            self._doc_buttons[path] = btn

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
        customtkinter.CTkLabel(
            parent,
            text="核对识别结果；批准后计入自动填写队列，可用主按钮批量写入网页。",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTENT_WRAP,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        self.cert_nav_label = self._build_cert_nav_row(parent, row=1)

        self.review_cert_status = customtkinter.CTkLabel(
            parent,
            text="当前：未批准",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            anchor="w",
        )
        self.review_cert_status.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        customtkinter.CTkLabel(
            parent,
            text="比对字段",
            font=customtkinter.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).grid(row=3, column=0, sticky="ew", pady=(0, 4))

        customtkinter.CTkLabel(
            parent,
            text="与网页元素比对，确保填写到正确记录",
            font=customtkinter.CTkFont(size=11),
            text_color="gray60",
            anchor="w",
            wraplength=CONTENT_WRAP,
            justify="left",
        ).grid(row=4, column=0, sticky="ew", pady=(0, 6))

        self.field_entries: dict[str, customtkinter.CTkEntry] = {}
        for row, (key, label) in enumerate(MATCH_FIELDS, start=5):
            customtkinter.CTkLabel(
                parent,
                text=label,
                anchor="w",
                width=96,
                font=customtkinter.CTkFont(size=12),
                text_color="gray60",
            ).grid(row=row, column=0, sticky="w", pady=4)

            entry = customtkinter.CTkEntry(
                parent,
                placeholder_text=f"请输入{label}",
                border_width=0,
            )
            entry.grid(row=row, column=0, sticky="ew", padx=(104, 0), pady=4)
            self.field_entries[key] = entry

        fill_header_row = 5 + len(MATCH_FIELDS)
        customtkinter.CTkLabel(
            parent,
            text="填写字段",
            font=customtkinter.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).grid(row=fill_header_row, column=0, sticky="ew", pady=(10, 8))

        for offset, (key, label) in enumerate(REVIEW_FILL_FIELDS):
            row = fill_header_row + 1 + offset
            customtkinter.CTkLabel(
                parent,
                text=label,
                anchor="w",
                width=96,
                font=customtkinter.CTkFont(size=12),
                text_color="gray60",
            ).grid(row=row, column=0, sticky="w", pady=4)

            entry = customtkinter.CTkEntry(
                parent,
                placeholder_text=f"请输入{label}",
                border_width=0,
            )
            entry.grid(row=row, column=0, sticky="ew", padx=(104, 0), pady=4)
            self.field_entries[key] = entry

        action_row = fill_header_row + 1 + len(REVIEW_FILL_FIELDS)
        actions = customtkinter.CTkFrame(parent, fg_color="transparent")
        actions.grid(row=action_row, column=0, sticky="ew", pady=(16, 0))
        actions.grid_columnconfigure((0, 1), weight=1)

        customtkinter.CTkButton(
            actions,
            text="批准",
            height=40,
            fg_color="#2d8a4e",
            hover_color="#267a44",
            command=self._on_approve_entry,
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        self.remove_toggle_button = customtkinter.CTkButton(
            actions,
            text="移除",
            height=40,
            fg_color=SECONDARY_BTN_FG,
            hover_color=SECONDARY_BTN_HOVER,
            text_color=SECONDARY_BTN_TEXT,
            command=self._on_toggle_remove_entry,
        )
        self.remove_toggle_button.grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self.autofill_button = customtkinter.CTkButton(
            parent,
            text="自动填写 (0)",
            height=44,
            font=customtkinter.CTkFont(size=14, weight="bold"),
            command=self._on_master_autofill,
        )
        self.autofill_button.grid(row=action_row + 1, column=0, sticky="ew", pady=(16, 0))

        self.export_excel_button = customtkinter.CTkButton(
            parent,
            text="导出 Excel (0)",
            height=40,
            fg_color=SECONDARY_BTN_FG,
            hover_color=SECONDARY_BTN_HOVER,
            text_color=SECONDARY_BTN_TEXT,
            command=self._on_export_excel,
        )
        self.export_excel_button.grid(row=action_row + 2, column=0, sticky="ew", pady=(10, 0))

        self.autofill_status_label = customtkinter.CTkLabel(
            parent,
            text="批准证书后计入填写队列",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTENT_WRAP,
            justify="left",
            anchor="w",
        )
        self.autofill_status_label.grid(row=action_row + 3, column=0, sticky="ew", pady=(10, 0))

    def _clear_approve_fields(self):
        for entry in self.field_entries.values():
            entry.delete(0, "end")

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
                setattr(fields, key, self.field_entries[key].get().strip())
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
        for key, entry in self.field_entries.items():
            value = getattr(fields, key, "") or ""
            if not value and key == "result_info":
                value = DEFAULT_RESULT_INFO
            entry.insert(0, value)
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
            self.remove_toggle_button.configure(text="撤销移除")
        else:
            self.remove_toggle_button.configure(text="移除")

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
        pass

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
            return
        if path in self._removed_paths:
            self._removed_paths.discard(path)
            self._update_autofill_button()
            self.set_status("已撤销移除")
            return

        self._removed_paths.add(path)
        if path in self._autofill_queue:
            self._autofill_queue.remove(path)
        self._update_autofill_button()
        self.set_status("已移除当前条目")

    def _on_master_autofill(self):
        n = len(self._autofill_queue)
        if n == 0:
            self.autofill_status_label.configure(text="队列为空，请先批准至少一份证书")
            self.set_status("自动填写：队列为空")
            return
        self.autofill_status_label.configure(text=f"正在自动填写 {n} 份…")
        self.set_status(f"自动填写 {n} 份（网页写入待实现）")

    def _export_rows(self) -> list[list[str]]:
        rows: list[list[str]] = []
        for path in self._autofill_queue:
            result = self._parse_results.get(path)
            if result is None:
                continue
            fields = result.fields
            rows.append([getattr(fields, key, "") or "" for key, _label in EXPORT_COLUMNS])
        return rows

    def _default_export_path(self) -> Path:
        base_dir = Path(self._source_folder) if self._source_folder else Path.cwd()
        return base_dir / "vincert_batch_import.xlsx"

    def _on_export_excel(self):
        self._save_fields_before_navigate()
        rows = self._export_rows()
        if not rows:
            self.autofill_status_label.configure(text="没有可导出的已批准证书")
            self.set_status("导出 Excel：队列为空")
            return

        default_path = self._default_export_path()
        target = filedialog.asksaveasfilename(
            parent=self,
            title="导出 Excel",
            initialdir=str(default_path.parent),
            initialfile=default_path.name,
            defaultextension=".xlsx",
            filetypes=[("Excel Workbook", "*.xlsx")],
        )
        if not target:
            self.set_status("已取消导出 Excel")
            return

        try:
            from openpyxl import Workbook  # pyright: ignore[reportMissingModuleSource]

            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "VinCert"
            sheet.append([label for _key, label in EXPORT_COLUMNS])
            for row in rows:
                sheet.append(row)

            for idx, (_key, label) in enumerate(EXPORT_COLUMNS, start=1):
                max_len = len(label)
                for row in rows:
                    max_len = max(max_len, len(str(row[idx - 1])))
                sheet.column_dimensions[chr(64 + idx)].width = min(max_len + 4, 36)

            workbook.save(target)
        except Exception as exc:  # noqa: BLE001
            self.autofill_status_label.configure(text=f"导出失败：{exc}")
            self.set_status(f"导出 Excel 失败：{exc}")
            return

        self.autofill_status_label.configure(text=f"已导出 {len(rows)} 份到 Excel")
        self.set_status(f"已导出 Excel：{Path(target).name}")

    def _on_close(self):
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()

"""
VinCert — certificate OCR / parse desktop app.
"""

from __future__ import annotations

import sys
from pathlib import Path
import tkinter as tk

import customtkinter
from PIL import ImageTk
from tkinter import filedialog

from vincert.models import CertificateFields, ParseResult
from vincert.pdf_io import page_count, render_page
from vincert.pipeline import parse_certificate
from vincert.folder_import import find_pdfs_in_folder

customtkinter.set_appearance_mode("System")
customtkinter.set_default_color_theme("blue")

SIDEBAR_WIDTH = 188
CONTROLS_WIDTH = 360
STEP_TILE_SIZE = 144
STEP_TILE_PADX = 18
SETTINGS_BTN_HEIGHT = 40
BUILD_VERSION = "v0.1"
BUILD_DATE = "21/07/2026"

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
OUTLINE_BTN_HOVER = ("gray70", "gray30")
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

EXTRACT_DISPLAY_FIELDS = MATCH_FIELDS + METROLOGY_FIELDS


class App(customtkinter.CTk):
    def __init__(self):
        super().__init__()

        self.title("VinCert")
        self.geometry("1400x820")

        self._imported_files: list[str] = []
        self._parse_results: dict[str, ParseResult] = {}
        self._autofill_queue: list[str] = []
        self._current_cert_index = 0
        self._current_step = "extract"

        # Extract / preview state
        self._source_folder: str | None = None
        self._preview_path: str | None = None
        self._preview_page_count = 0
        self._preview_photos: list[ImageTk.PhotoImage] = []
        self._doc_buttons: dict[str, customtkinter.CTkButton] = {}
        self._extract_busy = False
        self._pdf_resize_after_id: str | None = None
        self._last_pdf_canvas_width = 0
        self._extract_text_expanded = False

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0, minsize=SIDEBAR_WIDTH)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0, minsize=CONTROLS_WIDTH)

        self._build_sidebar()
        self._build_center_stack()
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
            fg_color="transparent",
            border_width=2,
            text_color=("gray10", "gray90"),
            hover_color=OUTLINE_BTN_HOVER,
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

    # ----------------------------------------------------------- center panels
    def _build_center_stack(self):
        self.center_stack = customtkinter.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.center_stack.grid(row=0, column=1, sticky="nsew")
        self.center_stack.grid_rowconfigure(0, weight=1)
        self.center_stack.grid_columnconfigure(0, weight=1)

        self._build_website_panel()
        self._build_extract_preview_panel()

        self.website_panel.grid(row=0, column=0, sticky="nsew")
        self.extract_preview_panel.grid(row=0, column=0, sticky="nsew")

    def _build_website_panel(self):
        self.website_panel = customtkinter.CTkFrame(
            self.center_stack, corner_radius=0, fg_color="transparent"
        )
        self.website_panel.grid_rowconfigure(1, weight=1)
        self.website_panel.grid_columnconfigure(0, weight=1)

        toolbar = customtkinter.CTkFrame(self.website_panel, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        toolbar.grid_columnconfigure(1, weight=1)

        customtkinter.CTkLabel(
            toolbar,
            text="目标网页",
            font=customtkinter.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=(0, 10))

        self.url_entry = customtkinter.CTkEntry(
            toolbar,
            placeholder_text="https://example.com/form",
        )
        self.url_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        customtkinter.CTkButton(
            toolbar,
            text="打开",
            width=64,
            command=self._on_open_website,
        ).grid(row=0, column=2, padx=(0, 6))

        customtkinter.CTkButton(
            toolbar,
            text="刷新",
            width=64,
            fg_color="transparent",
            border_width=2,
            text_color=("gray10", "gray90"),
            hover_color=OUTLINE_BTN_HOVER,
            command=self._on_refresh_website,
        ).grid(row=0, column=3)

        browser_frame = customtkinter.CTkFrame(
            self.website_panel, corner_radius=0, fg_color=EMBED_BG_COLOR
        )
        browser_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        browser_frame.grid_rowconfigure(0, weight=1)
        browser_frame.grid_columnconfigure(0, weight=1)

        self.browser_placeholder = customtkinter.CTkLabel(
            browser_frame,
            text="网页嵌入区域\n\n此处将显示浏览器中的目标网站\n填写流程可对照网页进行操作",
            font=customtkinter.CTkFont(size=14),
            text_color="gray50",
            justify="center",
        )
        self.browser_placeholder.grid(row=0, column=0, sticky="nsew")

    def _build_extract_preview_panel(self):
        """Full-width scrollable PDF preview."""
        self.extract_preview_panel = customtkinter.CTkFrame(
            self.center_stack, corner_radius=0, fg_color="transparent"
        )
        self.extract_preview_panel.grid_rowconfigure(1, weight=1)
        self.extract_preview_panel.grid_columnconfigure(0, weight=1)

        header = customtkinter.CTkFrame(self.extract_preview_panel, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        header.grid_columnconfigure(1, weight=1)

        customtkinter.CTkLabel(
            header,
            text="PDF 预览",
            font=customtkinter.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=(0, 12))

        self.preview_file_label = customtkinter.CTkLabel(
            header,
            text="未选择文件",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            anchor="w",
        )
        self.preview_file_label.grid(row=0, column=1, sticky="ew")

        self.preview_page_label = customtkinter.CTkLabel(
            header,
            text="0 页",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
        )
        self.preview_page_label.grid(row=0, column=2, padx=(8, 0))

        left = customtkinter.CTkFrame(self.extract_preview_panel, fg_color=EMBED_BG_COLOR)
        left.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)
        self.pdf_preview_shell = left

        canvas_bg = customtkinter.ThemeManager.theme["CTkFrame"]["fg_color"]
        if isinstance(canvas_bg, (tuple, list)):
            canvas_bg = canvas_bg[1 if customtkinter.get_appearance_mode() == "Dark" else 0]

        self.pdf_viewport = customtkinter.CTkFrame(left, fg_color="transparent")
        self.pdf_viewport.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.pdf_viewport.grid_rowconfigure(0, weight=1)
        self.pdf_viewport.grid_columnconfigure(0, weight=1)

        self.pdf_canvas = tk.Canvas(
            self.pdf_viewport,
            highlightthickness=0,
            bg=canvas_bg,
            # Pixel units → trackpad deltas map 1:1 for smoother feel.
            yscrollincrement=1,
            xscrollincrement=1,
        )
        self.pdf_vscroll = customtkinter.CTkScrollbar(
            self.pdf_viewport,
            orientation="vertical",
            command=self.pdf_canvas.yview,
        )
        self.pdf_canvas.configure(yscrollcommand=self.pdf_vscroll.set)
        self.pdf_canvas.grid(row=0, column=0, sticky="nsew")
        self.pdf_vscroll.grid(row=0, column=1, sticky="ns")
        self.pdf_canvas.bind("<Configure>", self._on_pdf_canvas_configure)

        self._draw_pdf_placeholder(
            "选择文件夹并点选文档列表中的证书后，\n在此滚动预览全部页面"
        )

        self.bind_all("<MouseWheel>", self._on_pdf_preview_wheel, add="+")
        self.bind_all("<Button-4>", self._on_pdf_preview_wheel, add="+")
        self.bind_all("<Button-5>", self._on_pdf_preview_wheel, add="+")

    def _build_controls_panel(self):
        self.controls_panel = customtkinter.CTkFrame(
            self, width=CONTROLS_WIDTH, corner_radius=0, fg_color=APP_BG_COLOR
        )
        self.controls_panel.grid(row=0, column=2, sticky="nsew", padx=0)
        self.controls_panel.grid_propagate(False)
        self.controls_panel.grid_rowconfigure(1, weight=1)
        self.controls_panel.grid_columnconfigure(0, weight=1)

        self.controls_header = customtkinter.CTkLabel(
            self.controls_panel,
            text="",
            font=customtkinter.CTkFont(size=16, weight="bold"),
            anchor="w",
        )
        self.controls_header.grid(row=0, column=0, padx=16, pady=(16, 8), sticky="ew")

        self.controls_body = customtkinter.CTkFrame(self.controls_panel, fg_color="transparent")
        self.controls_body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
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
            # Let extract list grow
            if key == "extract":
                frame.grid_rowconfigure(4, weight=1)
                frame.grid_rowconfigure(7, weight=0)
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

        if key == "extract":
            self.extract_preview_panel.tkraise()
        else:
            self.website_panel.tkraise()

        if key == "review":
            self._update_autofill_button()

    def set_status(self, message: str):
        print(f"[VinCert] {message}")

    def _apply_min_window_size(self):
        min_width = SIDEBAR_WIDTH + CONTROLS_WIDTH + 520
        self.minsize(min_width, SIDEBAR_MIN_HEIGHT)

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
            text="选择含证书 PDF 的文件夹（仅根目录），点选文档预览与核对提取结果。",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTROLS_WIDTH - 48,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        btn_row = customtkinter.CTkFrame(parent, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        btn_row.grid_columnconfigure((0, 1), weight=1)

        customtkinter.CTkButton(
            btn_row,
            text="选择文件夹…",
            command=self._pick_folder,
        ).grid(row=0, column=0, padx=(0, 6), sticky="ew")

        customtkinter.CTkButton(
            btn_row,
            text="清空",
            fg_color="transparent",
            border_width=2,
            text_color=("gray10", "gray90"),
            hover_color=OUTLINE_BTN_HOVER,
            command=self._clear_extract,
        ).grid(row=0, column=1, padx=(6, 0), sticky="ew")

        self.folder_label = customtkinter.CTkLabel(
            parent,
            text="未选择文件夹",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            anchor="w",
            wraplength=CONTROLS_WIDTH - 48,
            justify="left",
        )
        self.folder_label.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        customtkinter.CTkLabel(
            parent,
            text="文档列表",
            font=customtkinter.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).grid(row=3, column=0, sticky="ew", pady=(0, 6))

        self.doc_list_frame = customtkinter.CTkScrollableFrame(parent, height=180)
        self.doc_list_frame.grid(row=4, column=0, sticky="nsew", pady=(0, 10))
        self.doc_list_frame.grid_columnconfigure(0, weight=1)

        self.doc_list_empty = customtkinter.CTkLabel(
            self.doc_list_frame,
            text="文件夹根目录下的 PDF 将显示在这里",
            text_color="gray50",
        )
        self.doc_list_empty.grid(row=0, column=0, pady=20)

        # Parsed fields — match keys for webpage verification + autofill targets
        customtkinter.CTkLabel(
            parent,
            text="比对字段",
            font=customtkinter.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=5, column=0, sticky="ew", pady=(0, 2))

        customtkinter.CTkLabel(
            parent,
            text="用于与网页元素比对，确保填写到正确记录",
            font=customtkinter.CTkFont(size=11),
            text_color="gray60",
            anchor="w",
            wraplength=CONTROLS_WIDTH - 48,
            justify="left",
        ).grid(row=6, column=0, sticky="ew", pady=(0, 6))

        self.extract_match_frame = customtkinter.CTkFrame(parent, fg_color="transparent")
        self.extract_match_frame.grid(row=7, column=0, sticky="ew", pady=(0, 10))
        self.extract_match_frame.grid_columnconfigure(1, weight=1)

        customtkinter.CTkLabel(
            parent,
            text="填写字段",
            font=customtkinter.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=8, column=0, sticky="ew", pady=(0, 6))

        self.extract_autofill_frame = customtkinter.CTkFrame(parent, fg_color="transparent")
        self.extract_autofill_frame.grid(row=9, column=0, sticky="ew", pady=(0, 8))
        self.extract_autofill_frame.grid_columnconfigure(1, weight=1)

        self.extract_field_values: dict[str, customtkinter.CTkLabel] = {}
        field_wrap = CONTROLS_WIDTH - 48 - 104
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
                    font=customtkinter.CTkFont(size=11),
                    text_color="gray60",
                ).grid(row=row, column=0, sticky="nw", pady=2)

                value_label = customtkinter.CTkLabel(
                    frame,
                    text="—",
                    anchor="w",
                    justify="left",
                    wraplength=field_wrap,
                    font=customtkinter.CTkFont(size=12),
                )
                value_label.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
                self.extract_field_values[key] = value_label

        self.extract_errors_label = customtkinter.CTkLabel(
            parent,
            text="",
            anchor="w",
            justify="left",
            wraplength=CONTROLS_WIDTH - 48,
            font=customtkinter.CTkFont(size=11),
            text_color="#c0392b",
        )
        self.extract_errors_label.grid(row=10, column=0, sticky="ew", pady=(0, 6))

        # Collapsible raw text — below parsed fields
        self.extract_text_section = customtkinter.CTkFrame(parent, fg_color="transparent")
        self.extract_text_section.grid(row=11, column=0, sticky="ew", pady=(0, 8))
        self.extract_text_section.grid_columnconfigure(0, weight=1)

        text_header = customtkinter.CTkFrame(self.extract_text_section, fg_color="transparent")
        text_header.grid(row=0, column=0, sticky="ew")
        text_header.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            text_header,
            text="提取文本（封面页）",
            font=customtkinter.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        self.extract_text_toggle = customtkinter.CTkButton(
            text_header,
            text="▶",
            width=28,
            height=24,
            fg_color="transparent",
            hover_color=OUTLINE_BTN_HOVER,
            command=self._toggle_extract_text,
        )
        self.extract_text_toggle.grid(row=0, column=1, padx=(6, 0))

        self.extract_text_body = customtkinter.CTkFrame(self.extract_text_section, fg_color="transparent")
        self.extract_text_body.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.extract_text_body.grid_columnconfigure(0, weight=1)

        self.extract_text_box = customtkinter.CTkTextbox(
            self.extract_text_body,
            height=72,
            font=customtkinter.CTkFont(family="Courier", size=11),
        )
        self.extract_text_box.grid(row=0, column=0, sticky="ew")
        self.extract_text_body.grid_remove()

        customtkinter.CTkButton(
            parent,
            text="全部重新解析",
            height=36,
            fg_color="transparent",
            border_width=2,
            text_color=("gray10", "gray90"),
            hover_color=OUTLINE_BTN_HOVER,
            command=self._reparse_all,
        ).grid(row=12, column=0, sticky="ew", pady=(0, 8))

        self.extract_status_label = customtkinter.CTkLabel(
            parent,
            text="等待选择文件夹",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTROLS_WIDTH - 48,
            justify="left",
            anchor="w",
        )
        self.extract_status_label.grid(row=13, column=0, sticky="ew", pady=(0, 8))

        customtkinter.CTkButton(
            parent,
            text="前往核对填写 →",
            height=40,
            command=self._go_to_review,
        ).grid(row=14, column=0, sticky="ew")

    def _toggle_extract_text(self):
        self._extract_text_expanded = not self._extract_text_expanded
        if self._extract_text_expanded:
            self.extract_text_body.grid()
            self.extract_text_toggle.configure(text="▼")
        else:
            self.extract_text_body.grid_remove()
            self.extract_text_toggle.configure(text="▶")

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
        self._current_cert_index = 0
        self._preview_path = None
        self._clear_pdf_preview()
        self._rebuild_doc_list()
        self.folder_label.configure(text="未选择文件夹")
        self.preview_file_label.configure(text="未选择文件")
        self.preview_page_label.configure(text="0 页")
        self.extract_text_box.delete("1.0", "end")
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
        self._current_cert_index = 0
        ok = sum(1 for r in results.values() if r.ok)
        self.extract_status_label.configure(text=f"已加载 {len(paths)} 份 · 解析成功 {ok}")
        self._rebuild_doc_list()
        self._update_cert_nav_labels()
        if paths:
            self._select_document(paths[0])
        self.set_status(f"文件夹加载完成：{len(paths)} PDF")

    def _reparse_all(self):
        if not self._imported_files:
            self.extract_status_label.configure(text="没有可解析的文档")
            return
        if self._extract_busy:
            self.extract_status_label.configure(text="正在处理中，请稍候…")
            return

        paths = list(self._imported_files)
        self._extract_busy = True
        results: dict[str, ParseResult] = {}
        try:
            for i, path in enumerate(paths, start=1):
                self.extract_status_label.configure(text=f"解析进度 {i}/{len(paths)}")
                self.update_idletasks()
                results[path] = parse_certificate(path)
            self._on_reparse_done(results)
        except Exception as exc:  # noqa: BLE001
            self.extract_status_label.configure(text=f"失败：{exc}")
            self.set_status(f"重新解析失败：{exc}")
        finally:
            self._extract_busy = False

    def _on_reparse_done(self, results: dict[str, ParseResult]):
        self._extract_busy = False
        self._parse_results.update(results)
        ok = sum(1 for r in results.values() if r.ok)
        self.extract_status_label.configure(text=f"重新解析完成 · 成功 {ok}/{len(results)}")
        self._rebuild_doc_list()
        if self._preview_path:
            self._show_parse_result(self._preview_path)

    def _rebuild_doc_list(self):
        for child in self.doc_list_frame.winfo_children():
            child.destroy()
        self._doc_buttons.clear()

        if not self._imported_files:
            self.doc_list_empty = customtkinter.CTkLabel(
                self.doc_list_frame,
                text="文件夹根目录下的 PDF 将显示在这里",
                text_color="gray50",
            )
            self.doc_list_empty.grid(row=0, column=0, pady=20)
            return

        for i, path in enumerate(self._imported_files):
            name = Path(path).name
            result = self._parse_results.get(path)
            mark = "✓" if result and result.ok else "·"
            label = f"{mark}  {i + 1}. {name}"
            btn = customtkinter.CTkButton(
                self.doc_list_frame,
                text=label,
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

    def _highlight_selected_doc(self):
        for path, btn in self._doc_buttons.items():
            if path == self._preview_path:
                btn.configure(fg_color=DOC_ROW_ACTIVE, text_color=("white", "white"))
            else:
                btn.configure(fg_color="transparent", text_color=("gray10", "gray90"))

    def _select_document(self, path: str):
        if path not in self._imported_files:
            return
        self._preview_path = path
        self._current_cert_index = self._imported_files.index(path)
        self._update_cert_nav_labels()
        self._highlight_selected_doc()
        self.preview_file_label.configure(text=Path(path).name)
        self._show_parse_result(path)
        self._render_all_pages(path)

    def _clear_extract_fields_display(self):
        for label in self.extract_field_values.values():
            label.configure(text="—")
        self.extract_errors_label.configure(text="")

    def _show_parse_result(self, path: str):
        result = self._parse_results.get(path)
        if result is None:
            result = parse_certificate(path)
            self._parse_results[path] = result

        self.extract_text_box.delete("1.0", "end")
        display_text = "\n".join(result.lines) if result.lines else result.raw_text
        self.extract_text_box.insert("1.0", display_text or "（无文本）")

        fields = result.fields
        for key, label_widget in self.extract_field_values.items():
            value = getattr(fields, key, "") or "—"
            label_widget.configure(text=value)

        if result.errors:
            self.extract_errors_label.configure(text="⚠ " + "\n".join(result.errors))
        else:
            self.extract_errors_label.configure(text="")

    def _on_pdf_preview_wheel(self, event):
        if self._current_step != "extract":
            return
        widget_under = self.winfo_containing(event.x_root, event.y_root)
        if widget_under is None or not self._is_descendant(
            widget_under, self.pdf_preview_shell
        ):
            return
        if self.pdf_canvas.yview() == (0.0, 1.0):
            return

        if event.num == 4:
            pixels = -120
        elif event.num == 5:
            pixels = 120
        else:
            delta = event.delta
            if delta == 0:
                return
            if sys.platform == "darwin":
                # Trackpad deltas are small (±1–10); amplify for tall PDF pages.
                pixels = -delta * 5
            else:
                pixels = int(-delta / 120) * 96

        self.pdf_canvas.yview_scroll(pixels, "units")
        return "break"

    def _pdf_preview_width(self) -> int:
        self.pdf_canvas.update_idletasks()
        width = self.pdf_canvas.winfo_width()
        if width <= 1:
            width = self.pdf_viewport.winfo_width()
        return max(240, width - 24)

    def _pdf_caption_color(self) -> str:
        mode = customtkinter.get_appearance_mode()
        return "#9a9a9a" if mode == "Dark" else "#6b6b6b"

    def _draw_pdf_placeholder(self, text: str, *, color: str | None = None):
        self.pdf_canvas.delete("all")
        self._preview_photos.clear()
        self.pdf_canvas.update_idletasks()
        width = max(self.pdf_canvas.winfo_width(), 240)
        height = max(self.pdf_canvas.winfo_height(), 200)
        self.pdf_canvas.create_text(
            width // 2,
            max(80, height // 3),
            text=text,
            fill=color or self._pdf_caption_color(),
            font=("Helvetica", 13),
            justify="center",
            width=max(200, width - 48),
            tags=("placeholder",),
        )
        self.pdf_canvas.configure(scrollregion=(0, 0, width, height))

    def _on_pdf_canvas_configure(self, event):
        if event.width <= 1:
            return
        # Keep placeholder text centered when empty.
        if self.pdf_canvas.find_withtag("placeholder"):
            self.pdf_canvas.coords(
                "placeholder",
                event.width // 2,
                max(80, event.height // 3),
            )
            self.pdf_canvas.itemconfigure(
                "placeholder", width=max(200, event.width - 48)
            )
            self.pdf_canvas.configure(
                scrollregion=(0, 0, event.width, max(event.height, 200))
            )
        if not self._preview_path:
            return
        if abs(event.width - self._last_pdf_canvas_width) < 8:
            return
        self._last_pdf_canvas_width = event.width
        if self._pdf_resize_after_id is not None:
            self.after_cancel(self._pdf_resize_after_id)
        path = self._preview_path
        self._pdf_resize_after_id = self.after(250, lambda: self._render_all_pages(path))

    def _scroll_pdf_to_top(self):
        self.pdf_canvas.yview_moveto(0)

    def _clear_pdf_preview(self):
        self._draw_pdf_placeholder(
            "选择文件夹并点选文档列表中的证书后，\n在此滚动预览全部页面"
        )

    def _render_all_pages(self, path: str):
        """Render every page as canvas images for smooth scrolling."""
        try:
            n = page_count(path)
        except Exception as exc:  # noqa: BLE001
            self._draw_pdf_placeholder(f"预览失败\n{exc}", color="#c0392b")
            self.preview_page_label.configure(text="0 页")
            return

        self._preview_page_count = n
        self.preview_page_label.configure(text=f"{n} 页")
        preview_width = self._pdf_preview_width()

        self.pdf_canvas.delete("all")
        self._preview_photos.clear()
        self.pdf_canvas.create_text(
            preview_width // 2,
            40,
            text="正在渲染页面…",
            fill=self._pdf_caption_color(),
            font=("Helvetica", 13),
            tags=("loading",),
        )
        self.pdf_canvas.configure(scrollregion=(0, 0, preview_width, 80))
        self.update_idletasks()

        y = 12
        rendered = 0
        caption_color = self._pdf_caption_color()
        canvas_width = max(self.pdf_canvas.winfo_width(), preview_width)

        for i in range(n):
            if path != self._preview_path:
                return

            self.pdf_canvas.itemconfigure(
                "loading", text=f"正在渲染页面… {i + 1}/{n}"
            )
            self.update_idletasks()

            try:
                image = render_page(path, i, zoom=2.0, max_width=preview_width)
            except Exception as exc:  # noqa: BLE001
                y += 8
                self.pdf_canvas.create_text(
                    canvas_width // 2,
                    y,
                    text=f"— 第 {i + 1} / {n} 页（渲染失败）—",
                    fill=caption_color,
                    font=("Helvetica", 11),
                    anchor="n",
                )
                y += 22
                self.pdf_canvas.create_text(
                    canvas_width // 2,
                    y,
                    text=str(exc),
                    fill="#c0392b",
                    font=("Helvetica", 11),
                    anchor="n",
                    width=max(200, preview_width - 24),
                )
                y += 36
                continue

            y += 8 if rendered else 4
            self.pdf_canvas.create_text(
                canvas_width // 2,
                y,
                text=f"— 第 {i + 1} / {n} 页 —",
                fill=caption_color,
                font=("Helvetica", 11),
                anchor="n",
            )
            y += 22

            photo = ImageTk.PhotoImage(image)
            self._preview_photos.append(photo)
            self.pdf_canvas.create_image(
                canvas_width // 2,
                y,
                image=photo,
                anchor="n",
            )
            y += image.height + 12
            rendered += 1
            self.pdf_canvas.configure(scrollregion=(0, 0, canvas_width, y + 8))

        self.pdf_canvas.delete("loading")

        if path != self._preview_path:
            return

        if rendered == 0:
            self._draw_pdf_placeholder("未能渲染任何页面")
            return

        self.pdf_canvas.configure(scrollregion=(0, 0, canvas_width, y + 8))
        self._scroll_pdf_to_top()
        self._last_pdf_canvas_width = self.pdf_canvas.winfo_width()

    def _go_to_review(self):
        if not self._imported_files:
            self.extract_status_label.configure(text="请先选择文件夹并完成提取")
            return
        self._load_approve_fields_for_current()
        self.show_step("review")

    # ----------------------------------------------------------- review + fill
    def _build_review_controls(self, parent: customtkinter.CTkFrame):
        customtkinter.CTkLabel(
            parent,
            text="核对识别结果；批准后计入自动填写队列，可用主按钮批量写入网页。",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTROLS_WIDTH - 48,
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
            wraplength=CONTROLS_WIDTH - 48,
            justify="left",
        ).grid(row=4, column=0, sticky="ew", pady=(0, 6))

        self.match_field_labels: dict[str, customtkinter.CTkLabel] = {}
        match_wrap = CONTROLS_WIDTH - 48 - 104
        for row, (key, label) in enumerate(MATCH_FIELDS, start=5):
            customtkinter.CTkLabel(
                parent,
                text=label,
                anchor="w",
                width=96,
                text_color="gray60",
            ).grid(row=row, column=0, sticky="nw", pady=2)

            value_label = customtkinter.CTkLabel(
                parent,
                text="—",
                anchor="w",
                justify="left",
                wraplength=match_wrap,
            )
            value_label.grid(row=row, column=0, sticky="ew", padx=(104, 0), pady=2)
            self.match_field_labels[key] = value_label

        fill_header_row = 5 + len(MATCH_FIELDS)
        customtkinter.CTkLabel(
            parent,
            text="填写字段",
            font=customtkinter.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).grid(row=fill_header_row, column=0, sticky="ew", pady=(10, 8))

        self.field_entries: dict[str, customtkinter.CTkEntry] = {}
        for offset, (key, label) in enumerate(METROLOGY_FIELDS):
            row = fill_header_row + 1 + offset
            customtkinter.CTkLabel(
                parent,
                text=label,
                anchor="w",
                width=96,
            ).grid(row=row, column=0, sticky="w", pady=4)

            entry = customtkinter.CTkEntry(parent, placeholder_text=f"请输入{label}")
            entry.grid(row=row, column=0, sticky="ew", padx=(104, 0), pady=4)
            self.field_entries[key] = entry

        action_row = fill_header_row + 1 + len(METROLOGY_FIELDS)
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

        customtkinter.CTkButton(
            actions,
            text="跳过",
            height=40,
            fg_color="transparent",
            border_width=2,
            text_color=("gray10", "gray90"),
            hover_color=OUTLINE_BTN_HOVER,
            command=self._on_skip_entry,
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        customtkinter.CTkButton(
            actions,
            text="移出队列",
            height=40,
            fg_color="transparent",
            border_width=2,
            text_color=("gray10", "gray90"),
            hover_color=OUTLINE_BTN_HOVER,
            command=self._on_unqueue_entry,
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self.autofill_button = customtkinter.CTkButton(
            parent,
            text="自动填写 (0)",
            height=44,
            font=customtkinter.CTkFont(size=14, weight="bold"),
            command=self._on_master_autofill,
        )
        self.autofill_button.grid(row=action_row + 1, column=0, sticky="ew", pady=(16, 0))

        self.autofill_status_label = customtkinter.CTkLabel(
            parent,
            text="批准证书后计入填写队列",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTROLS_WIDTH - 48,
            justify="left",
            anchor="w",
        )
        self.autofill_status_label.grid(row=action_row + 2, column=0, sticky="ew", pady=(10, 0))

    def _clear_approve_fields(self):
        for entry in self.field_entries.values():
            entry.delete(0, "end")
        if hasattr(self, "match_field_labels"):
            for label in self.match_field_labels.values():
                label.configure(text="—")

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
        )
        for key, _ in METROLOGY_FIELDS:
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
            result = parse_certificate(path)
            self._parse_results[path] = result
        fields: CertificateFields = result.fields
        for key, entry in self.field_entries.items():
            entry.insert(0, getattr(fields, key, "") or "")
        if hasattr(self, "match_field_labels"):
            for key, label in self.match_field_labels.items():
                label.configure(text=getattr(fields, key, "") or "—")
        self._update_review_cert_status()

    def _update_review_cert_status(self):
        if not hasattr(self, "review_cert_status"):
            return
        path = self._current_cert_path()
        if path is None:
            self.review_cert_status.configure(text="当前：无证书", text_color="gray60")
            return
        if path in self._autofill_queue:
            pos = self._autofill_queue.index(path) + 1
            self.review_cert_status.configure(
                text=f"当前：已批准 · 队列第 {pos}/{len(self._autofill_queue)} 份",
                text_color=("#2d8a4e", "#3dd68c"),
            )
        else:
            self.review_cert_status.configure(text="当前：未批准", text_color="gray60")

    def _update_autofill_button(self):
        if not hasattr(self, "autofill_button"):
            return
        n = len(self._autofill_queue)
        self.autofill_button.configure(text=f"自动填写 ({n})")
        if n == 0:
            self.autofill_status_label.configure(text="批准证书后计入填写队列")
        else:
            self.autofill_status_label.configure(
                text=f"队列中共 {n} 份已批准证书，点击主按钮写入左侧网页"
            )
        self._update_review_cert_status()

    def _on_prev_certificate(self):
        if self._current_cert_index > 0:
            self._current_cert_index -= 1
        self._update_cert_nav_labels()
        self._load_approve_fields_for_current()
        self.set_status("上一份")

    def _on_next_certificate(self):
        if self._imported_files and self._current_cert_index < len(self._imported_files) - 1:
            self._current_cert_index += 1
        self._update_cert_nav_labels()
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
        if path not in self._autofill_queue:
            self._autofill_queue.append(path)
        self._update_autofill_button()
        self.set_status(f"已批准，队列 {len(self._autofill_queue)} 份")
        for i in range(self._current_cert_index + 1, len(self._imported_files)):
            if self._imported_files[i] not in self._autofill_queue:
                self._current_cert_index = i
                self._update_cert_nav_labels()
                self._load_approve_fields_for_current()
                return
        self._update_review_cert_status()

    def _on_skip_entry(self):
        self.set_status("已跳过当前条目")
        self._on_next_certificate()

    def _on_unqueue_entry(self):
        path = self._current_cert_path()
        if path is None:
            return
        if path in self._autofill_queue:
            self._autofill_queue.remove(path)
            self._update_autofill_button()
            self.set_status("已从填写队列移除")
        else:
            self.set_status("当前证书不在填写队列中")

    def _on_master_autofill(self):
        n = len(self._autofill_queue)
        if n == 0:
            self.autofill_status_label.configure(text="队列为空，请先批准至少一份证书")
            self.set_status("自动填写：队列为空")
            return
        self.autofill_status_label.configure(text=f"正在自动填写 {n} 份…")
        self.set_status(f"自动填写 {n} 份（网页写入待实现）")

    # ------------------------------------------------------------------ website
    def _on_open_website(self):
        url = self.url_entry.get().strip()
        if url:
            self.browser_placeholder.configure(
                text=f"网页嵌入区域\n\n{url}\n\n（浏览器嵌入待实现）"
            )
            self.set_status(f"已打开：{url}")
        else:
            self.set_status("请输入网址")

    def _on_refresh_website(self):
        self.set_status("网页已刷新（待实现）")

    def _on_close(self):
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()

import customtkinter
from tkinter import filedialog


customtkinter.set_appearance_mode("System")
customtkinter.set_default_color_theme("blue")

SIDEBAR_WIDTH = 188
CONTROLS_WIDTH = 360
STEP_TILE_SIZE = 144
STEP_TILE_PADX = 18
SETTINGS_BTN_HEIGHT = 40
BUILD_VERSION = "v0"
BUILD_DATE = "10/07/2026"

STEPS = [
    ("batch", "批量导入", "📁"),
    ("approve", "人工审核", "✍️"),
    ("fill", "自动填写", "🌐"),
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


class App(customtkinter.CTk):
    def __init__(self):
        super().__init__()

        self.title("VinCert")
        self.geometry("1280x720")

        self._imported_files: list[str] = []
        self._current_cert_index = 0
        self._current_step = "batch"

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0, minsize=SIDEBAR_WIDTH)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0, minsize=CONTROLS_WIDTH)

        self._build_sidebar()
        self._build_website_panel()
        self._build_controls_panel()
        self._apply_min_window_size()
        self.show_step("batch")

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
        progress_wrap.grid_columnconfigure((0, 1, 2), weight=1, uniform="progress")

        self.progress_step_label = customtkinter.CTkLabel(
            progress_wrap,
            text="第 1/3 步",
            font=customtkinter.CTkFont(size=11),
            text_color="gray60",
        )
        self.progress_step_label.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

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

    def _build_website_panel(self):
        self.website_panel = customtkinter.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.website_panel.grid(row=0, column=1, sticky="nsew")
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
            ("batch", self._build_batch_controls),
            ("approve", self._build_approve_controls),
            ("fill", self._build_fill_controls),
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
            "batch": "批量导入",
            "approve": "人工审核",
            "fill": "自动填写",
        }
        self.controls_header.configure(text=titles[key])

        for name, frame in self.step_views.items():
            if name == key:
                frame.tkraise()

    def set_status(self, message: str):
        pass

    def _apply_min_window_size(self):
        min_width = SIDEBAR_WIDTH + CONTROLS_WIDTH + 400
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

    # ------------------------------------------------------------------ batch
    def _build_batch_controls(self, parent: customtkinter.CTkFrame):
        customtkinter.CTkLabel(
            parent,
            text="选择证件图片或 PDF，批量导入后进行识别。",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTROLS_WIDTH - 48,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        btn_row = customtkinter.CTkFrame(parent, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        btn_row.grid_columnconfigure((0, 1), weight=1)

        customtkinter.CTkButton(
            btn_row,
            text="选择文件",
            command=self._select_certificate_files,
        ).grid(row=0, column=0, padx=(0, 6), sticky="ew")

        customtkinter.CTkButton(
            btn_row,
            text="清空列表",
            fg_color="transparent",
            border_width=2,
            text_color=("gray10", "gray90"),
            hover_color=OUTLINE_BTN_HOVER,
            command=self._clear_import_list,
        ).grid(row=0, column=1, padx=(6, 0), sticky="ew")

        self.import_count_label = customtkinter.CTkLabel(
            parent,
            text="已选择 0 个文件",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            anchor="w",
        )
        self.import_count_label.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        self.import_listbox = customtkinter.CTkTextbox(parent, height=280, state="disabled")
        self.import_listbox.grid(row=3, column=0, sticky="ew", pady=(0, 16))

        customtkinter.CTkButton(
            parent,
            text="开始批量导入",
            height=40,
            command=self._on_start_batch_import,
        ).grid(row=4, column=0, sticky="ew")

        customtkinter.CTkButton(
            parent,
            text="前往人工审核 →",
            height=40,
            fg_color="transparent",
            border_width=2,
            text_color=("gray10", "gray90"),
            hover_color=OUTLINE_BTN_HOVER,
            command=lambda: self.show_step("approve"),
        ).grid(row=5, column=0, sticky="ew", pady=(10, 0))

    def _select_certificate_files(self):
        paths = filedialog.askopenfilenames(
            title="选择证件文件",
            filetypes=[
                ("证件文件", "*.png *.jpg *.jpeg *.pdf *.tiff *.bmp"),
                ("所有文件", "*.*"),
            ],
        )
        if not paths:
            return

        self._imported_files = list(paths)
        self._current_cert_index = 0
        self._refresh_import_list()
        self._update_cert_nav_labels()
        self.set_status(f"已选择 {len(self._imported_files)} 个文件")

    def _clear_import_list(self):
        self._imported_files.clear()
        self._current_cert_index = 0
        self._refresh_import_list()
        self._update_cert_nav_labels()
        self.set_status("导入列表已清空")

    def _refresh_import_list(self):
        self.import_count_label.configure(text=f"已选择 {len(self._imported_files)} 个文件")
        self.import_listbox.configure(state="normal")
        self.import_listbox.delete("1.0", "end")
        if self._imported_files:
            self.import_listbox.insert("end", "\n".join(self._imported_files))
        else:
            self.import_listbox.insert("end", "尚未选择文件，请点击「选择文件」添加证件。")
        self.import_listbox.configure(state="disabled")

    def _on_start_batch_import(self):
        if not self._imported_files:
            self.set_status("请先添加证件文件")
            return
        self.set_status(f"已排队 {len(self._imported_files)} 个文件（识别功能待实现）")

    # ------------------------------------------------------------------ approve
    def _build_approve_controls(self, parent: customtkinter.CTkFrame):
        customtkinter.CTkLabel(
            parent,
            text="核对识别结果，修正字段后批准进入填写流程。",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTROLS_WIDTH - 48,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        self.cert_nav_label = self._build_cert_nav_row(parent, row=1)

        preview = customtkinter.CTkFrame(parent, height=120)
        preview.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        preview.grid_propagate(False)
        preview.grid_columnconfigure(0, weight=1)
        preview.grid_rowconfigure(0, weight=1)

        self.preview_placeholder = customtkinter.CTkLabel(
            preview,
            text="证件预览",
            text_color="gray50",
        )
        self.preview_placeholder.grid(row=0, column=0)

        customtkinter.CTkLabel(
            parent,
            text="识别字段",
            font=customtkinter.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).grid(row=3, column=0, sticky="ew", pady=(0, 8))

        self.field_entries: dict[str, customtkinter.CTkEntry] = {}
        fields = [
            ("name", "姓名"),
            ("id_number", "证件号码"),
            ("certificate_no", "证书编号"),
            ("issue_date", "发证日期"),
            ("expiry_date", "有效期至"),
            ("issuer", "发证机关"),
        ]

        for row, (key, label) in enumerate(fields, start=4):
            customtkinter.CTkLabel(
                parent,
                text=label,
                anchor="w",
                width=72,
            ).grid(row=row, column=0, sticky="w", pady=4)

            entry = customtkinter.CTkEntry(parent, placeholder_text=f"请输入{label}")
            entry.grid(row=row, column=0, sticky="ew", padx=(80, 0), pady=4)
            self.field_entries[key] = entry

        action_row = row + 1
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
            parent,
            text="前往自动填写 →",
            height=40,
            fg_color="transparent",
            border_width=2,
            text_color=("gray10", "gray90"),
            hover_color=OUTLINE_BTN_HOVER,
            command=lambda: self.show_step("fill"),
        ).grid(row=action_row + 1, column=0, sticky="ew", pady=(10, 0))

    def _on_prev_certificate(self):
        if self._current_cert_index > 0:
            self._current_cert_index -= 1
        self._update_cert_nav_labels()
        self.set_status("上一份（数据加载待实现）")

    def _on_next_certificate(self):
        if self._imported_files and self._current_cert_index < len(self._imported_files) - 1:
            self._current_cert_index += 1
        self._update_cert_nav_labels()
        self.set_status("下一份（数据加载待实现）")

    def _on_open_settings(self):
        pass

    def _cert_nav_text(self) -> str:
        total = len(self._imported_files)
        if total == 0:
            return "0/0"
        return f"{self._current_cert_index + 1}/{total}"

    def _update_cert_nav_labels(self):
        text = self._cert_nav_text()
        self.cert_nav_label.configure(text=text)
        self.fill_nav_label.configure(text=text)

    def _on_approve_entry(self):
        self.set_status("已批准（填写逻辑待实现）")

    def _on_skip_entry(self):
        self.set_status("已跳过当前条目")

    # ------------------------------------------------------------------ fill
    def _build_fill_controls(self, parent: customtkinter.CTkFrame):
        customtkinter.CTkLabel(
            parent,
            text="将审核通过的字段自动填入左侧网页表单。",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTROLS_WIDTH - 48,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        self.fill_nav_label = self._build_cert_nav_row(parent, row=1)

        summary = customtkinter.CTkFrame(parent)
        summary.grid(row=2, column=0, sticky="ew", pady=(0, 16))
        summary.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            summary,
            text="填写状态",
            font=customtkinter.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=14, pady=(12, 6), sticky="w")

        self.fill_status_label = customtkinter.CTkLabel(
            summary,
            text="等待开始填写",
            font=customtkinter.CTkFont(size=12),
            text_color="gray60",
            wraplength=CONTROLS_WIDTH - 72,
            justify="left",
        )
        self.fill_status_label.grid(row=1, column=0, padx=14, pady=(0, 12), sticky="w")

        customtkinter.CTkButton(
            parent,
            text="填写",
            height=40,
            command=self._on_fill,
        ).grid(row=3, column=0, sticky="ew")

        customtkinter.CTkButton(
            parent,
            text="← 返回批量导入",
            height=40,
            fg_color="transparent",
            border_width=2,
            text_color=("gray10", "gray90"),
            hover_color=OUTLINE_BTN_HOVER,
            command=lambda: self.show_step("batch"),
        ).grid(row=4, column=0, sticky="ew", pady=(10, 0))

    def _on_fill(self):
        self.fill_status_label.configure(text="正在填写网页…")
        self.set_status("自动填写（待实现）")

    # ------------------------------------------------------------------ website
    def _on_open_website(self):
        url = self.url_entry.get().strip()
        if url:
            self.browser_placeholder.configure(text=f"网页嵌入区域\n\n{url}\n\n（浏览器嵌入待实现）")
            self.set_status(f"已打开：{url}")
        else:
            self.set_status("请输入网址")

    def _on_refresh_website(self):
        self.set_status("网页已刷新（待实现）")


if __name__ == "__main__":
    app = App()
    app.mainloop()

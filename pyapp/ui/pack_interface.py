"""压缩打包页：选择/拖入文件 → 设置格式与输出 → 打包（可选自动上传）。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    FluentIcon,
    HeaderCardWidget,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    RadioButton,
    StrongBodyLabel,
    SubtitleLabel,
    TextEdit,
)

from ..core import (
    ARCHIVE_FORMATS,
    archive_prefix,
    build_initial_groups,
    derive_default_output_dir,
    describe_archive_tool,
    detect_7z_executable,
    detect_rar_executable,
    format_bytes,
    resolve_archive_tool,
    scan_inputs,
)
from ..lanzou_client import parse_folder_id
from .widgets import DropArea
from .workers import EventBridge, PackWorker


def _vbox(widget: QWidget, *, margins=(0, 0, 0, 0), spacing=10) -> QVBoxLayout:
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


class PackInterface(QWidget):
    def __init__(self, upload_interface, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("packInterface")
        self._upload = upload_interface

        self._input_roots: list[Path] = []
        self._files = []
        self._total_bytes = 0
        self._default_output_dir: Path | None = None
        self._custom_output_dir: Path | None = None
        self._created_archives: list[Path] = []

        self._bridge = EventBridge(self)
        self._bridge.event.connect(self._handle_event)
        self._pack_worker: PackWorker | None = None

        self._build_ui()
        self.log("把文件或文件夹拖进来，或点上方按钮选择。")
        self.log("每个压缩包都按最终压缩结果严格控制在 100MB 内。")
        self.log("上面的“粗略预计包数”只是预估，最终以实际压缩结果为准。")

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = _vbox(self, margins=(24, 18, 24, 24), spacing=16)
        root.addWidget(SubtitleLabel("批量压缩打包"))
        root.addWidget(
            CaptionLabel("打包与上传分开；支持 zip / 7z / rar，最终按压缩结果控制每包不超过 100MB。")
        )

        # 选择 + 拖放
        source_card = HeaderCardWidget(self)
        source_card.setTitle("选择来源")
        source_body = QWidget()
        source_layout = _vbox(source_body, spacing=12)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        pick_files = PushButton(FluentIcon.DOCUMENT, "选择文件")
        pick_files.clicked.connect(self._select_files)
        pick_dir = PushButton(FluentIcon.FOLDER, "选择目录")
        pick_dir.clicked.connect(self._select_directory)
        clear_btn = PushButton(FluentIcon.DELETE, "清空")
        clear_btn.clicked.connect(self._clear_inputs)
        for btn in (pick_files, pick_dir, clear_btn):
            actions.addWidget(btn)
        actions.addStretch(1)
        source_layout.addLayout(actions)

        self._drop = DropArea()
        self._drop.paths_dropped.connect(self._load_roots)
        source_layout.addWidget(self._drop)
        source_card.viewLayout.addWidget(source_body)
        root.addWidget(source_card)

        # 当前输入摘要
        summary_card = HeaderCardWidget(self)
        summary_card.setTitle("当前输入")
        summary_body = QWidget()
        summary_layout = _vbox(summary_body, spacing=6)
        self._source_label = BodyLabel("来源: 未选择")
        self._count_label = BodyLabel("文件数量: 0")
        self._size_label = BodyLabel("总大小: 0 B")
        self._estimate_label = StrongBodyLabel("粗略预计包数: 0")
        for w in (self._source_label, self._count_label, self._size_label, self._estimate_label):
            summary_layout.addWidget(w)
        summary_card.viewLayout.addWidget(summary_body)
        root.addWidget(summary_card)

        # 压缩设置
        archive_card = HeaderCardWidget(self)
        archive_card.setTitle("压缩设置")
        archive_body = QWidget()
        archive_layout = _vbox(archive_body, spacing=12)

        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(8)
        fmt_row.addWidget(BodyLabel("压缩格式"))
        self._format_combo = ComboBox()
        for fmt in ARCHIVE_FORMATS:
            self._format_combo.addItem(fmt.upper(), userData=fmt)
        self._format_combo.setCurrentIndex(0)
        self._format_combo.currentIndexChanged.connect(self._refresh_archiver_status)
        fmt_row.addWidget(self._format_combo)
        fmt_row.addStretch(1)
        archive_layout.addLayout(fmt_row)

        tool_row = QHBoxLayout()
        tool_row.setSpacing(8)
        tool_row.addWidget(BodyLabel("压缩程序"))
        self._tool_edit = LineEdit()
        self._tool_edit.setPlaceholderText("留空自动选择；7z/rar 可指定可执行文件")
        self._tool_edit.textChanged.connect(self._refresh_archiver_status)
        tool_row.addWidget(self._tool_edit, stretch=1)
        detect_btn = PushButton(FluentIcon.SYNC, "自动检测")
        detect_btn.clicked.connect(self._auto_detect)
        pick_btn = PushButton(FluentIcon.FOLDER, "选择程序")
        pick_btn.clicked.connect(self._select_archiver)
        tool_row.addWidget(detect_btn)
        tool_row.addWidget(pick_btn)
        archive_layout.addLayout(tool_row)

        self._archiver_status = BodyLabel(describe_archive_tool("zip"))
        self._archiver_status.setWordWrap(True)
        archive_layout.addWidget(self._archiver_status)
        archive_layout.addWidget(
            CaptionLabel(
                "zip 默认可回退内置压缩；7z / rar 优先用程序目录 tools 内置工具，其次系统已安装版本。"
            )
        )
        archive_card.viewLayout.addWidget(archive_body)
        root.addWidget(archive_card)

        # 输出目录
        output_card = HeaderCardWidget(self)
        output_card.setTitle("输出目录")
        output_body = QWidget()
        output_layout = _vbox(output_body, spacing=10)
        radio_row = QHBoxLayout()
        radio_row.setSpacing(12)
        self._radio_source = RadioButton("原文件夹")
        self._radio_source.setChecked(True)
        self._radio_source.toggled.connect(self._refresh_output_label)
        self._radio_custom = RadioButton("自定义")
        self._radio_custom.toggled.connect(self._refresh_output_label)
        radio_row.addWidget(self._radio_source)
        radio_row.addWidget(self._radio_custom)
        pick_out = PushButton(FluentIcon.FOLDER, "选择输出目录")
        pick_out.clicked.connect(self._select_output_dir)
        radio_row.addWidget(pick_out)
        radio_row.addStretch(1)
        output_layout.addLayout(radio_row)
        self._output_label = BodyLabel("输出到: 未选择")
        self._output_label.setWordWrap(True)
        output_layout.addWidget(self._output_label)
        output_card.viewLayout.addWidget(output_body)
        root.addWidget(output_card)

        # 开始打包
        run_row = QHBoxLayout()
        run_row.setSpacing(12)
        self._pack_btn = PrimaryPushButton(FluentIcon.SEND, "开始压缩打包")
        self._pack_btn.clicked.connect(self._start_packing)
        run_row.addWidget(self._pack_btn)
        self._auto_upload = CheckBox("打包完成后自动上传到蓝奏云")
        run_row.addWidget(self._auto_upload)
        run_row.addStretch(1)
        root.addLayout(run_row)

        self._progress = ProgressBar()
        self._progress.setValue(0)
        self._progress.hide()
        root.addWidget(self._progress)

        # 结果 + 日志
        result_card = HeaderCardWidget(self)
        result_card.setTitle("本次生成")
        result_body = QWidget()
        result_layout = _vbox(result_body, spacing=6)
        self._result_text = TextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMinimumHeight(110)
        result_layout.addWidget(self._result_text)
        result_card.viewLayout.addWidget(result_body)
        root.addWidget(result_card)

        log_card = HeaderCardWidget(self)
        log_card.setTitle("打包日志")
        log_body = QWidget()
        log_layout = _vbox(log_body, spacing=6)
        self._log_text = TextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMinimumHeight(150)
        log_layout.addWidget(self._log_text)
        log_card.viewLayout.addWidget(log_body)
        root.addWidget(log_card)

        root.addStretch(1)

    # ------------------------------------------------------------------
    # 日志 / 提示
    # ------------------------------------------------------------------
    def log(self, message: str) -> None:
        self._log_text.append(message)

    def _warn(self, title: str, content: str = "") -> None:
        InfoBar.warning(title, content, duration=3500, position=InfoBarPosition.TOP, parent=self)

    def _success(self, title: str, content: str = "") -> None:
        InfoBar.success(title, content, duration=2500, position=InfoBarPosition.TOP, parent=self)

    # ------------------------------------------------------------------
    # 选择来源
    # ------------------------------------------------------------------
    def _current_format(self) -> str:
        return self._format_combo.currentData() or "zip"

    def _select_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择文件")
        if paths:
            self._load_roots([Path(p) for p in paths])

    def _select_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            self._load_roots([Path(path)])

    def _clear_inputs(self) -> None:
        self._input_roots = []
        self._files = []
        self._total_bytes = 0
        self._default_output_dir = None
        self._custom_output_dir = None
        self._clear_results()
        self._log_text.clear()
        self._refresh_summary()
        self.log("已清空当前输入。")

    def _load_roots(self, roots: list[Path]) -> None:
        self._log_text.clear()
        self._clear_results()
        files, warnings = scan_inputs(roots)
        self._input_roots = roots
        self._files = files
        self._total_bytes = sum(f.size for f in files)
        self._default_output_dir = derive_default_output_dir(roots)
        if not files:
            self.log("没有扫描到可打包的文件。")
        else:
            self.log(f"已载入 {len(files)} 个文件，总大小 {format_bytes(self._total_bytes)}。")
        for warning in warnings:
            self.log(f"提示: {warning}")
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        if not self._input_roots:
            self._source_label.setText("来源: 未选择")
        elif len(self._input_roots) == 1:
            self._source_label.setText(f"来源: {self._input_roots[0]}")
        else:
            self._source_label.setText(f"来源入口: {len(self._input_roots)} 个")
        self._count_label.setText(f"文件数量: {len(self._files)}")
        self._size_label.setText(f"总大小: {format_bytes(self._total_bytes)}")
        self._estimate_label.setText(f"粗略预计包数: {len(build_initial_groups(self._files))}")
        self._refresh_output_label()

    # ------------------------------------------------------------------
    # 压缩程序
    # ------------------------------------------------------------------
    def _refresh_archiver_status(self, *_args) -> None:
        self._archiver_status.setText(
            describe_archive_tool(self._current_format(), self._tool_edit.text())
        )
        self._refresh_summary()

    def _auto_detect(self) -> None:
        fmt = self._current_format()
        detected = detect_rar_executable() if fmt == "rar" else detect_7z_executable()
        if detected is None:
            self._tool_edit.clear()
            self._warn(
                "未检测到压缩程序",
                "可把 7z / rar 放到程序目录 tools 文件夹，或手动选择可执行文件。",
            )
            return
        self._tool_edit.setText(str(detected))
        self.log(f"已检测到压缩程序: {detected}")

    def _select_archiver(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择压缩程序")
        if path:
            self._tool_edit.setText(path)

    # ------------------------------------------------------------------
    # 输出目录
    # ------------------------------------------------------------------
    def _select_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self._custom_output_dir = Path(path)
            self._radio_custom.setChecked(True)
            self._refresh_output_label()

    def _resolved_output_dir(self) -> Path | None:
        if self._radio_custom.isChecked():
            return self._custom_output_dir
        return self._default_output_dir

    def _refresh_output_label(self, *_args) -> None:
        output_dir = self._resolved_output_dir()
        self._output_label.setText(
            "输出到: 未选择" if output_dir is None else f"输出到: {output_dir}"
        )

    # ------------------------------------------------------------------
    # 结果
    # ------------------------------------------------------------------
    def _clear_results(self) -> None:
        self._created_archives = []
        self._result_text.clear()

    def _append_archive(self, archive: Path) -> None:
        self._created_archives.append(archive)
        lines = [
            f"{p.name} | {format_bytes(p.stat().st_size)} | {p}"
            for p in self._created_archives
            if p.exists()
        ]
        self._result_text.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------
    # 打包
    # ------------------------------------------------------------------
    def _start_packing(self) -> None:
        if not self._files:
            self._warn("请先选择文件或目录")
            return
        output_dir = self._resolved_output_dir()
        if output_dir is None:
            self._warn("请先选择输出目录")
            return

        auto_upload = self._auto_upload.isChecked()
        try:
            tool_spec = resolve_archive_tool(self._current_format(), self._tool_edit.text())
            if auto_upload:
                if self._upload.active_cookie is None:
                    raise RuntimeError("已勾选自动上传，请先在“蓝奏云上传”页完成登录。")
                parse_folder_id(self._upload.folder_id_text())
        except Exception as exc:  # noqa: BLE001
            self._warn("无法开始", str(exc))
            return

        self.set_running(True)
        self._log_text.clear()
        self._clear_results()
        self._progress.show()
        self._progress.setRange(0, 0)  # 不确定进度（打包大小不可预知）
        self.log(f"开始打包，共 {len(self._files)} 个文件，格式 {tool_spec.extension}。")

        cookie = None
        folder_id_text = ""
        ua = ""
        if auto_upload:
            cookie = dict(self._upload.active_cookie or {})
            folder_id_text = self._upload.folder_id_text()
            ua = self._upload.active_ua
            self._upload.clear_share_links()
            self._upload.append_upload_log("已开启自动上传，打包完成后会自动上传到蓝奏云。")

        worker = PackWorker(
            list(self._files),
            output_dir,
            archive_prefix(self._input_roots),
            tool_spec,
            self._bridge,
            auto_upload=auto_upload,
            cookie=cookie,
            folder_id_text=folder_id_text,
            ua=ua,
        )
        worker.finished.connect(lambda: setattr(self, "_pack_worker", None))
        self._pack_worker = worker
        worker.start()

    def _handle_event(self, kind: str, payload: object) -> None:
        if kind == "pack_log":
            self.log(str(payload))
        elif kind == "pack_archive":
            self._append_archive(payload)  # type: ignore[arg-type]
        elif kind == "pack_archives_ready":
            archives = list(payload)  # type: ignore[arg-type]
            self._created_archives = archives
            self._upload.set_generated_archives(archives, select_all=True)
        elif kind == "pack_done":
            self.set_running(False)
            self._progress.hide()
            self._progress.setRange(0, 100)
            self._success("打包完成", f"共生成 {len(self._created_archives)} 个压缩包")
        elif kind.startswith("upload_"):
            # 自动上传阶段的事件转交给上传页
            self._upload.handle_event(kind, payload)

    def set_running(self, running: bool) -> None:
        self._pack_btn.setEnabled(not running)

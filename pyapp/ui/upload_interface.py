"""蓝奏上传页：登录（浏览器导入/网页登录自动抓取/粘贴）+ 待上传队列 + 进度 + 分享链接。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    HeaderCardWidget,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    ListWidget,
    PasswordLineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TextEdit,
    TransparentPushButton,
)

from ..lanzou_client import (
    BROWSER_IMPORTERS,
    ShareLink,
    available_browser_choices,
    create_lanzou_client,
    format_share_line,
    load_browser_login_cookie,
    open_lanzou_login_in_browser,
    parse_cookie_string,
    parse_folder_id,
)
from .workers import (
    BrowserLoginWatcher,
    CallableWorker,
    EventBridge,
    UploadWorker,
    WebLoginWorker,
)


def _vbox(widget: QWidget, *, margins=(0, 0, 0, 0), spacing=10) -> QVBoxLayout:
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


class UploadInterface(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("uploadInterface")

        self.active_cookie: dict[str, str] | None = None
        self.active_ua: str = ""
        self._created_archives: list[Path] = []
        self._upload_archives: list[Path] = []
        self._share_links: list[ShareLink] = []
        self._bridge = EventBridge(self)
        self._bridge.event.connect(self.handle_event)
        self._upload_worker: UploadWorker | None = None
        self._login_worker: CallableWorker | None = None
        self._login_watcher: BrowserLoginWatcher | None = None
        self._web_login_worker: WebLoginWorker | None = None

        self._build_ui()
        self.append_upload_log("先登录蓝奏云，再选择压缩包上传。推荐“内置登录”，最稳。")

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = _vbox(self, margins=(24, 18, 24, 24), spacing=16)

        root.addWidget(SubtitleLabel("蓝奏云上传"))

        root.addWidget(self._build_login_card())
        root.addWidget(self._build_generated_card())
        root.addWidget(self._build_queue_card())
        root.addWidget(self._build_result_card())
        root.addWidget(self._build_log_card())
        root.addStretch(1)

    def _build_login_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("登录蓝奏云")
        body = QWidget()
        layout = _vbox(body, spacing=12)

        # 1) 内置登录（推荐，最稳）：系统 WebView2 内嵌登录页，直接抓 Cookie
        inbuilt_row = QHBoxLayout()
        inbuilt_row.setSpacing(8)
        self._web_login_btn = PrimaryPushButton(FluentIcon.GLOBE, "内置登录（推荐，最稳）")
        self._web_login_btn.clicked.connect(self._open_inbuilt_login)
        inbuilt_row.addWidget(self._web_login_btn)
        inbuilt_row.addWidget(CaptionLabel("程序内打开登录页，登录后自动抓取 Cookie；不受浏览器锁库/加密影响"))
        inbuilt_row.addStretch(1)
        layout.addLayout(inbuilt_row)

        # 2) 从已登录浏览器直接导入
        browser_row = QHBoxLayout()
        browser_row.setSpacing(8)
        browser_row.addWidget(BodyLabel("浏览器"))
        self._browser_combo = ComboBox()
        for key in available_browser_choices():
            label = "自动检测全部" if key == "auto" else (BROWSER_IMPORTERS.get(key) or key)
            self._browser_combo.addItem(label, userData=key)
        self._browser_combo.setCurrentIndex(0)
        browser_row.addWidget(self._browser_combo)
        import_btn = PushButton(FluentIcon.DOWNLOAD, "从浏览器导入登录")
        import_btn.clicked.connect(self._import_browser_login)
        browser_row.addWidget(import_btn)
        browser_row.addWidget(CaptionLabel("需先完全退出该浏览器（新版 Edge/Chrome 会锁库）"))
        browser_row.addStretch(1)
        layout.addLayout(browser_row)

        # 3) 打开外部浏览器登录并自动抓取
        web_row = QHBoxLayout()
        web_row.setSpacing(8)
        web_btn = PushButton(FluentIcon.LINK, "打开外部浏览器登录并自动抓取")
        web_btn.clicked.connect(self._open_external_login)
        web_row.addWidget(web_btn)
        web_row.addWidget(CaptionLabel("在所选浏览器打开登录页，登录后程序轮询自动导入"))
        web_row.addStretch(1)
        layout.addLayout(web_row)

        # 3) 手动粘贴 Cookie
        cookie_row = QHBoxLayout()
        cookie_row.setSpacing(8)
        cookie_row.addWidget(BodyLabel("Cookie"))
        self._cookie_edit = PasswordLineEdit()
        self._cookie_edit.setPlaceholderText("至少包含 ylogin 和 phpdisk_info")
        cookie_row.addWidget(self._cookie_edit, stretch=1)
        paste_btn = TransparentPushButton(FluentIcon.PASTE, "粘贴")
        paste_btn.clicked.connect(self._paste_cookie)
        apply_btn = PushButton(FluentIcon.ACCEPT, "应用 Cookie")
        apply_btn.clicked.connect(self._apply_cookie_login)
        cookie_row.addWidget(paste_btn)
        cookie_row.addWidget(apply_btn)
        layout.addLayout(cookie_row)

        # 状态行
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(BodyLabel("登录状态"))
        self._login_status = StrongBodyLabel("未登录")
        status_row.addWidget(self._login_status)
        status_row.addStretch(1)
        clear_btn = TransparentPushButton(FluentIcon.CANCEL, "清除登录")
        clear_btn.clicked.connect(self._clear_login)
        status_row.addWidget(clear_btn)
        layout.addLayout(status_row)

        card.viewLayout.addWidget(body)
        return card

    def _build_generated_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("本次生成的压缩包")
        body = QWidget()
        layout = _vbox(body, spacing=10)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        select_all = PushButton(FluentIcon.ACCEPT, "全选")
        select_all.clicked.connect(lambda: self._generated_list.selectAll())
        add_btn = PushButton(FluentIcon.ADD, "加入待上传")
        add_btn.clicked.connect(self._add_selected_generated)
        only_btn = PushButton(FluentIcon.SYNC, "仅用选中项")
        only_btn.clicked.connect(self._replace_with_selected_generated)
        for btn in (select_all, add_btn, only_btn):
            actions.addWidget(btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self._generated_list = ListWidget()
        self._generated_list.setSelectionMode(ListWidget.SelectionMode.ExtendedSelection)
        self._generated_list.setMinimumHeight(120)
        layout.addWidget(self._generated_list)

        card.viewLayout.addWidget(body)
        return card

    def _build_queue_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("待上传压缩包")
        body = QWidget()
        layout = _vbox(body, spacing=10)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        add_ext = PushButton(FluentIcon.FOLDER_ADD, "添加外部压缩包")
        add_ext.clicked.connect(self._select_external_archives)
        remove_btn = PushButton(FluentIcon.REMOVE, "移除选中")
        remove_btn.clicked.connect(self._remove_selected_queue)
        clear_btn = PushButton(FluentIcon.DELETE, "清空")
        clear_btn.clicked.connect(self._clear_queue)
        for btn in (add_ext, remove_btn, clear_btn):
            actions.addWidget(btn)
        actions.addStretch(1)
        self._upload_btn = PrimaryPushButton(FluentIcon.CLOUD, "开始上传")
        self._upload_btn.clicked.connect(self._start_upload)
        actions.addWidget(self._upload_btn)
        layout.addLayout(actions)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        folder_row.addWidget(BodyLabel("目标文件夹 ID"))
        self._folder_edit = LineEdit()
        self._folder_edit.setPlaceholderText("留空 = 上传到根目录")
        self._folder_edit.setFixedWidth(180)
        folder_row.addWidget(self._folder_edit)
        folder_row.addStretch(1)
        layout.addLayout(folder_row)

        self._queue_label = CaptionLabel("待上传压缩包: 0 个")
        layout.addWidget(self._queue_label)

        self._queue_list = ListWidget()
        self._queue_list.setSelectionMode(ListWidget.SelectionMode.ExtendedSelection)
        self._queue_list.setMinimumHeight(120)
        layout.addWidget(self._queue_list)

        self._progress = ProgressBar()
        self._progress.setValue(0)
        self._progress.hide()
        layout.addWidget(self._progress)

        card.viewLayout.addWidget(body)
        return card

    def _build_result_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("网盘链接")
        body = QWidget()
        layout = _vbox(body, spacing=10)

        actions = QHBoxLayout()
        copy_btn = PushButton(FluentIcon.COPY, "复制全部链接")
        copy_btn.clicked.connect(self._copy_all_links)
        actions.addWidget(copy_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self._result_text = TextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMinimumHeight(110)
        layout.addWidget(self._result_text)

        card.viewLayout.addWidget(body)
        return card

    def _build_log_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("上传日志")
        body = QWidget()
        layout = _vbox(body, spacing=6)
        self._log_text = TextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMinimumHeight(140)
        layout.addWidget(self._log_text)
        card.viewLayout.addWidget(body)
        return card

    # ------------------------------------------------------------------
    # 日志 / 提示
    # ------------------------------------------------------------------
    def append_upload_log(self, message: str) -> None:
        self._log_text.append(message)

    def _info(self, title: str, content: str = "") -> None:
        InfoBar.info(title, content, duration=2500, position=InfoBarPosition.TOP, parent=self)

    def _warn(self, title: str, content: str = "") -> None:
        InfoBar.warning(title, content, duration=3500, position=InfoBarPosition.TOP, parent=self)

    def _success(self, title: str, content: str = "") -> None:
        InfoBar.success(title, content, duration=2500, position=InfoBarPosition.TOP, parent=self)

    # ------------------------------------------------------------------
    # 登录
    # ------------------------------------------------------------------
    def _selected_browser(self) -> str:
        return self._browser_combo.currentData() or "auto"

    def _open_inbuilt_login(self) -> None:
        if self._web_login_worker is not None:
            self._info("登录窗口已打开", "请在弹出的窗口里完成登录")
            return
        self.append_upload_log("正在打开内置登录窗口（系统 WebView2）；登录成功后会自动抓取 Cookie…")
        self._login_status.setText("内置登录窗口已打开，请完成登录…")
        self._web_login_btn.setEnabled(False)
        worker = WebLoginWorker()
        worker.succeeded.connect(self._on_inbuilt_login_ok)
        worker.failed.connect(self._on_inbuilt_login_failed)

        def _cleanup() -> None:
            self._web_login_worker = None
            self._web_login_btn.setEnabled(True)

        worker.finished.connect(_cleanup)
        self._web_login_worker = worker
        worker.start()

    def _on_inbuilt_login_ok(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        cookie = data.get("cookie") or {}
        ua = data.get("ua") or ""
        selftest = data.get("selftest") or {}
        if "error" in selftest:
            self.append_upload_log(f"内置登录自检异常: {selftest['error']}")
        elif "logged_in" in selftest:
            self.append_upload_log(
                f"内置登录自检: 已登录={selftest.get('logged_in')} "
                f"(HTTP {selftest.get('status')}, len={selftest.get('len')}, 标题={selftest.get('title')!r})"
            )
            if selftest.get("snippet"):
                self.append_upload_log(f"  页面片段: {selftest.get('snippet')}")
        self._set_login_cookie(cookie, "内置登录", verified=False, ua=ua)

    def _on_inbuilt_login_failed(self, message: str) -> None:
        if self.active_cookie is None:
            self._login_status.setText("内置登录未完成")
        self._warn("内置登录", message)
        self.append_upload_log(f"内置登录: {message}")

    def _open_external_login(self) -> None:
        browser = self._selected_browser()
        try:
            browser_label, watch_browser = open_lanzou_login_in_browser(browser)
        except Exception as exc:  # noqa: BLE001
            self._warn("打开登录页失败", str(exc))
            return
        self.append_upload_log(
            f"已在 {browser_label} 打开蓝奏云登录页，请完成登录；登录后会自动检测并导入 Cookie。"
        )
        self._login_status.setText("等待网页登录完成…")
        self._stop_login_watcher()
        watcher = BrowserLoginWatcher(watch_browser)
        watcher.succeeded.connect(self._on_browser_login_ok)
        watcher.failed.connect(self._on_watch_failed)
        watcher.finished.connect(lambda: setattr(self, "_login_watcher", None))
        self._login_watcher = watcher
        watcher.start()

    def _on_watch_failed(self, message: str) -> None:
        if self.active_cookie is None:
            self._login_status.setText("未自动导入，请手动重试")
        self.append_upload_log(f"网页登录自动抓取失败: {message}")

    def _stop_login_watcher(self) -> None:
        if self._login_watcher is not None:
            self._login_watcher.stop()
            self._login_watcher = None

    def _import_browser_login(self) -> None:
        browser = self._selected_browser()
        self.append_upload_log(f"正在从浏览器读取蓝奏云登录态（{browser}）…")

        worker = CallableWorker(lambda: load_browser_login_cookie(browser))
        worker.succeeded.connect(self._on_browser_login_ok)
        worker.failed.connect(self._on_browser_login_fail)
        worker.finished.connect(lambda: setattr(self, "_login_worker", None))
        self._login_worker = worker
        worker.start()

    def _on_browser_login_ok(self, result: object) -> None:
        cookie, source, verified = result  # type: ignore[misc]
        self._set_login_cookie(cookie, source, verified=verified)

    def _on_browser_login_fail(self, message: str) -> None:
        self._warn("浏览器导入失败", message)
        self.append_upload_log(f"浏览器导入失败: {message}")

    def _paste_cookie(self) -> None:
        text = QApplication.clipboard().text()
        if not text:
            self._warn("剪贴板为空")
            return
        self._cookie_edit.setText(text)
        self.append_upload_log("已从剪贴板粘贴 Cookie（不会明文显示）。")

    def _apply_cookie_login(self) -> None:
        try:
            cookie = parse_cookie_string(self._cookie_edit.text())
        except Exception as exc:  # noqa: BLE001
            self._warn("Cookie 无效", str(exc))
            return
        self._set_login_cookie(cookie, "手动 Cookie", verified=False)

    def _set_login_cookie(self, cookie: dict, source: str, *, verified: bool, ua: str = "") -> None:
        # 校验 cookie 放后台，避免卡界面
        def verify() -> dict:
            if not verified:
                create_lanzou_client(cookie, ua)
            return dict(cookie)

        self._login_status.setText("正在校验登录…")
        worker = CallableWorker(verify)
        worker.succeeded.connect(lambda c: self._on_login_verified(c, source, ua))
        worker.failed.connect(self._on_login_failed)
        worker.finished.connect(lambda: setattr(self, "_login_worker", None))
        self._login_worker = worker
        worker.start()

    def _on_login_verified(self, cookie: object, source: str, ua: str = "") -> None:
        self._stop_login_watcher()
        self.active_cookie = dict(cookie)  # type: ignore[arg-type]
        self.active_ua = ua
        self._cookie_edit.clear()
        self._login_status.setText(f"已登录（{source}）")
        self.append_upload_log(f"蓝奏云登录成功：{source}。")
        self._success("登录成功", source)

    def _on_login_failed(self, message: str) -> None:
        self._login_status.setText("登录失败")
        self._warn("登录失败", message)
        self.append_upload_log(f"登录失败: {message}")

    def _clear_login(self) -> None:
        self._stop_login_watcher()
        self.active_cookie = None
        self.active_ua = ""
        self._cookie_edit.clear()
        self._login_status.setText("未登录")
        self.append_upload_log("已清除蓝奏云登录状态。")

    # ------------------------------------------------------------------
    # 生成列表 / 待上传队列
    # ------------------------------------------------------------------
    def set_generated_archives(self, archives: list[Path], *, select_all: bool = True) -> None:
        self._created_archives = list(archives)
        self._refresh_generated_list(select_all=select_all)
        # 默认把本次生成的全部放进待上传队列
        self._upload_archives = list(archives)
        self._refresh_queue()

    def _refresh_generated_list(self, *, select_all: bool = False) -> None:
        from ..core import format_bytes

        self._generated_list.clear()
        for path in self._created_archives:
            if path.exists():
                self._generated_list.addItem(f"{path.name} | {format_bytes(path.stat().st_size)}")
        if select_all and self._created_archives:
            self._generated_list.selectAll()

    def _selected_generated(self) -> list[Path]:
        rows = {idx.row() for idx in self._generated_list.selectedIndexes()}
        return [p for i, p in enumerate(self._created_archives) if i in rows]

    def _add_selected_generated(self) -> None:
        selected = self._selected_generated()
        if not selected:
            self._info("请先在“本次生成的压缩包”里选择项目")
            return
        self._extend_queue(selected)

    def _replace_with_selected_generated(self) -> None:
        selected = self._selected_generated()
        if not selected:
            self._info("请先在“本次生成的压缩包”里选择项目")
            return
        self._upload_archives = list(selected)
        self._refresh_queue()

    def _select_external_archives(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择压缩包",
            "",
            "归档文件 (*.zip *.7z *.rar);;所有文件 (*.*)",
        )
        if paths:
            self._extend_queue([Path(p) for p in paths])

    def _extend_queue(self, archives: list[Path]) -> None:
        existing = {self._archive_key(p) for p in self._upload_archives}
        for archive in archives:
            key = self._archive_key(archive)
            if key not in existing:
                self._upload_archives.append(archive)
                existing.add(key)
        self._refresh_queue()

    @staticmethod
    def _archive_key(path: Path) -> str:
        try:
            return str(path.resolve()) if path.exists() else str(path)
        except OSError:
            return str(path)

    def _remove_selected_queue(self) -> None:
        rows = {idx.row() for idx in self._queue_list.selectedIndexes()}
        if not rows:
            self._info("请先在待上传列表里选择要移除的项目")
            return
        self._upload_archives = [p for i, p in enumerate(self._upload_archives) if i not in rows]
        self._refresh_queue()

    def _clear_queue(self) -> None:
        self._upload_archives = []
        self._refresh_queue()

    def _refresh_queue(self) -> None:
        self._queue_list.clear()
        deduped: list[Path] = []
        seen: set[str] = set()
        for path in self._upload_archives:
            key = self._archive_key(path)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(path)
            self._queue_list.addItem(f"{path.name} | {path}")
        self._upload_archives = deduped
        self._queue_label.setText(f"待上传压缩包: {len(self._upload_archives)} 个")

    def folder_id_text(self) -> str:
        return self._folder_edit.text().strip()

    # ------------------------------------------------------------------
    # 上传
    # ------------------------------------------------------------------
    def _start_upload(self) -> None:
        if self.active_cookie is None:
            self._warn("请先完成蓝奏云登录")
            return
        archives = [p for p in self._upload_archives if p.exists()]
        if not archives:
            self._warn("待上传列表为空或文件都不存在")
            return
        try:
            parse_folder_id(self.folder_id_text())
        except Exception as exc:  # noqa: BLE001
            self._warn("目标文件夹 ID 无效", str(exc))
            return

        self.set_running(True)
        self.clear_share_links()
        self._progress.show()
        self.set_upload_progress(0, len(archives))
        self.append_upload_log(f"开始上传现有压缩包，共 {len(archives)} 个。")

        worker = UploadWorker(
            archives, dict(self.active_cookie), self.folder_id_text(), self._bridge, self.active_ua
        )
        worker.finished.connect(lambda: setattr(self, "_upload_worker", None))
        self._upload_worker = worker
        worker.start()

    # ------------------------------------------------------------------
    # 事件处理（既服务于本页 UploadWorker，也服务于自动上传）
    # ------------------------------------------------------------------
    def handle_event(self, kind: str, payload: object) -> None:
        if kind == "upload_log":
            self.append_upload_log(str(payload))
        elif kind == "upload_link":
            self.append_share_link(payload)  # type: ignore[arg-type]
        elif kind == "upload_progress":
            done, total = payload  # type: ignore[misc]
            self.set_upload_progress(done, total)
        elif kind == "upload_finished":
            self._success("上传完成", "全部压缩包已上传到蓝奏云")
        elif kind == "upload_done":
            self.set_running(False)
            self._progress.hide()

    def append_share_link(self, link: ShareLink) -> None:
        self._share_links.append(link)
        self._result_text.setPlainText("\n".join(format_share_line(item) for item in self._share_links))

    def clear_share_links(self) -> None:
        self._share_links.clear()
        self._result_text.clear()

    def set_upload_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self._progress.setValue(0)
            return
        self._progress.setMaximum(total)
        self._progress.setValue(done)

    def _copy_all_links(self) -> None:
        if not self._share_links:
            self._info("当前没有可复制的链接")
            return
        QApplication.clipboard().setText(
            "\n".join(format_share_line(link) for link in self._share_links)
        )
        self._success("已复制全部链接")

    def set_running(self, running: bool) -> None:
        self._upload_btn.setEnabled(not running)

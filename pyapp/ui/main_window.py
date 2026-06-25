"""主窗口：FluentWindow + 左侧导航，承载压缩打包页与蓝奏上传页。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import (
    FluentIcon,
    FluentWindow,
    NavigationItemPosition,
    SmoothScrollArea,
)

from .pack_interface import PackInterface
from .upload_interface import UploadInterface


def _wrap_scroll(interface: QWidget, object_name: str) -> SmoothScrollArea:
    scroll = SmoothScrollArea()
    scroll.setObjectName(object_name)
    scroll.setWidget(interface)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.enableTransparentBackground()
    return scroll


class MainWindow(FluentWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("批量压缩打包")
        self.resize(960, 880)
        self.setMinimumSize(820, 640)

        # 先建上传页，再把它交给打包页（自动上传时复用其登录态与上传逻辑）
        self.upload_interface = UploadInterface(self)
        self.pack_interface = PackInterface(self.upload_interface, self)

        self.pack_scroll = _wrap_scroll(self.pack_interface, "packScroll")
        self.upload_scroll = _wrap_scroll(self.upload_interface, "uploadScroll")

        self.addSubInterface(self.pack_scroll, FluentIcon.ZIP_FOLDER, "压缩打包")
        self.addSubInterface(
            self.upload_scroll, FluentIcon.CLOUD, "蓝奏上传", NavigationItemPosition.TOP
        )

        try:
            self.setMicaEffectEnabled(True)
        except Exception:  # noqa: BLE001 - 仅 Win11 支持
            pass

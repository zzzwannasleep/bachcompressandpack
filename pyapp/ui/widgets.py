"""界面通用小控件。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import QVBoxLayout
from qfluentwidgets import BodyLabel, CardWidget, StrongBodyLabel, isDarkTheme


class DropArea(CardWidget):
    """支持拖入文件 / 文件夹的卡片，松手后发出 paths_dropped 信号。"""

    paths_dropped = pyqtSignal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(120)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(6)
        self._title = StrongBodyLabel("把文件或文件夹拖到这里")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint = BodyLabel("也可以用上方的“选择文件 / 选择目录”按钮")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(self._title)
        layout.addWidget(self._hint)
        layout.addStretch(1)

        self._normal_style = self.styleSheet()

    # -- drag & drop ----------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_active(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_active(False)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._set_active(False)
        urls = event.mimeData().urls()
        paths = [Path(url.toLocalFile()) for url in urls if url.toLocalFile()]
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()

    def _set_active(self, active: bool) -> None:
        if active:
            accent = "rgba(0,153,255,0.18)" if isDarkTheme() else "rgba(0,120,212,0.12)"
            self.setStyleSheet(f"DropArea{{border:2px dashed #0078d4;background:{accent};}}")
            self._title.setText("松手即可载入")
        else:
            self.setStyleSheet(self._normal_style)
            self._title.setText("把文件或文件夹拖到这里")

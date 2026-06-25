"""程序入口：初始化 Qt、应用 Fluent 主题、显示主窗口。

历史说明：原先这里是 1800 多行的 tkinter 实现，现已重构为
- core.py          打包纯逻辑
- lanzou_client.py 蓝奏云登录/上传/Cookie 获取
- ui/              PyQt6 + QFluentWidgets 界面层
本文件只保留启动逻辑。
"""

from __future__ import annotations

import os
import site
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPS = REPO_ROOT / ".python_deps"
if LOCAL_DEPS.exists():
    site.addsitedir(str(LOCAL_DEPS))
    sys.path.insert(0, str(LOCAL_DEPS))


def resource_path(name: str) -> Path:
    """定位随程序分发的资源（打包后在 _MEIPASS，开发时在仓库根目录）。"""
    base = Path(getattr(sys, "_MEIPASS", REPO_ROOT))
    return base / name


def app_icon():
    from PyQt6.QtGui import QIcon, QPixmap

    # 首选内嵌 base64（打包后必然可用，不依赖数据文件路径）
    try:
        import base64

        from ._icon import ICON_PNG_B64

        pixmap = QPixmap()
        if pixmap.loadFromData(base64.b64decode(ICON_PNG_B64)):
            return QIcon(pixmap)
    except Exception:  # noqa: BLE001
        pass

    for candidate in ("icon.ico", "icon.png"):
        path = resource_path(candidate)
        if path.exists():
            return QIcon(str(path))
    return QIcon()


def _enable_high_dpi() -> None:
    # 让 DPI 缩放对非整数倍屏幕也平滑
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    if os.name == "nt":
        try:
            from ctypes import windll

            windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        except Exception:  # noqa: BLE001
            pass


# 命令行子命令：命中其一就走 CLI，不加载 PyQt6/Qt，便于在无 GUI 的服务器上使用。
CLI_COMMANDS = {"pack", "upload", "tools"}


def main() -> None:
    # 内置网页登录助手分支（由主程序以子进程方式拉起）
    if "--weblogin" in sys.argv:
        idx = sys.argv.index("--weblogin")
        result_path = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        from .weblogin import run_weblogin

        raise SystemExit(run_weblogin(result_path))

    # 命令行模式：`python -m pyapp pack ...` 或带 --cli / --version 时不启动图形界面
    argv = sys.argv[1:]
    if argv and (argv[0] in CLI_COMMANDS or argv[0] in ("--cli", "--version")):
        from .cli import main as cli_main

        cli_argv = argv[1:] if argv[0] == "--cli" else argv
        raise SystemExit(cli_main(cli_argv))

    _enable_high_dpi()

    from PyQt6.QtWidgets import QApplication
    from qfluentwidgets import Theme, setTheme, setThemeColor

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("批量压缩打包")
    app.setWindowIcon(app_icon())

    setTheme(Theme.AUTO)            # 跟随系统亮/暗
    setThemeColor("#0078d4")        # Windows 主题蓝

    from .ui.main_window import MainWindow

    window = MainWindow()
    window.setWindowIcon(app_icon())
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

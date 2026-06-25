# -*- mode: python ; coding: utf-8 -*-
"""跨平台 GUI 便携版打包规格（onedir，Windows / Linux / macOS 通用）。

产物为一个可整体拷贝的文件夹（dist/batch-packager/），可在其中放置 tools/ 携带
7z / rar，开箱即用。

平台差异：
- 内置 WebView2 网页登录依赖 pywebview + pythonnet，仅 Windows 提供；Linux/macOS
  上不打包这部分（其余登录方式：粘贴 Cookie / 从浏览器导入仍可用）。
- 图标仅 Windows 用 .ico。
"""
import sys

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

IS_WINDOWS = sys.platform.startswith("win")

datas = []
binaries = []
hiddenimports = ["darkdetect"]


def _safe_collect_all(name):
    try:
        d, b, h = collect_all(name)
        datas.extend(d)
        binaries.extend(b)
        hiddenimports.extend(h)
    except Exception as exc:  # noqa: BLE001
        print(f"[gui.spec] 跳过 collect_all({name}): {exc}")


def _safe_collect_submodules(name):
    try:
        hiddenimports.extend(collect_submodules(name))
    except Exception as exc:  # noqa: BLE001
        print(f"[gui.spec] 跳过 collect_submodules({name}): {exc}")


try:
    datas += collect_data_files("certifi")
except Exception:
    pass

_safe_collect_all("qfluentwidgets")
for _mod in ("browser_cookie3", "lanzou", "requests_toolbelt"):
    _safe_collect_submodules(_mod)

excludes = [
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineQuick",
    "tkinter",
]

if IS_WINDOWS:
    # WebView2 内置登录所需（仅 Windows）
    try:
        datas += copy_metadata("pywebview")
    except Exception:
        pass
    _safe_collect_all("webview")
    _safe_collect_all("pythonnet")
    _safe_collect_all("clr_loader")
    hiddenimports.append("clr")
else:
    # 非 Windows 不打包 WebView2 相关，避免引入 GTK/.NET 依赖
    excludes += ["webview", "pywebview", "pythonnet", "clr_loader", "clr"]

icon = "icon.ico" if IS_WINDOWS else None


a = Analysis(
    ["launch.pyw"],
    pathex=[".", ".python_deps"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="batch-packager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="batch-packager",
)

# -*- mode: python ; coding: utf-8 -*-
"""跨平台 CLI 单文件打包规格（Windows / Linux / macOS 通用）。

只打包命令行所需内容，不含 PyQt6 / WebView 等 GUI 依赖，产物轻量。
上传相关依赖（requests / lanzou / requests_toolbelt）若已安装则一并收进，
未安装也不影响纯打包功能。
"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = []
binaries = []
hiddenimports = []

# 上传相关依赖按需收集（缺失时忽略，保证纯打包仍可构建）
for module in ("certifi",):
    try:
        datas += collect_data_files(module)
    except Exception:
        pass
for module in ("lanzou", "requests_toolbelt", "browser_cookie3"):
    try:
        hiddenimports += collect_submodules(module)
    except Exception:
        pass


a = Analysis(
    ['cli_entry.py'],
    pathex=['.', '.python_deps'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt6',
        'PyQt5',
        'PySide6',
        'qfluentwidgets',
        'webview',
        'pywebview',
        'tkinter',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='bachpack',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

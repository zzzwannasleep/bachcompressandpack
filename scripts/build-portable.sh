#!/usr/bin/env bash
# 跨平台构建 GUI 便携版（onedir）——Linux / macOS。
# Windows 请用 scripts\build-portable.ps1。
#
#   bash scripts/build-portable.sh
#
# 产物：dist/batch-packager/（可整体拷贝的文件夹）。
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON="${PYTHON:-python3}"

echo "==> 安装构建依赖（PyQt6 + QFluentWidgets + 上传依赖 + PyInstaller）"
"$PYTHON" -m pip install --upgrade pyinstaller
"$PYTHON" -m pip install \
  "PyQt6==6.11.0" "PyQt6-Fluent-Widgets==1.11.2" \
  requests requests-toolbelt "lanzou-api==2.6.10" "browser-cookie3==0.20.1"

echo "==> 用 PyInstaller 构建 GUI 便携版"
QT_QPA_PLATFORM=offscreen "$PYTHON" -m PyInstaller --noconfirm --clean \
  --distpath dist --workpath build \
  batch-packager-gui.spec

echo "==> 完成：$PROJECT_ROOT/dist/batch-packager/"
echo "提示：可把 7z(7zz) / rar 放到 dist/batch-packager/tools/ 下随包携带。"

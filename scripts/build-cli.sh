#!/usr/bin/env bash
# 跨平台构建 CLI 单文件可执行程序（Linux / macOS）。
# Windows 请用 scripts\build-cli.ps1。
#
#   bash scripts/build-cli.sh
#
# 产物：dist/bachpack（Linux/macOS）。
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON="${PYTHON:-python3}"

echo "==> 安装构建依赖（pyinstaller + 上传依赖，纯打包可跳过上传依赖）"
"$PYTHON" -m pip install --upgrade pyinstaller
"$PYTHON" -m pip install requests requests-toolbelt "lanzou-api==2.6.10" "browser-cookie3==0.20.1" || \
  echo "（上传依赖安装失败，仅纯打包功能可用）"

echo "==> 用 PyInstaller 构建 CLI"
"$PYTHON" -m PyInstaller --noconfirm --clean \
  --distpath dist --workpath build \
  batch-packager-cli.spec

echo "==> 完成：$PROJECT_ROOT/dist/bachpack"
"$PROJECT_ROOT/dist/bachpack" --version || true

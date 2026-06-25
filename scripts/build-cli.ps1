# 构建 Windows CLI 单文件可执行程序（bachpack.exe）。
#   powershell -ExecutionPolicy Bypass -File .\scripts\build-cli.ps1
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

Write-Host "==> 安装构建依赖"
python -m pip install --upgrade pyinstaller
try {
  python -m pip install requests requests-toolbelt "lanzou-api==2.6.10" "browser-cookie3==0.20.1"
} catch {
  Write-Host "（上传依赖安装失败，仅纯打包功能可用）"
}

Write-Host "==> 用 PyInstaller 构建 CLI"
python -m PyInstaller --noconfirm --clean `
  --distpath dist --workpath build `
  batch-packager-cli.spec

Write-Host "==> 完成: $projectRoot\dist\bachpack.exe"
& "$projectRoot\dist\bachpack.exe" --version

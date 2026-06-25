$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$depsRoot = (Resolve-Path ".python_deps").Path
$pywin32Paths = @(
  $depsRoot,
  (Join-Path $depsRoot "win32"),
  (Join-Path $depsRoot "win32\lib"),
  (Join-Path $depsRoot "pythonwin")
)
$env:PYTHONPATH = ($pywin32Paths -join ";")

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --noupx `
  --windowed `
  --name batch-packager `
  --icon icon.ico `
  --distpath dist `
  --workpath build `
  --specpath . `
  --paths . `
  --paths .python_deps `
  --collect-submodules browser_cookie3 `
  --collect-submodules lanzou `
  --collect-submodules requests_toolbelt `
  --collect-data certifi `
  --collect-all qfluentwidgets `
  --collect-all webview `
  --collect-all pythonnet `
  --collect-all clr_loader `
  --copy-metadata pywebview `
  --hidden-import darkdetect `
  --hidden-import clr `
  --exclude-module PyQt6.QtWebEngineCore `
  --exclude-module PyQt6.QtWebEngineWidgets `
  --exclude-module PyQt6.QtWebEngineQuick `
  --exclude-module tkinter `
  launch.pyw

Write-Host "Built: $projectRoot\dist\batch-packager.exe"

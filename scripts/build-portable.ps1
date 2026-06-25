param(
  [string]$SevenZipPath = "",
  [string]$RarPath = "",
  [switch]$SkipArchive
)

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

$portableDistRoot = Join-Path $projectRoot "dist\portable"
$portableSpecRoot = Join-Path $projectRoot "build\portable-spec"
$portableRoot = Join-Path $portableDistRoot "batch-packager"
$archivePath = Join-Path $projectRoot "dist\batch-packager-portable.zip"
$toolsRoot = Join-Path $portableRoot "tools"

New-Item -ItemType Directory -Force -Path $portableSpecRoot | Out-Null

function Resolve-OptionalToolPath {
  param(
    [string]$ExplicitPath,
    [string[]]$DefaultCandidates
  )

  if ($ExplicitPath) {
    return (Resolve-Path -LiteralPath $ExplicitPath).Path
  }

  foreach ($candidate in $DefaultCandidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }

  return $null
}

function Copy-OptionalTool {
  param(
    [string]$SourcePath,
    [string]$TargetDir,
    [string]$Kind
  )

  if (-not $SourcePath) {
    return $false
  }

  New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
  $sourceItem = Get-Item -LiteralPath $SourcePath
  Copy-Item -LiteralPath $sourceItem.FullName -Destination (Join-Path $TargetDir $sourceItem.Name) -Force

  if ($Kind -eq "7zip" -and $sourceItem.Name -ieq "7z.exe") {
    $dllPath = Join-Path $sourceItem.DirectoryName "7z.dll"
    if (Test-Path -LiteralPath $dllPath) {
      Copy-Item -LiteralPath $dllPath -Destination (Join-Path $TargetDir "7z.dll") -Force
    }
  }

  if ($Kind -eq "rar") {
    Get-ChildItem -LiteralPath $sourceItem.DirectoryName -Filter "*.dll" -File |
      Where-Object { $_.Name -match "^Rar" } |
      ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $TargetDir $_.Name) -Force
      }
  }

  return $true
}

# --icon 的相对路径会按 specpath 解析，这里 specpath 不在项目根，故用绝对路径
$iconIco = Join-Path $projectRoot "icon.ico"

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onedir `
  --noupx `
  --windowed `
  --name batch-packager `
  --icon $iconIco `
  --distpath $portableDistRoot `
  --workpath build `
  --specpath $portableSpecRoot `
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

if (Test-Path -LiteralPath $toolsRoot) {
  Remove-Item -LiteralPath $toolsRoot -Recurse -Force
}

$sevenZipCandidates = @(
  (Join-Path $env:ProgramFiles "7-Zip\7zz.exe"),
  (Join-Path $env:ProgramFiles "7-Zip\7za.exe"),
  (Join-Path $env:ProgramFiles "7-Zip\7z.exe"),
  (Join-Path ${env:ProgramFiles(x86)} "7-Zip\7zz.exe"),
  (Join-Path ${env:ProgramFiles(x86)} "7-Zip\7za.exe"),
  (Join-Path ${env:ProgramFiles(x86)} "7-Zip\7z.exe")
)
$rarCandidates = @(
  (Join-Path $env:ProgramFiles "WinRAR\rar.exe"),
  (Join-Path $env:ProgramFiles "WinRAR\WinRAR.exe"),
  (Join-Path ${env:ProgramFiles(x86)} "WinRAR\rar.exe"),
  (Join-Path ${env:ProgramFiles(x86)} "WinRAR\WinRAR.exe")
)

$resolvedSevenZip = Resolve-OptionalToolPath -ExplicitPath $SevenZipPath -DefaultCandidates $sevenZipCandidates
$resolvedRar = Resolve-OptionalToolPath -ExplicitPath $RarPath -DefaultCandidates $rarCandidates

$bundledTools = @()
if (Copy-OptionalTool -SourcePath $resolvedSevenZip -TargetDir (Join-Path $toolsRoot "7zip") -Kind "7zip") {
  $bundledTools += "7-Zip"
}
if (Copy-OptionalTool -SourcePath $resolvedRar -TargetDir (Join-Path $toolsRoot "rar") -Kind "rar") {
  $bundledTools += "RAR"
}

if (-not $SkipArchive) {
  if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
  }
  Compress-Archive -LiteralPath $portableRoot -DestinationPath $archivePath -Force
}

Write-Host "Portable folder: $portableRoot"
if (-not $SkipArchive) {
  Write-Host "Portable zip: $archivePath"
}

if ($bundledTools.Count -gt 0) {
  Write-Host ("Bundled tools: " + ($bundledTools -join ", "))
} else {
  Write-Host "Bundled tools: none found locally. You can pass -SevenZipPath / -RarPath to include them."
}

Write-Host "Note: redistributing rar.exe / WinRAR.exe may require confirming its license terms yourself."

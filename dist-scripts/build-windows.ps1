$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$BuildVenv = Join-Path $ProjectRoot ".venv-build"
$AppName = "OwletMonitor"

Set-Location $ProjectRoot

py -3 -m venv $BuildVenv
& (Join-Path $BuildVenv "Scripts\python.exe") -m pip install --upgrade pip
& (Join-Path $BuildVenv "Scripts\python.exe") -m pip install aiohttp certifi wxPython pyinstaller

& (Join-Path $BuildVenv "Scripts\pyinstaller.exe") `
  --noconfirm `
  --clean `
  --windowed `
  --name $AppName `
  --add-data "wx_monitor_app/layout.json;wx_monitor_app" `
  --add-data "wx_monitor_app/icon.jpeg;wx_monitor_app" `
  --add-data "src;src" `
  "wx_monitor_app/app.py"

Write-Host ""
Write-Host "Built: $(Join-Path $ProjectRoot "dist\$AppName\$AppName.exe")"
Write-Host ""
Write-Host "Put login.json next to the exe before giving it to a user:"
Write-Host "  $(Join-Path $ProjectRoot "dist\$AppName\login.json")"
Write-Host "  $(Join-Path $ProjectRoot "dist\$AppName\$AppName.exe")"

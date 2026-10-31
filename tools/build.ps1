# Build dist\GameShop.exe: one file; double-click runs the tray icon (no console window).
# Run from the repo root: powershell -ExecutionPolicy Bypass -File tools\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "Create the venv first: python -m venv .venv; .venv\Scripts\pip install -r requirements.txt" }
Push-Location $root
& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name GameShop `
    --add-data "gameshop\systems.toml;gameshop" `
    --add-data "gameshop\web;gameshop\web" `
    --hidden-import pystray._win32 `
    --collect-submodules uvicorn `
    gameshop\launcher.py
Pop-Location
Write-Host "Built dist\GameShop.exe"

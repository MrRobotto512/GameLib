# Build dist\GameShop.exe (one file; double-click runs the tray icon, no console window) and, when
# Inno Setup 6 is installed, dist\GameShopSetup.exe, which installs it into Program Files.
# Run from the repo root: powershell -ExecutionPolicy Bypass -File tools\build.ps1 [-DistPath <folder>]
param([string]$DistPath = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "Create the venv first: python -m venv .venv; .venv\Scripts\pip install -r requirements.txt" }
if (-not $DistPath) { $DistPath = Join-Path $root "dist" }
Push-Location $root
& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name GameShop --icon tools\gameshop.ico `
    --distpath $DistPath `
    --add-data "gameshop\systems.toml;gameshop" `
    --add-data "gameshop\web;gameshop\web" `
    --add-data "gameshop\payloads;gameshop\payloads" `
    --add-data "gameshop\ps5app;gameshop\ps5app" `
    --hidden-import pystray._win32 `
    --collect-submodules uvicorn `
    gameshop\launcher.py
if ($LASTEXITCODE) { Pop-Location; Write-Error "PyInstaller failed" }
Write-Host "Built $DistPath\GameShop.exe"

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    $version = (& $py -c "import gameshop; print(gameshop.__version__)").Trim()
    & $iscc /Q "/DAppVersion=$version" "/DDist=$DistPath" "/DOutDir=$DistPath" tools\installer.iss
    if ($LASTEXITCODE) { Pop-Location; Write-Error "Inno Setup failed" }
    Write-Host "Built $DistPath\GameShopSetup.exe"
} else {
    Write-Host "Inno Setup 6 is not installed, so no GameShopSetup.exe (winget install JRSoftware.InnoSetup)."
}
Pop-Location

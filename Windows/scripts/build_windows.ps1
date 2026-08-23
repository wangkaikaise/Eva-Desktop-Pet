$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".venv")) {
    py -3.13 -m venv .venv
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements-dev.txt
$env:QT_QPA_PLATFORM = "offscreen"
& $Python -m ruff check main.py eva_window.py metrics.py reminders.py settings.py settings_dialog.py state_machine.py tests
& $Python -m pytest
& (Join-Path $PSScriptRoot "prepare_vendor.ps1")
& $Python -m PyInstaller EvaDesktopPet.spec --noconfirm --clean

$Version = & $Python -c "from version import APP_VERSION; print(APP_VERSION)"
$Archive = Join-Path $Root "dist\Eva-Desktop-Pet-Windows-$Version-x64.zip"
if (Test-Path $Archive) {
    Remove-Item -LiteralPath $Archive -Force
}
Compress-Archive -Path "dist\EvaDesktopPet\*" -DestinationPath $Archive -CompressionLevel Optimal
Write-Host "Built $Archive"

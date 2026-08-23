$ErrorActionPreference = "Stop"

function Assert-ProcessSucceeded([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".venv")) {
    py -3.13 -m venv .venv
    Assert-ProcessSucceeded "Create virtual environment"
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -m pip install --upgrade pip
Assert-ProcessSucceeded "Upgrade pip"
& $Python -m pip install -r requirements-dev.txt
Assert-ProcessSucceeded "Install dependencies"
$env:QT_QPA_PLATFORM = "offscreen"
& $Python -m ruff check main.py eva_window.py metrics.py reminders.py settings.py settings_dialog.py state_machine.py tests
Assert-ProcessSucceeded "Ruff"
& $Python -m pytest
Assert-ProcessSucceeded "Pytest"
& (Join-Path $PSScriptRoot "prepare_vendor.ps1")
& $Python -m PyInstaller EvaDesktopPet.spec --noconfirm --clean
Assert-ProcessSucceeded "PyInstaller"

$Version = & $Python -c "from version import APP_VERSION; print(APP_VERSION)"
Assert-ProcessSucceeded "Read application version"
$Archive = Join-Path $Root "dist\Eva-Desktop-Pet-Windows-$Version-x64.zip"
if (Test-Path $Archive) {
    Remove-Item -LiteralPath $Archive -Force
}
Compress-Archive -Path "dist\EvaDesktopPet\*" -DestinationPath $Archive -CompressionLevel Optimal
Write-Host "Built $Archive"

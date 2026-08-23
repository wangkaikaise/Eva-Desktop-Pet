$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Vendor = Join-Path $Root "vendor"
$Temp = Join-Path ([System.IO.Path]::GetTempPath()) ("eva-vendor-" + [guid]::NewGuid())
$LhmZip = Join-Path $Temp "LibreHardwareMonitor.zip"
$LhmDir = Join-Path $Temp "lhm"
$PawnIo = Join-Path $Temp "PawnIO_setup.exe"

$LhmUrl = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v0.9.6/LibreHardwareMonitor.zip"
$LhmSha256 = "086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001"
$PawnIoUrl = "https://github.com/namazso/PawnIO.Setup/releases/download/2.2.0/PawnIO_setup.exe"
$PawnIoSha256 = "1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032"

function Assert-Hash([string]$Path, [string]$Expected) {
    $Actual = (Get-FileHash -Algorithm SHA256 $Path).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected) {
        throw "SHA-256 mismatch for $Path. Expected $Expected, got $Actual"
    }
}

try {
    New-Item -ItemType Directory -Force -Path $Temp, $Vendor | Out-Null
    Invoke-WebRequest -Uri $LhmUrl -OutFile $LhmZip
    Assert-Hash $LhmZip $LhmSha256
    Expand-Archive -Path $LhmZip -DestinationPath $LhmDir
    Get-ChildItem -Path $LhmDir -Filter "*.dll" -File |
        Copy-Item -Destination $Vendor -Force

    Invoke-WebRequest -Uri $PawnIoUrl -OutFile $PawnIo
    Assert-Hash $PawnIo $PawnIoSha256
    Copy-Item $PawnIo (Join-Path $Vendor "PawnIO_setup.exe") -Force
    Write-Host "Prepared verified hardware-monitoring dependencies in $Vendor"
}
finally {
    if (Test-Path $Temp) {
        Remove-Item -LiteralPath $Temp -Recurse -Force
    }
}

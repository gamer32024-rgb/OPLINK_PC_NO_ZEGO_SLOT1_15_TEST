[CmdletBinding()]
param(
    [string] $Uf2Path = "",
    [string] $VolumeLabel = "RPI-RP2"
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $Uf2Path) {
    $Uf2Path = Join-Path $Root "build\phase0\pico_h_phase0_smoke.uf2"
}

if (-not (Test-Path -LiteralPath $Uf2Path)) {
    throw "UF2 not found: $Uf2Path"
}

$Disk = Get-CimInstance Win32_LogicalDisk |
    Where-Object { $_.VolumeName -eq $VolumeLabel -and $_.DriveType -eq 2 } |
    Select-Object -First 1

if (-not $Disk) {
    throw "BOOTSEL disk with label $VolumeLabel was not found."
}

$Destination = Join-Path ($Disk.DeviceID + "\") (Split-Path -Leaf $Uf2Path)
$Hash = Get-FileHash -LiteralPath $Uf2Path -Algorithm SHA256

Write-Host "Flashing UF2:"
Write-Host "  Source: $Uf2Path"
Write-Host "  Target: $Destination"
Write-Host "  Bytes:  $((Get-Item -LiteralPath $Uf2Path).Length)"
Write-Host "  SHA256: $($Hash.Hash)"

Copy-Item -LiteralPath $Uf2Path -Destination $Destination -Force

Write-Host "UF2 copied. The Pico should auto-eject and reboot."

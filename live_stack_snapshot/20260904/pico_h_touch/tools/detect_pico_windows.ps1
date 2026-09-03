[CmdletBinding()]
param()

$ErrorActionPreference = "SilentlyContinue"

function Write-Section {
    param([string] $Name)
    Write-Host ""
    Write-Host "== $Name =="
}

$devicePattern = "Pico|RP2|RPI|Raspberry|BOOTSEL|USB Serial|Touch|Digitizer|Unknown USB|Mass Storage"
$idPattern = "VID_2E8A|VID_239A|RP2|RPI"

Write-Section "Present PnP devices"
Get-PnpDevice -PresentOnly |
    Where-Object {
        $_.FriendlyName -match $devicePattern -or
        $_.InstanceId -match $idPattern
    } |
    Select-Object Status, Class, FriendlyName, InstanceId |
    Format-Table -AutoSize

Write-Section "Raspberry Pi USB VID search"
Get-CimInstance Win32_PnPEntity |
    Where-Object {
        $_.PNPDeviceID -match $idPattern -or
        $_.Name -match $devicePattern
    } |
    Select-Object Name, Status, PNPClass, PNPDeviceID, ConfigManagerErrorCode |
    Format-Table -AutoSize

Write-Section "Serial ports"
Get-CimInstance Win32_SerialPort |
    Select-Object DeviceID, Name, PNPDeviceID |
    Format-Table -AutoSize

Write-Section "Removable disks"
Get-CimInstance Win32_LogicalDisk |
    Where-Object { $_.DriveType -eq 2 -or $_.VolumeName -eq "RPI-RP2" } |
    Select-Object DeviceID, VolumeName, DriveType, FileSystem, Size |
    Format-Table -AutoSize

Write-Section "Summary"
$rpiDisk = Get-CimInstance Win32_LogicalDisk |
    Where-Object { $_.VolumeName -eq "RPI-RP2" } |
    Select-Object -First 1

$piUsb = Get-CimInstance Win32_PnPEntity |
    Where-Object { $_.PNPDeviceID -match "VID_2E8A" } |
    Select-Object -First 1

if ($rpiDisk) {
    Write-Host "BOOTSEL mass-storage detected: $($rpiDisk.DeviceID) RPI-RP2"
} elseif ($piUsb) {
    Write-Host "Raspberry Pi USB VID detected, but RPI-RP2 disk is not mounted."
} else {
    Write-Host "No Raspberry Pi Pico/RP2040 USB device detected."
}

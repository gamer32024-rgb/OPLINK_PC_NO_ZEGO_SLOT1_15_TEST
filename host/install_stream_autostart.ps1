[CmdletBinding()]
param([switch]$StartNow)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$Watchdog = Join-Path $Root "start_stream_autostart.ps1"
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$StartupFolder = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupFolder "OPLINK_PC Stream Host.lnk"

if (!(Test-Path -LiteralPath $Watchdog -PathType Leaf)) {
    throw "Missing stream watchdog: $Watchdog"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $PowerShell
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Watchdog`""
$shortcut.WorkingDirectory = $Root
$shortcut.Description = "Start and monitor the OPLINK_PC no-ZEGO stream host after Windows logon"
$shortcut.Save()

if ($StartNow) {
    $pidPath = Join-Path $Root "runtime\autostart.pid"
    $alreadyRunning = $false
    if (Test-Path -LiteralPath $pidPath) {
        $watchdogPid = [int](Get-Content -LiteralPath $pidPath -Raw)
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$watchdogPid" -ErrorAction SilentlyContinue
        $alreadyRunning = $process -and $process.CommandLine -and
            $process.CommandLine.IndexOf($Watchdog, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    }
    if (!$alreadyRunning) {
        Start-Process -FilePath $PowerShell `
            -ArgumentList "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Watchdog`"" `
            -WorkingDirectory $Root -WindowStyle Hidden | Out-Null
    }
}

Write-Host "Installed post-logon OPLINK_PC stream autostart: $ShortcutPath"
Write-Host "The watchdog waits for GUI_TEST_PC and all 15 game windows, then keeps the stream host ready."

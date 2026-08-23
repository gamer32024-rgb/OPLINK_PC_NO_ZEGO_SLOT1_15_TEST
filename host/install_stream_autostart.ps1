[CmdletBinding()]
param([switch]$StartNow)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$Watchdog = Join-Path $Root "start_stream_autostart.ps1"
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$StartupFolder = [Environment]::GetFolderPath("Startup")
$LegacyShortcutPath = Join-Path $StartupFolder "OPLINK_PC Stream Host.lnk"
$ShortcutPath = Join-Path $StartupFolder "OPLINK 720p Native Stream Host.lnk"

if (!(Test-Path -LiteralPath $Watchdog -PathType Leaf)) {
    throw "Missing stream watchdog: $Watchdog"
}

$legacyPidPath = Join-Path $Root "runtime\autostart.pid"
if (Test-Path -LiteralPath $legacyPidPath) {
    $legacyPid = [int](Get-Content -LiteralPath $legacyPidPath -Raw)
    $legacyProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$legacyPid" -ErrorAction SilentlyContinue
    if ($legacyProcess -and $legacyProcess.CommandLine -and
        $legacyProcess.CommandLine.IndexOf($Watchdog, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        Stop-Process -Id $legacyPid -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $legacyPidPath -Force -ErrorAction SilentlyContinue
}

$shell = New-Object -ComObject WScript.Shell
Remove-Item -LiteralPath $LegacyShortcutPath -Force -ErrorAction SilentlyContinue
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $PowerShell
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Watchdog`""
$shortcut.WorkingDirectory = $Root
$shortcut.Description = "Start and monitor the OPLINK 720p native single-stream host after Windows logon"
$shortcut.Save()

if ($StartNow) {
    $pidPath = Join-Path $Root "runtime\native_single_autostart.pid"
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

Write-Host "Installed post-logon OPLINK 720p native stream autostart: $ShortcutPath"
Write-Host "The watchdog waits for GUI_TEST_PC and at least one game window, then keeps native_single 5112 ready for Slots 1-20."

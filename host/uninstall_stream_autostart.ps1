[CmdletBinding()]
param([switch]$StopStreamHost)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$Watchdog = Join-Path $Root "start_stream_autostart.ps1"
$StartupFolder = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupFolder "OPLINK 720p Native Stream Host.lnk"
$LegacyShortcutPath = Join-Path $StartupFolder "OPLINK_PC Stream Host.lnk"
$PidPath = Join-Path $Root "runtime\native_single_autostart.pid"
$LegacyPidPath = Join-Path $Root "runtime\autostart.pid"

Remove-Item -LiteralPath $ShortcutPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $LegacyShortcutPath -Force -ErrorAction SilentlyContinue
foreach ($watchdogPidPath in @($PidPath, $LegacyPidPath)) {
    if (Test-Path -LiteralPath $watchdogPidPath) {
        $watchdogPid = [int](Get-Content -LiteralPath $watchdogPidPath -Raw)
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$watchdogPid" -ErrorAction SilentlyContinue
        if ($process -and $process.CommandLine -and
            $process.CommandLine.IndexOf($Watchdog, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            Stop-Process -Id $watchdogPid -Force -ErrorAction SilentlyContinue
        }
        Remove-Item -LiteralPath $watchdogPidPath -Force -ErrorAction SilentlyContinue
    }
}
if ($StopStreamHost) {
    & (Join-Path $Root "stop_stream_test.ps1") -IgnoreMissing
}
Write-Host "Removed the OPLINK 720p native stream autostart shortcut and watchdog."

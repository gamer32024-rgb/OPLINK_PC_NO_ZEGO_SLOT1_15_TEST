[CmdletBinding()]
param([switch]$StopStreamHost)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$Watchdog = Join-Path $Root "start_stream_autostart.ps1"
$ShortcutPath = Join-Path ([Environment]::GetFolderPath("Startup")) "OPLINK_PC Stream Host.lnk"
$PidPath = Join-Path $Root "runtime\autostart.pid"

Remove-Item -LiteralPath $ShortcutPath -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $PidPath) {
    $watchdogPid = [int](Get-Content -LiteralPath $PidPath -Raw)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$watchdogPid" -ErrorAction SilentlyContinue
    if ($process -and $process.CommandLine -and
        $process.CommandLine.IndexOf($Watchdog, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        Stop-Process -Id $watchdogPid -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
}
if ($StopStreamHost) {
    & (Join-Path $Root "stop_stream_test.ps1") -IgnoreMissing
}
Write-Host "Removed the OPLINK_PC stream autostart shortcut and watchdog."

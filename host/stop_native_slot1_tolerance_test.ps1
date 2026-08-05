[CmdletBinding()]
param(
    [switch]$IgnoreMissing,
    [ValidateRange(1024, 65535)]
    [int]$ServeHttpsPort = 8443
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$Runtime = Join-Path $Root "runtime\native_slot1_tolerance"
$StatePath = Join-Path $Runtime "state.json"
$NativeStatePath = Join-Path $Runtime "native_single_publisher.json"
$Tailscale = "C:\Program Files\Tailscale\tailscale.exe"
$ServerScript = [System.IO.Path]::GetFullPath((Join-Path $Root "stream_test_server.py"))

function Stop-IdentifiedProcess {
    param([int]$Pid, [string]$ExpectedPath)
    if ($Pid -le 0 -or !$ExpectedPath) { return $false }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$Pid" -ErrorAction SilentlyContinue
    if (!$process) { return $false }
    if (-not [string]::Equals(
            [string]$process.ExecutablePath,
            [string]$ExpectedPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) { return $false }
    Stop-Process -Id $Pid -Force -ErrorAction SilentlyContinue
    return $true
}

$stopped = 0
$state = $null
if (Test-Path -LiteralPath $StatePath) {
    $state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
}
if (Test-Path -LiteralPath $NativeStatePath) {
    try {
        $native = Get-Content -LiteralPath $NativeStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (Stop-IdentifiedProcess ([int]$native.publisher_pid) ([string]$native.publisher_executable)) { $stopped++ }
        if (Stop-IdentifiedProcess ([int]$native.router_pid) ([string]$native.router_executable)) { $stopped++ }
    } catch {
    }
}
if ($state -and $state.pids.api) {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    $fallback = "C:\Users\andyb\Documents\star_cros_bot\.venv\Scripts\python.exe"
    foreach ($candidate in @($python, $fallback)) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            if (Stop-IdentifiedProcess ([int]$state.pids.api) $candidate) { $stopped++; break }
        }
    }
}
if (Test-Path -LiteralPath $Tailscale -PathType Leaf) {
    & $Tailscale serve --https=$ServeHttpsPort --set-path=/oplink-test off | Out-Null
    & $Tailscale serve --https=$ServeHttpsPort --set-path=/oplink-whep off | Out-Null
}
Remove-Item -LiteralPath $StatePath,$NativeStatePath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $Runtime "input_token.txt") -Force -ErrorAction SilentlyContinue
if ($stopped -eq 0 -and !$state -and !$IgnoreMissing) {
    throw "No native Slot 1 tolerance test state or owned process was found."
}
Write-Host "Stopped $stopped native Slot 1 tolerance process(es). Legacy OPLINK_PC was not targeted."

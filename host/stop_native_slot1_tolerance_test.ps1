[CmdletBinding()]
param(
    [switch]$IgnoreMissing,
    [ValidateRange(1024, 65535)]
    [int]$ApiPort = 5112,
    [ValidateRange(1024, 65535)]
    [int]$ServeHttpsPort = 8443
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$Runtime = Join-Path $Root "runtime\native_slot1_tolerance"
$StatePath = Join-Path $Runtime "state.json"
$NativeStatePath = Join-Path $Runtime "native_single_publisher.json"
$OverviewStatePath = Join-Path $Runtime "overview_publisher.json"
$Tailscale = "C:\Program Files\Tailscale\tailscale.exe"
$ServerScript = [System.IO.Path]::GetFullPath((Join-Path $Root "stream_test_server.py"))
$Python = $null

function Stop-IdentifiedProcess {
    param([int]$ProcessId, [string]$ExpectedPath)
    if ($ProcessId -le 0 -or !$ExpectedPath) { return $false }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (!$process) { return $false }
    if (-not [string]::Equals(
            [string]$process.ExecutablePath,
            [string]$ExpectedPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) { return $false }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    return $true
}

function Stop-StagingApiProcesses {
    param([int]$Port)

    $portPattern = "(?i)(?:^|\s)--port\s+$Port(?:\s|$)"
    $apiProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $commandLine = [string]$_.CommandLine
        $executable = [string]$_.ExecutablePath
        $executable -match '(?i)python(?:w)?\.exe$' -and
        $commandLine.IndexOf($ServerScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $commandLine.IndexOf($Runtime, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $commandLine -match $portPattern
    })
    $parentIds = @($apiProcesses | ForEach-Object { [int]$_.ParentProcessId })
    $ordered = @($apiProcesses | Sort-Object @{ Expression = {
        if ($parentIds -contains [int]$_.ProcessId) { 1 } else { 0 }
    }})
    $count = 0
    foreach ($process in $ordered) {
        Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction SilentlyContinue
        $count++
    }
    return $count
}

$stopped = 0
$state = $null
if (Test-Path -LiteralPath $StatePath) {
    $state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
}
$targetApiPort = if ($state -and $state.api_port) { [int]$state.api_port } else { $ApiPort }
if (Test-Path -LiteralPath $NativeStatePath) {
    try {
        $native = Get-Content -LiteralPath $NativeStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (Stop-IdentifiedProcess ([int]$native.publisher_pid) ([string]$native.publisher_executable)) { $stopped++ }
        if (Stop-IdentifiedProcess ([int]$native.router_pid) ([string]$native.router_executable)) { $stopped++ }
    } catch {
    }
}
if (Test-Path -LiteralPath $OverviewStatePath) {
    try {
        $overview = Get-Content -LiteralPath $OverviewStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (Stop-IdentifiedProcess ([int]$overview.publisher_pid) ([string]$overview.publisher_executable)) { $stopped++ }
    } catch {
    }
}
$stopped += Stop-StagingApiProcesses -Port $targetApiPort
if (Test-Path -LiteralPath $Tailscale -PathType Leaf) {
    & $Tailscale serve --https=$ServeHttpsPort --set-path=/oplink-test off | Out-Null
    & $Tailscale serve --https=$ServeHttpsPort --set-path=/oplink-whep off | Out-Null
    & $Tailscale serve --https=$ServeHttpsPort --set-path=/gui-test-pc off | Out-Null
}
$restore = $state.layout.before | Select-Object -First 1
if ($restore -and $restore.client_width -and $restore.client_height) {
    $Python = (Get-Command python -ErrorAction SilentlyContinue).Source
    $fallback = "C:\Users\andyb\Documents\star_cros_bot\.venv\Scripts\python.exe"
    foreach ($candidate in @($Python, $fallback)) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            $Python = $candidate
            break
        }
    }
    if ($Python) {
        & $Python $ServerScript --slots 1 --repair-stream-layout `
            --client-width ([int]$restore.client_width) --client-height ([int]$restore.client_height) | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Native test stopped, but Slot 1 geometry could not be restored automatically."
        }
    }
}
Remove-Item -LiteralPath $StatePath,$NativeStatePath,$OverviewStatePath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $Runtime "input_token.txt") -Force -ErrorAction SilentlyContinue
if ($stopped -eq 0 -and !$state -and !$IgnoreMissing) {
    throw "No native Slot 1 tolerance test state or owned process was found."
}
Write-Host "Stopped $stopped native Slot 1 tolerance process(es). Legacy OPLINK_PC was not targeted."

[CmdletBinding()]
param([switch]$IgnoreMissing)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$StatePath = Join-Path $Root "runtime\state.json"
$ActivePublisherPath = Join-Path $Root "runtime\active_publisher.json"
$NativePublisherPath = Join-Path $Root "runtime\native_single_publisher.json"
$ServerScript = [System.IO.Path]::GetFullPath((Join-Path $Root "stream_test_server.py"))
$RuntimeConfig = [System.IO.Path]::GetFullPath((Join-Path $Root "runtime\mediamtx.runtime.yml"))
$NativeRouter = [System.IO.Path]::GetFullPath(
    (Join-Path $Root "tools\oplink_capture_router\oplink_capture_router.exe")
)

function Test-CommandLineContains {
    param([string]$CommandLine, [string]$Needle)
    if (!$CommandLine) { return $false }
    return $CommandLine.IndexOf($Needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

$nativeProcessIdentities = @{}
if (Test-Path -LiteralPath $NativePublisherPath) {
    try {
        $nativeState = Get-Content -LiteralPath $NativePublisherPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($nativeState.publisher_pid -and $nativeState.publisher_executable) {
            $nativeProcessIdentities[[int]$nativeState.publisher_pid] =
                [System.IO.Path]::GetFullPath([string]$nativeState.publisher_executable)
        }
        if ($nativeState.router_pid -and $nativeState.router_executable) {
            $nativeProcessIdentities[[int]$nativeState.router_pid] =
                [System.IO.Path]::GetFullPath([string]$nativeState.router_executable)
        }
    } catch {
        Write-Warning "Ignoring invalid native publisher state: $($_.Exception.Message)"
    }
}

# Runtime PID files survive a reboot and Windows can reuse those PIDs. Identify only
# processes whose command lines prove that they belong to this stream host.
$ownedProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $name = [string]$_.Name
    $commandLine = [string]$_.CommandLine
    $processId = [int]$_.ProcessId
    $matchesNativeState = $nativeProcessIdentities.ContainsKey($processId) -and
        [string]::Equals(
            [string]$_.ExecutablePath,
            [string]$nativeProcessIdentities[$processId],
            [System.StringComparison]::OrdinalIgnoreCase
        )
    $isApi = $name -match '^pythonw?\.exe$' -and (Test-CommandLineContains $commandLine $ServerScript)
    $isMediaMtx = $name -ieq 'mediamtx.exe' -and (Test-CommandLineContains $commandLine $RuntimeConfig)
    $isPublisher = $name -ieq 'ffmpeg.exe' -and
        (Test-CommandLineContains $commandLine 'gfxcapture=hwnd=') -and
        (Test-CommandLineContains $commandLine 'rtsp://127.0.0.1:8554/slot')
    $isNativePublisher = $name -ieq 'ffmpeg.exe' -and
        (Test-CommandLineContains $commandLine '-f rawvideo') -and
        (Test-CommandLineContains $commandLine 'rtsp://127.0.0.1:8554/oplink_active')
    $isNativeRouter = $name -ieq 'oplink_capture_router.exe' -and
        [string]::Equals(
            [string]$_.ExecutablePath,
            $NativeRouter,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    $matchesNativeState -or $isApi -or $isMediaMtx -or $isPublisher -or
        $isNativePublisher -or $isNativeRouter
})

foreach ($process in $ownedProcesses | Sort-Object @{ Expression = {
    if ($_.Name -ieq 'ffmpeg.exe') { 0 } elseif ($_.Name -match '^python') { 1 } else { 2 }
} }) {
    Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction SilentlyContinue
}

$hadRuntimeState = (Test-Path -LiteralPath $StatePath) -or
    (Test-Path -LiteralPath $ActivePublisherPath) -or
    (Test-Path -LiteralPath $NativePublisherPath)
Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $ActivePublisherPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $NativePublisherPath -Force -ErrorAction SilentlyContinue

if ($ownedProcesses.Count -eq 0 -and !$hadRuntimeState -and !$IgnoreMissing) {
    throw "No active OPLINK_PC stream processes or runtime state were found."
}
Write-Host "Stopped $($ownedProcesses.Count) verified OPLINK_PC stream process(es)."

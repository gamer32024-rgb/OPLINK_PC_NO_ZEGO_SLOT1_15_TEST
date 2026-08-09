param(
    [ValidateRange(256, 3840)]
    [int]$OutputWidth = 1280,
    [ValidateRange(144, 2160)]
    [int]$OutputHeight = 720,
    [ValidateRange(1, 60)]
    [int]$Fps = 30,
    [ValidateRange(250, 20000)]
    [int]$BitrateKbps = 2200,
    [ValidateSet("auto", "nvenc", "mf", "x264")]
    [string]$Encoder = "mf",
    [ValidateRange(1024, 65535)]
    [int]$ApiPort = 5112,
    [ValidateRange(1024, 65535)]
    [int]$ServeHttpsPort = 8443,
    [ValidateRange(1024, 65535)]
    [int]$GuiTestPcLiveInputPort = 5111,
    [string]$SlotPidMapPath = "D:\15game\gui_test_pc_slot_pids.json",
    [string]$FFmpegPath,
    [string]$NativeRouterPath,
    [string]$PythonPath,
    [switch]$ConfigureTailscaleServe,
    [switch]$DisableInput,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$Runtime = Join-Path $Root "runtime\native_slot1_tolerance"
$StatePath = Join-Path $Runtime "state.json"
$ServerScript = [System.IO.Path]::GetFullPath((Join-Path $Root "stream_test_server.py"))
$Tailscale = "C:\Program Files\Tailscale\tailscale.exe"
$MediamtxApi = "http://127.0.0.1:9997"
$LegacyApi = "http://127.0.0.1:5110"
$NativeRouter = $null
$apiProcess = $null

function Resolve-Executable {
    param(
        [string]$ExplicitPath,
        [string]$EnvironmentPath,
        [string]$CommandName,
        [string[]]$Fallbacks
    )
    foreach ($candidate in @($ExplicitPath, $EnvironmentPath) + $Fallbacks) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    $command = Get-Command $CommandName -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

function Start-HiddenProcess {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$StdoutPath,
        [string]$StderrPath
    )
    $quotedArguments = $Arguments | ForEach-Object {
        $value = [string]$_
        if ($value -match '[\s"]') {
            '"' + $value.Replace('"', '\"') + '"'
        } else {
            $value
        }
    }
    Start-Process -FilePath $FilePath -ArgumentList ($quotedArguments -join " ") `
        -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath -PassThru
}

function Stop-IdentifiedProcess {
    param([int]$ProcessId, [string]$ExpectedPath)
    if ($ProcessId -le 0 -or !$ExpectedPath) { return }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (!$process) { return }
    if ([string]::Equals(
            [string]$process.ExecutablePath,
            [string]$ExpectedPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Stop-StagingApiProcesses {
    $portPattern = "(?i)(?:^|\s)--port\s+$ApiPort(?:\s|$)"
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
    foreach ($process in $ordered) {
        Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction SilentlyContinue
    }
}

function Stop-NativeStack {
    if (Test-Path -LiteralPath (Join-Path $Runtime "native_single_publisher.json")) {
        try {
            $nativeState = Get-Content -LiteralPath (Join-Path $Runtime "native_single_publisher.json") -Raw -Encoding UTF8 | ConvertFrom-Json
            Stop-IdentifiedProcess ([int]$nativeState.publisher_pid) ([string]$nativeState.publisher_executable)
            Stop-IdentifiedProcess ([int]$nativeState.router_pid) ([string]$nativeState.router_executable)
        } catch {
        }
    }
    if (Test-Path -LiteralPath (Join-Path $Runtime "overview_publisher.json")) {
        try {
            $overviewState = Get-Content -LiteralPath (Join-Path $Runtime "overview_publisher.json") -Raw -Encoding UTF8 | ConvertFrom-Json
            Stop-IdentifiedProcess ([int]$overviewState.publisher_pid) ([string]$overviewState.publisher_executable)
        } catch {
        }
    }
    Stop-StagingApiProcesses
    Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
}

if ([Math]::Abs(($OutputWidth / $OutputHeight) - (16 / 9)) -gt 0.001) {
    throw "OutputWidth and OutputHeight must describe a 16:9 profile."
}
if (!(Test-Path -LiteralPath $ServerScript -PathType Leaf)) { throw "Missing metadata server: $ServerScript" }
if (!(Test-Path -LiteralPath $Tailscale -PathType Leaf)) { throw "Tailscale CLI was not found: $Tailscale" }

$Python = Resolve-Executable -ExplicitPath $PythonPath -EnvironmentPath $env:OPLINK_PYTHON `
    -CommandName "python" -Fallbacks @("C:\Users\andyb\Documents\star_cros_bot\.venv\Scripts\python.exe")
$ffmpegFallbacks = @()
$wingetRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
if (Test-Path -LiteralPath $wingetRoot) {
    $ffmpegFallbacks = @(Get-ChildItem -LiteralPath $wingetRoot -Recurse -Filter ffmpeg.exe -File -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -ExpandProperty FullName)
}
$FFmpeg = Resolve-Executable -ExplicitPath $FFmpegPath -EnvironmentPath $env:OPLINK_FFMPEG `
    -CommandName "ffmpeg" -Fallbacks $ffmpegFallbacks
$NativeRouter = Resolve-Executable -ExplicitPath $NativeRouterPath -EnvironmentPath $env:OPLINK_NATIVE_ROUTER `
    -CommandName "oplink_capture_router" `
    -Fallbacks @((Join-Path $Root "tools\oplink_capture_router\oplink_capture_router.exe"))
if (!$Python) { throw "Python 3 was not found. Pass -PythonPath." }
if (!$FFmpeg) { throw "FFmpeg was not found. Pass -FFmpegPath." }
if (!$NativeRouter) { throw "Native router was not found. Build it first or pass -NativeRouterPath." }

if ($Restart -and (Test-Path -LiteralPath $StatePath)) {
    & (Join-Path $Root "stop_native_slot1_tolerance_test.ps1") -IgnoreMissing -ApiPort $ApiPort -ServeHttpsPort $ServeHttpsPort
}
if (Test-Path -LiteralPath $StatePath) {
    throw "A native Slot 1 tolerance test is already running. Use -Restart or stop_native_slot1_tolerance_test.ps1."
}

$legacyHealth = Invoke-RestMethod -Uri "$LegacyApi/api/v1/health" -TimeoutSec 5
if (!$legacyHealth.ok -or $legacyHealth.stream_mode -ne "legacy_warm_cache") {
    throw "Legacy OPLINK_PC health is not the expected legacy_warm_cache state."
}
$mediaPaths = Invoke-RestMethod -Uri "$MediamtxApi/v3/paths/list" -TimeoutSec 5
if ($null -eq $mediaPaths) { throw "The existing MediaMTX API did not respond." }

if (!$DisableInput) {
    $inputHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$GuiTestPcLiveInputPort/health" -TimeoutSec 5
    if (!$inputHealth.enabled -or $inputHealth.execution_owner -ne "GUI_TEST_PC") {
        throw "GUI_TEST_PC live input is not ready on port $GuiTestPcLiveInputPort."
    }
}

$tailscaleStatus = & $Tailscale status --json | ConvertFrom-Json
$tailscaleDnsName = ([string]$tailscaleStatus.Self.DNSName).TrimEnd(".")
if (!$tailscaleDnsName) { throw "Tailscale did not return this host's DNS name." }

New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
$formalTokenPath = Join-Path $Root "runtime\input_token.txt"
$inputTokenPath = Join-Path $Runtime "input_token.txt"
if (!$DisableInput) {
    if (!(Test-Path -LiteralPath $formalTokenPath -PathType Leaf)) {
        throw "The existing GUI_TEST_PC input token was not found."
    }
    Copy-Item -LiteralPath $formalTokenPath -Destination $inputTokenPath -Force
}

$layoutText = & $Python $ServerScript --slots 1 --repair-stream-layout `
    --client-width $OutputWidth --client-height $OutputHeight
if ($LASTEXITCODE -ne 0) { throw "Could not resize and validate Slot 1 for the native tolerance test." }
$layout = $layoutText | ConvertFrom-Json
if (!$layout.ok -or [int]$layout.slots_refreshed -ne 1) {
    throw "Slot 1 layout preflight did not refresh exactly one slot."
}
$identityText = & $Python $ServerScript --slots 1 --probe 1
if ($LASTEXITCODE -ne 0) { throw "Could not probe Slot 1." }
$identity = $identityText | ConvertFrom-Json
if (!$identity.ok -or !$identity.aspect_is_16_9) { throw "Slot 1 is not ready as a 16:9 capture target." }

$selectedEncoder = if ($Encoder -eq "auto") { "mf" } else { $Encoder }
$state = [ordered]@{
    mode = "native_single_slot1_tolerance"
    started_at = (Get-Date).ToUniversalTime().ToString("o")
    host_url = "https://$tailscaleDnsName`:$ServeHttpsPort"
    api_port = $ApiPort
    profile = [ordered]@{ encoded = [ordered]@{ w = $OutputWidth; h = $OutputHeight }; fps = $Fps; bitrate_kbps = $BitrateKbps }
    encoder = $selectedEncoder
    layout = $layout
    source_identity = $identity
    legacy_health_before = [ordered]@{ ok = $legacyHealth.ok; stream_mode = $legacyHealth.stream_mode; profile = $legacyHealth.profile }
    pids = [ordered]@{ api = $null }
}
[System.IO.File]::WriteAllText($StatePath, ($state | ConvertTo-Json -Depth 10), [System.Text.UTF8Encoding]::new($false))

try {
    $apiArguments = @(
        $ServerScript, "--host", "127.0.0.1", "--port", "$ApiPort", "--slots", "1,2,3,4,5,6,7,8,9,10,11,12,13,14,15",
        "--ffmpeg", $FFmpeg, "--encoder", $selectedEncoder,
        "--publisher-mode", "native_single", "--native-router", $NativeRouter,
        "--native-path", "oplink_active", "--width", "$OutputWidth", "--height", "$OutputHeight",
        "--fps", "$Fps", "--bitrate-kbps", "$BitrateKbps",
        "--overview-path", "oplink_overview", "--overview-fps", "10", "--overview-bitrate-kbps", "1800",
        "--publisher-cache-size", "1", "--viewer-idle-timeout-seconds", "15",
        "--mediamtx-api", $MediamtxApi, "--runtime-dir", $Runtime
    )
    if (!$DisableInput) {
        $apiArguments += @(
            "--gui-input-url", "http://127.0.0.1:$GuiTestPcLiveInputPort",
            "--input-token-file", $inputTokenPath
        )
    }
    $apiProcess = Start-HiddenProcess -FilePath $Python -Arguments $apiArguments `
        -StdoutPath (Join-Path $Runtime "api.out.log") -StderrPath (Join-Path $Runtime "api.err.log")
    $state.pids.api = $apiProcess.Id
    [System.IO.File]::WriteAllText($StatePath, ($state | ConvertTo-Json -Depth 10), [System.Text.UTF8Encoding]::new($false))

    $health = $null
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/api/v1/health" -TimeoutSec 1 } catch { $health = $null }
        if ($health -and $health.stream_mode -eq "native_single") { break }
        Start-Sleep -Milliseconds 250
    }
    if (!$health -or $health.stream_mode -ne "native_single") { throw "Native Slot 1 API did not become ready." }
    if ([int]$health.profile.encoded.w -ne $OutputWidth -or [int]$health.profile.encoded.h -ne $OutputHeight) {
        throw "Native API returned an unexpected output profile."
    }
    $activation = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$ApiPort/api/v1/activate" `
        -ContentType "application/json" -Body '{"slot":1}' -TimeoutSec 10
    if (!$activation.publisher_alive -or [int]$activation.active_slot -ne 1) {
        throw "Native Slot 1 publisher did not activate."
    }

    if ($ConfigureTailscaleServe) {
        & $Tailscale serve --bg "--https=$ServeHttpsPort" "--set-path=/oplink-test" "http://127.0.0.1:$ApiPort"
        if ($LASTEXITCODE -ne 0) { throw "Could not configure the isolated native API Serve path." }
        & $Tailscale serve --bg "--https=$ServeHttpsPort" "--set-path=/oplink-whep" "http://127.0.0.1:8889"
        if ($LASTEXITCODE -ne 0) { throw "Could not configure the isolated native WHEP Serve path." }
        & $Tailscale serve --bg "--https=$ServeHttpsPort" "--set-path=/gui-test-pc" "http://127.0.0.1:5100"
        if ($LASTEXITCODE -ne 0) { throw "Could not configure the isolated GUI_TEST_PC Serve path." }
    }

    Write-Host "Native Slot 1 tolerance test is ready; legacy OPLINK_PC remains on 5110/443."
    Write-Host "Profile: ${OutputWidth}x${OutputHeight}@$Fps | Bitrate: ${BitrateKbps} kbps | Encoder: $selectedEncoder"
    Write-Host "Slot 1 physical client: $($layout.client_width)x$($layout.client_height)"
    Write-Host "New IPA host URL: https://$tailscaleDnsName`:$ServeHttpsPort"
    Write-Host "Native WHEP: https://$tailscaleDnsName`:$ServeHttpsPort/oplink-whep/oplink_active/whep"
    Write-Host "Stop: .\host\stop_native_slot1_tolerance_test.ps1 -ServeHttpsPort $ServeHttpsPort"
} catch {
    Stop-NativeStack
    throw
}

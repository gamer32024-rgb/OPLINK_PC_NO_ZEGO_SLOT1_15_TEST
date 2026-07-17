[CmdletBinding()]
param(
    [int[]]$Slots = @(1, 15),
    [ValidateSet("720p", "1080p")]
    [string]$Profile = "720p",
    [ValidateRange(1, 60)]
    [int]$Fps = 30,
    [ValidateRange(250, 20000)]
    [int]$BitrateKbps = 4000,
    [ValidateSet("auto", "nvenc", "x264")]
    [string]$Encoder = "auto",
    [string]$FFmpegPath,
    [string]$MediaMTXPath,
    [string]$PythonPath,
    [ValidateRange(1024, 65535)]
    [int]$ApiPort = 5110,
    [switch]$ConfigureTailscaleServe,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$Runtime = Join-Path $Root "runtime"
$StatePath = Join-Path $Runtime "state.json"
$TemplatePath = Join-Path $Root "mediamtx.template.yml"
$RuntimeConfig = Join-Path $Runtime "mediamtx.runtime.yml"
$ServerScript = Join-Path $Root "stream_test_server.py"
$Tailscale = "C:\Program Files\Tailscale\tailscale.exe"
$startedProcesses = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

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
    $process = Start-Process -FilePath $FilePath -ArgumentList ($quotedArguments -join " ") -WorkingDirectory $Root `
        -WindowStyle Hidden -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath -PassThru
    $startedProcesses.Add($process)
    return $process
}

function Stop-StartedProcesses {
    foreach ($process in $startedProcesses) {
        if ($process -and !$process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

if (($Slots | Sort-Object -Unique) -join "," -ne "1,15") {
    throw "This test has a fixed source set: -Slots 1,15"
}
if (!(Test-Path -LiteralPath $TemplatePath)) { throw "Missing MediaMTX template: $TemplatePath" }
if (!(Test-Path -LiteralPath $ServerScript)) { throw "Missing metadata server: $ServerScript" }
if (!(Test-Path -LiteralPath $Tailscale)) { throw "Tailscale CLI was not found: $Tailscale" }

if ($Restart -and (Test-Path -LiteralPath $StatePath)) {
    & (Join-Path $Root "stop_stream_test.ps1") -IgnoreMissing
}
if (Test-Path -LiteralPath $StatePath) {
    throw "A previous test state exists. Run .\stop_stream_test.ps1 or pass -Restart."
}

$ffmpegFallbacks = @()
$wingetRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
if (Test-Path -LiteralPath $wingetRoot) {
    $ffmpegFallbacks = @(Get-ChildItem -LiteralPath $wingetRoot -Recurse -Filter ffmpeg.exe -File -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -ExpandProperty FullName)
}
$FFmpeg = Resolve-Executable -ExplicitPath $FFmpegPath -EnvironmentPath $env:OPLINK_FFMPEG `
    -CommandName "ffmpeg" -Fallbacks $ffmpegFallbacks
$MediaMTX = Resolve-Executable -ExplicitPath $MediaMTXPath -EnvironmentPath $env:OPLINK_MEDIAMTX `
    -CommandName "mediamtx" -Fallbacks @((Join-Path $Root "tools\mediamtx\mediamtx.exe"))
$Python = Resolve-Executable -ExplicitPath $PythonPath -EnvironmentPath $env:OPLINK_PYTHON `
    -CommandName "python" -Fallbacks @("C:\Users\andyb\Documents\star_cros_bot\.venv\Scripts\python.exe")

if (!$FFmpeg) { throw "FFmpeg was not found. Install a build containing gfxcapture or pass -FFmpegPath." }
if (!$MediaMTX) { throw "MediaMTX was not found. Run .\install_mediamtx.ps1 or pass -MediaMTXPath." }
if (!$Python) { throw "Python 3 was not found. Pass -PythonPath." }

$filterInfo = & $FFmpeg -hide_banner -filters 2>&1 | Out-String
if ($filterInfo -notmatch "\bgfxcapture\b") {
    throw "The selected FFmpeg does not include the gfxcapture filter: $FFmpeg"
}

$tailscaleIPv4 = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceAlias -match "Tailscale" -and $_.AddressState -eq "Preferred" } |
    Select-Object -First 1 -ExpandProperty IPAddress
if (!$tailscaleIPv4) { throw "No active Tailscale IPv4 address was found." }

$tailscaleStatus = & $Tailscale status --json | ConvertFrom-Json
$tailscaleDnsName = ([string]$tailscaleStatus.Self.DNSName).TrimEnd(".")
if (!$tailscaleDnsName) { throw "Tailscale did not return this host's DNS name." }

$profileWidth = if ($Profile -eq "1080p") { 1920 } else { 1280 }
$profileHeight = if ($Profile -eq "1080p") { 1080 } else { 720 }
$identities = @()
foreach ($slot in $Slots) {
    $probeText = & $Python $ServerScript --probe $slot
    if ($LASTEXITCODE -ne 0) { throw "Could not probe slot $slot." }
    $identity = $probeText | ConvertFrom-Json
    if (!$identity.ok) { throw "Slot $slot is not ready: $($identity.error)" }
    if (!$identity.aspect_is_16_9) {
        throw "Slot $slot is not 16:9: $($identity.client_logical.w)x$($identity.client_logical.h)"
    }
    $identities += $identity
}

New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
$configText = (Get-Content -LiteralPath $TemplatePath -Raw).Replace("__TAILSCALE_IPV4__", $tailscaleIPv4)
[System.IO.File]::WriteAllText($RuntimeConfig, $configText, [System.Text.UTF8Encoding]::new($false))

$selectedEncoder = $Encoder
if ($Encoder -eq "auto" -or $Encoder -eq "nvenc") {
    $probeIdentity = $identities | Where-Object slot -eq 1 | Select-Object -First 1
    $probeFilter = "gfxcapture=hwnd=$($probeIdentity.hwnd):capture_cursor=0:capture_border=0:max_framerate=${Fps}:resize_mode=scale"
    $nvencProbeLog = Join-Path $Runtime "nvenc_probe.err.log"
    $savedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $FFmpeg -hide_banner -loglevel warning -f lavfi -i $probeFilter -frames:v 1 -an `
        -vf "hwdownload,format=bgra,scale=${profileWidth}:${profileHeight}:flags=bilinear,format=nv12" `
        -c:v h264_nvenc -preset p1 -tune ull -f null NUL 2> $nvencProbeLog
    $nvencExitCode = $LASTEXITCODE
    $ErrorActionPreference = $savedErrorActionPreference
    if ($nvencExitCode -eq 0) {
        $selectedEncoder = "nvenc"
    } elseif ($Encoder -eq "nvenc") {
        throw "NVENC was explicitly requested but the live encoder probe failed. See $nvencProbeLog"
    } else {
        $selectedEncoder = "x264"
    }
}

try {
    $media = Start-HiddenProcess -FilePath $MediaMTX -Arguments @($RuntimeConfig) `
        -StdoutPath (Join-Path $Runtime "mediamtx.out.log") -StderrPath (Join-Path $Runtime "mediamtx.err.log")
    Start-Sleep -Milliseconds 750
    if ($media.HasExited) { throw "MediaMTX exited during startup." }

    $api = Start-HiddenProcess -FilePath $Python -Arguments @($ServerScript, "--host", "127.0.0.1", "--port", "$ApiPort") `
        -StdoutPath (Join-Path $Runtime "api.out.log") -StderrPath (Join-Path $Runtime "api.err.log")

    $publishers = @()
    foreach ($identity in $identities) {
        $slot = [int]$identity.slot
        $pathName = "slot{0:D2}" -f $slot
        $captureFilter = "gfxcapture=hwnd=$($identity.hwnd):capture_cursor=0:capture_border=0:max_framerate=${Fps}:resize_mode=scale"
        $pixelFormat = if ($selectedEncoder -eq "nvenc") { "nv12" } else { "yuv420p" }
        $videoFilter = "hwdownload,format=bgra,scale=${profileWidth}:${profileHeight}:flags=bilinear,format=$pixelFormat"
        $encoderArgs = if ($selectedEncoder -eq "nvenc") {
            @("-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ull", "-rc", "cbr")
        } else {
            @("-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency")
        }
        $ffmpegArgs = @(
            "-hide_banner", "-loglevel", "info", "-f", "lavfi", "-i", $captureFilter,
            "-an", "-vf", $videoFilter
        ) + $encoderArgs + @(
            "-b:v", "${BitrateKbps}k", "-maxrate", "${BitrateKbps}k",
            "-bufsize", "$([Math]::Max(250, [int]($BitrateKbps / 5)))k",
            "-g", "$Fps", "-keyint_min", "$Fps", "-sc_threshold", "0",
            "-f", "rtsp", "-rtsp_transport", "tcp", "rtsp://127.0.0.1:8554/$pathName"
        )
        $publisher = Start-HiddenProcess -FilePath $FFmpeg -Arguments $ffmpegArgs `
            -StdoutPath (Join-Path $Runtime "$pathName.ffmpeg.out.log") `
            -StderrPath (Join-Path $Runtime "$pathName.ffmpeg.err.log")
        $publishers += [ordered]@{ slot = $slot; pid = $publisher.Id; path = $pathName }
    }

    $state = [ordered]@{
        started_at = (Get-Date).ToUniversalTime().ToString("o")
        profile = [ordered]@{
            encoded = [ordered]@{ w = $profileWidth; h = $profileHeight }
            fps = $Fps
            bitrate_kbps = $BitrateKbps
        }
        encoder = $selectedEncoder
        source_identities = $identities
        tailscale = [ordered]@{
            ipv4 = $tailscaleIPv4
            host = $tailscaleDnsName
            app_base_url = "https://$tailscaleDnsName"
            sources_url = "https://$tailscaleDnsName/oplink-test/api/v1/sources"
            whep_base_url = "https://$tailscaleDnsName/oplink-whep"
        }
        pids = [ordered]@{
            mediamtx = $media.Id
            api = $api.Id
            publishers = $publishers
        }
    }
    [System.IO.File]::WriteAllText($StatePath, ($state | ConvertTo-Json -Depth 8), [System.Text.UTF8Encoding]::new($false))

    if ($ConfigureTailscaleServe) {
        & $Tailscale serve --bg --https=443 --set-path /oplink-test "http://127.0.0.1:$ApiPort"
        if ($LASTEXITCODE -ne 0) { throw "Could not configure the /oplink-test Tailscale Serve mount." }
        & $Tailscale serve --bg --https=443 --set-path /oplink-whep "http://127.0.0.1:8889"
        if ($LASTEXITCODE -ne 0) { throw "Could not configure the /oplink-whep Tailscale Serve mount." }
    }

    Start-Sleep -Seconds 3
    foreach ($process in $startedProcesses) {
        if ($process.HasExited) { throw "A test process exited during startup. Check host/runtime logs." }
    }
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/api/v1/health" -TimeoutSec 5
    if (!$health.all_sources_ready) { throw "The metadata service reports that a source is no longer ready." }

    Write-Host "OPLINK_PC slot 1/15 no-ZEGO test is ready."
    Write-Host "Encoder: $selectedEncoder | Output: ${profileWidth}x${profileHeight}@$Fps | Bitrate: ${BitrateKbps} kbps"
    foreach ($identity in $identities) {
        Write-Host ("Slot {0}: HWND={1} logical={2}x{3} WGC expected={4}x{5} aspect={6:N5}" -f `
            $identity.slot, $identity.hwnd, $identity.client_logical.w, $identity.client_logical.h, `
            $identity.capture_physical_expected.w, $identity.capture_physical_expected.h, $identity.aspect)
    }
    Write-Host "iOS app host: https://$tailscaleDnsName"
    Write-Host "Metadata: https://$tailscaleDnsName/oplink-test/api/v1/sources"
    Write-Host "WHEP slot 1: https://$tailscaleDnsName/oplink-whep/slot01/whep"
    Write-Host "WHEP slot 15: https://$tailscaleDnsName/oplink-whep/slot15/whep"
} catch {
    Stop-StartedProcesses
    if (Test-Path -LiteralPath $StatePath) { Remove-Item -LiteralPath $StatePath -Force }
    throw
}

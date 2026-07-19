[CmdletBinding()]
param(
    [ValidateRange(5, 300)]
    [int]$PollSeconds = 10,
    [switch]$Once
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$Runtime = Join-Path $Root "runtime"
$LogPath = Join-Path $Runtime "autostart.log"
$PidPath = Join-Path $Runtime "autostart.pid"
$StartScript = Join-Path $Root "start_stream_test.ps1"
$SlotPidMapPath = "D:\15game\gui_test_pc_slot_pids.json"
$mutex = [System.Threading.Mutex]::new($false, "Local\OPLINK_PC_Stream_Autostart")
$hasMutex = $false
$lastState = $null
$exitCode = 0
$gameSetWasIncomplete = $true

New-Item -ItemType Directory -Force -Path $Runtime | Out-Null

function Write-AutostartLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss.fff"), $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Set-WatchdogState {
    param([string]$State, [string]$Message)
    if ($script:lastState -ne $State) {
        $script:lastState = $State
        Write-AutostartLog "$State - $Message"
    }
}

function Get-JsonEndpoint {
    param([string]$Uri, [int]$TimeoutSeconds = 2)
    try {
        return Invoke-RestMethod -Uri $Uri -TimeoutSec $TimeoutSeconds
    } catch {
        return $null
    }
}

function Test-GuiTestPcBridge {
    $health = Get-JsonEndpoint "http://127.0.0.1:5111/health"
    return $null -ne $health -and [bool]$health.enabled -and
        [string]$health.execution_owner -eq "GUI_TEST_PC"
}

function Test-StreamHost {
    $health = Get-JsonEndpoint "http://127.0.0.1:5110/api/v1/health"
    $media = Get-JsonEndpoint "http://127.0.0.1:9997/v3/paths/list"
    return $null -ne $health -and [bool]$health.ok -and $null -ne $media
}

function Get-ReadyGameSlotCount {
    if (!(Test-Path -LiteralPath $SlotPidMapPath -PathType Leaf)) { return 0 }
    try {
        $slotMap = Get-Content -LiteralPath $SlotPidMapPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return 0
    }
    $ready = 0
    foreach ($slot in 1..15) {
        $property = $slotMap.PSObject.Properties | Where-Object Name -eq ([string]$slot) | Select-Object -First 1
        if (!$property -or !$property.Value.Pid) { continue }
        $pidValue = [int]$property.Value.Pid
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
        if ($process -and $process.Name -ieq "StarCG.exe") { $ready++ }
    }
    return $ready
}

function Start-OplinkStreamHost {
    Remove-Item -LiteralPath (Join-Path $Runtime "autostart_host_start.out.log") `
        -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $Runtime "autostart_host_start.err.log") `
        -Force -ErrorAction SilentlyContinue
    $startParameters = @{
        Profile = "1080p"
        Fps = 30
        BitrateKbps = 6000
        PublisherCacheSize = 3
        ViewerIdleTimeoutSeconds = 15
        Encoder = "mf"
        ConfigureTailscaleServe = $true
        AllowVpnDefaultRoute = $true
        Restart = $true
    }
    # Run in a child PowerShell scope without an OS pipe. Persistent MediaMTX/API
    # descendants therefore cannot keep the watchdog blocked, and Write-Host output
    # containing the pairing token is discarded rather than copied to another log.
    & $StartScript @startParameters 6>$null | Out-Null
}

try {
    try {
        $hasMutex = $mutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $hasMutex = $true
    }
    if (!$hasMutex) {
        Write-Output "OPLINK_PC stream autostart is already running."
        return
    }
    [System.IO.File]::WriteAllText($PidPath, [string]$PID, [System.Text.UTF8Encoding]::new($false))
    Write-AutostartLog "WATCHDOG_START - pid=$PID once=$([bool]$Once)"

    while ($true) {
        try {
            $guiReady = Test-GuiTestPcBridge
            $streamReady = Test-StreamHost
            if (!$guiReady) {
                Set-WatchdogState "WAITING_GUI_TEST_PC" "Open GUI_TEST_PC manually; live-touch bridge 127.0.0.1:5111 is not ready."
                $exitCode = 2
            } else {
                $readySlots = Get-ReadyGameSlotCount
                if ($readySlots -lt 15) {
                    $gameSetWasIncomplete = $true
                    Set-WatchdogState "WAITING_GAME_WINDOWS" "$readySlots/15 registered StarCG processes are ready; start the remaining slots manually from GUI_TEST_PC."
                    $exitCode = 3
                } elseif ($streamReady -and !$gameSetWasIncomplete) {
                    Set-WatchdogState "READY" "GUI_TEST_PC, 15/15 game windows, API, MediaMTX, WHEP, and Tailscale Serve are ready."
                    $exitCode = 0
                } else {
                    Set-WatchdogState "STARTING_STREAM_HOST" "15/15 game windows are ready; refreshing layout and starting the stream host."
                    Start-OplinkStreamHost
                    if (!(Test-StreamHost)) {
                        throw "Stream host did not pass local API and MediaMTX readiness checks."
                    }
                    $gameSetWasIncomplete = $false
                    Set-WatchdogState "READY" "Stream host started successfully."
                    $exitCode = 0
                }
            }
        } catch {
            $exitCode = 4
            Set-WatchdogState "RETRYING" $_.Exception.Message
        }

        if ($Once) { break }
        Start-Sleep -Seconds $PollSeconds
    }
} finally {
    if (Test-Path -LiteralPath $PidPath) {
        $recordedPid = (Get-Content -LiteralPath $PidPath -Raw -ErrorAction SilentlyContinue).Trim()
        if ($recordedPid -eq [string]$PID) {
            Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
        }
    }
    if ($hasMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}

if ($Once) { exit $exitCode }

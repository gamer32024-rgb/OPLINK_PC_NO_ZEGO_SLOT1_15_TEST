[CmdletBinding()]
param(
    [ValidateRange(5, 300)]
    [int]$PollSeconds = 10,
    [ValidateRange(10, 300)]
    [int]$GuiReadyTimeoutSeconds = 60,
    [switch]$Once,
    [switch]$NoLaunchGuiTestPc
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$Runtime = Join-Path $Root "runtime"
$LogPath = Join-Path $Runtime "autostart.log"
$PidPath = Join-Path $Runtime "autostart.pid"
$StartScript = Join-Path $Root "start_stream_test.ps1"
$WorkspaceRoot = Split-Path (Split-Path $Root -Parent) -Parent
$GuiTestPcRoot = Join-Path $WorkspaceRoot "GUI_TEST_PC_DEV_20260703"
$GuiTestPcScript = Join-Path $GuiTestPcRoot "gui_test_pc.py"
$Python = "C:\Users\andyb\Documents\star_cros_bot\.venv\Scripts\python.exe"
$GuiTestPcStdout = Join-Path $Runtime "gui_test_pc_autostart.out.log"
$GuiTestPcStderr = Join-Path $Runtime "gui_test_pc_autostart.err.log"
$SlotPidMapPath = "D:\15game\gui_test_pc_slot_pids.json"
$GameLauncherScript = Join-Path $GuiTestPcRoot "launcher\starcg_15_control_gui_test_pc.ps1"
$GameSource = "D:\TWFULLPC1.2.76"
$GameBypassDir = "D:\15game"
$GameLauncherLog = Join-Path $GameBypassDir "launcher_action.log"
$NetBindConfig = Join-Path $GuiTestPcRoot "config_pc\netbind_config.txt"
$NetBindLauncher = Join-Path $GuiTestPcRoot "netbind_pc\build_ninja\GuiTestNetBindLauncher.exe"
$NetBindLog = Join-Path $GuiTestPcRoot "logs_pc\gui_test_pc_netbind_hook.log"
$WindowsUserConfig = Join-Path $GuiTestPcRoot "config_pc\starcg_windows_users.json"
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

function Test-GuiTestPcProcess {
    return $null -ne (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and
        $_.CommandLine.IndexOf($GuiTestPcScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    } | Select-Object -First 1)
}

function Ensure-GuiTestPc {
    if (Test-GuiTestPcBridge) { return $true }
    if ($NoLaunchGuiTestPc) { return $false }
    if (!(Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "GUI_TEST_PC python.exe was not found: $Python"
    }
    if (!(Test-Path -LiteralPath $GuiTestPcScript -PathType Leaf)) {
        throw "GUI_TEST_PC script was not found: $GuiTestPcScript"
    }
    if (!(Test-GuiTestPcProcess)) {
        Set-WatchdogState "STARTING_GUI_TEST_PC" "Launching GUI_TEST_PC for the live-touch bridge."
        Start-Process -FilePath $Python -ArgumentList ('"' + $GuiTestPcScript + '"') `
            -WorkingDirectory $GuiTestPcRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $GuiTestPcStdout `
            -RedirectStandardError $GuiTestPcStderr | Out-Null
    }
    $deadline = (Get-Date).AddSeconds($GuiReadyTimeoutSeconds)
    do {
        if (Test-GuiTestPcBridge) { return $true }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return $false
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

function Get-ActiveVpnDefaultRoute {
    $candidates = @(
        Get-NetRoute -AddressFamily IPv4 -DestinationPrefix "0.0.0.0/0" `
            -PolicyStore ActiveStore -ErrorAction SilentlyContinue | ForEach-Object {
            $route = $_
            $adapter = Get-NetAdapter -InterfaceIndex $route.InterfaceIndex `
                -ErrorAction SilentlyContinue
            $ipInterface = Get-NetIPInterface -AddressFamily IPv4 `
                -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue
            if ($adapter -and $adapter.Status -eq "Up" -and $ipInterface) {
                [pscustomobject]@{
                    Adapter = [string]$adapter.Name
                    Description = [string]$adapter.InterfaceDescription
                    EffectiveMetric = [int]$route.RouteMetric + [int]$ipInterface.InterfaceMetric
                }
            }
        } | Sort-Object EffectiveMetric
    )
    if ($candidates.Count -eq 0) { return $null }
    $primary = $candidates[0]
    $identity = "$($primary.Adapter) $($primary.Description)"
    if ($identity -match "(?i)surfshark|wireguard|\bvpn\b|\btap\b|\btun(?:nel)?\b") {
        return $primary
    }
    return $null
}

function Start-MissingGameSlots {
    foreach ($requiredPath in @(
        $GameLauncherScript,
        (Join-Path $GameSource "StarCG.exe"),
        $NetBindConfig,
        $NetBindLauncher,
        $WindowsUserConfig
    )) {
        if (!(Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "Required GUI_TEST_PC launcher dependency was not found: $requiredPath"
        }
    }

    Set-WatchdogState "STARTING_GAME_WINDOWS" "Calling the GUI_TEST_PC launcher to fill missing slots 1-15."
    & $GameLauncherScript `
        -Action "start-missing" `
        -SlotList "1-15" `
        -Source $GameSource `
        -TargetRoot $GameSource `
        -BypassDir $GameBypassDir `
        -Slots 15 `
        -LogPath $GameLauncherLog `
        -ForceBindConfig $NetBindConfig `
        -WindowsUserConfigPath $WindowsUserConfig `
        -NetBindLauncherPath $NetBindLauncher `
        -NetBindLogPath $NetBindLog `
        -UseNetBind `
        -UseWindowsUsers `
        -Json | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "GUI_TEST_PC start-missing launcher exited with code $LASTEXITCODE."
    }
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
            $guiReady = Ensure-GuiTestPc
            $streamReady = Test-StreamHost
            if (!$guiReady) {
                Set-WatchdogState "WAITING_GUI_TEST_PC" "Live-touch bridge 127.0.0.1:5111 is not ready."
                $exitCode = 2
            } else {
                $readySlots = Get-ReadyGameSlotCount
                if ($readySlots -lt 15) {
                    $gameSetWasIncomplete = $true
                    $vpnDefaultRoute = Get-ActiveVpnDefaultRoute
                    if ($vpnDefaultRoute) {
                        Set-WatchdogState "WAITING_VPN_CLOSE" ("Close the VPN; missing game slots will start automatically. " +
                            "Current default=$($vpnDefaultRoute.Adapter).")
                    } else {
                        Set-WatchdogState "WAITING_GAME_WINDOWS" "$readySlots/15 registered StarCG processes are ready; starting missing slots."
                        Start-MissingGameSlots
                    }
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
            $message = $_.Exception.Message
            if ($message -like "blocked netbind start: VPN default route active*") {
                Set-WatchdogState "WAITING_VPN_CLOSE" "Close the VPN; missing game slots will then start automatically."
            } else {
                Set-WatchdogState "RETRYING" $message
            }
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

param(
    [ValidateSet("status", "arrange", "ensure")]
    [string]$Action = "ensure",
    [string]$SlotList = "1-20",
    [string]$SlotPidMapPath = "D:\15game\gui_test_pc_slot_pids.json",
    [string]$ConfigPath = "",
    [switch]$Json
)

$ErrorActionPreference = "Stop"
$HardPolicyId = "starcg_4k_stacked_720p_pico_v2"
$HardLayoutFormat = "gui_test_pc_window_layout_v3_hard_4k_stacked"
$HardLayoutMode = "stacked"
$HardDisplayWidth = 3840
$HardDisplayHeight = 2160
$HardGridColumns = 1
$HardGridRows = 20
$HardGridGapX = 0
$HardGridGapY = 0
$HardClientWidth = 1280
$HardClientHeight = 720
$HardOuterWidth = 1302
$HardOuterHeight = 776

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path (Split-Path -Parent $PSScriptRoot) "config_pc\window_layout.json"
}

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class GuiTestPcWindowLayout {
    public const int SW_RESTORE = 9;
    public const uint SWP_NOSIZE = 0x0001;
    public const uint SWP_NOZORDER = 0x0004;
    public const uint SWP_NOACTIVATE = 0x0010;
    public const uint SWP_SHOWWINDOW = 0x0040;
    public const uint SWP_NOOWNERZORDER = 0x0200;
    public const int SM_CXVIRTUALSCREEN = 78;
    public const int SM_CYVIRTUALSCREEN = 79;
    public const int SM_XVIRTUALSCREEN = 76;
    public const int SM_YVIRTUALSCREEN = 77;

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct POINT {
        public int X;
        public int Y;
    }

    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();

    [DllImport("user32.dll")]
    public static extern int GetSystemMetrics(int nIndex);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool GetClientRect(IntPtr hWnd, out RECT rect);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool ClientToScreen(IntPtr hWnd, ref POINT point);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetWindowPos(
        IntPtr hWnd,
        IntPtr hWndInsertAfter,
        int X,
        int Y,
        int cx,
        int cy,
        uint uFlags
    );
}
"@

[GuiTestPcWindowLayout]::SetProcessDPIAware() | Out-Null

function Parse-SlotList {
    param([string]$Value)

    $values = @()
    foreach ($part in ($Value -split "[,;\s]+")) {
        $token = $part.Trim()
        if (-not $token) {
            continue
        }
        if ($token -match "^(\d{1,2})-(\d{1,2})$") {
            $start = [int]$Matches[1]
            $end = [int]$Matches[2]
            if ($start -gt $end) {
                throw "Invalid slot range: $token"
            }
            $values += $start..$end
        } elseif ($token -match "^\d{1,2}$") {
            $values += [int]$token
        } else {
            throw "Invalid slot value: $token"
        }
    }
    $slots = @($values | Sort-Object -Unique)
    if ($slots.Count -lt 1 -or @($slots | Where-Object { $_ -lt 1 -or $_ -gt 20 }).Count -gt 0) {
        throw "Slots must be in the range 1-20."
    }
    return $slots
}

function Get-VirtualDesktop {
    return [ordered]@{
        x = [GuiTestPcWindowLayout]::GetSystemMetrics([GuiTestPcWindowLayout]::SM_XVIRTUALSCREEN)
        y = [GuiTestPcWindowLayout]::GetSystemMetrics([GuiTestPcWindowLayout]::SM_YVIRTUALSCREEN)
        width = [GuiTestPcWindowLayout]::GetSystemMetrics([GuiTestPcWindowLayout]::SM_CXVIRTUALSCREEN)
        height = [GuiTestPcWindowLayout]::GetSystemMetrics([GuiTestPcWindowLayout]::SM_CYVIRTUALSCREEN)
    }
}

function Assert-HardLayoutConfig {
    param($Config, [hashtable]$Desktop)

    if (-not $Config) {
        throw "Locked 4K layout config is missing: $ConfigPath"
    }
    $failures = @()
    if ([string]$Config.format -ne $HardLayoutFormat) { $failures += "format" }
    if ([string]$Config.policy_id -ne $HardPolicyId) { $failures += "policy_id" }
    if ([string]$Config.layout_mode -ne $HardLayoutMode) { $failures += "layout_mode" }
    if ($Config.locked -ne $true) { $failures += "locked" }
    if ([int]$Config.display.x -ne 0 -or [int]$Config.display.y -ne 0 -or
        [int]$Config.display.width -ne $HardDisplayWidth -or [int]$Config.display.height -ne $HardDisplayHeight) {
        $failures += "display"
    }
    if ([int]$Config.grid.columns -ne $HardGridColumns -or [int]$Config.grid.rows -ne $HardGridRows -or
        [int]$Config.grid.origin_x -ne 0 -or [int]$Config.grid.origin_y -ne 0 -or
        [int]$Config.grid.gap_x -ne $HardGridGapX -or [int]$Config.grid.gap_y -ne $HardGridGapY) {
        $failures += "grid"
    }
    if ([int]$Config.expected_client.width -ne $HardClientWidth -or
        [int]$Config.expected_client.height -ne $HardClientHeight) {
        $failures += "expected_client"
    }
    if ([int]$Config.expected_outer.width -ne $HardOuterWidth -or
        [int]$Config.expected_outer.height -ne $HardOuterHeight) {
        $failures += "expected_outer"
    }
    if ($failures.Count -gt 0) {
        throw "Layout config violates locked policy ${HardPolicyId}: $($failures -join ',')"
    }
    if ([int]$Desktop.x -ne 0 -or [int]$Desktop.y -ne 0 -or
        [int]$Desktop.width -ne $HardDisplayWidth -or [int]$Desktop.height -ne $HardDisplayHeight) {
        throw "Physical desktop violates locked policy ${HardPolicyId}: current=$($Desktop.x),$($Desktop.y) $($Desktop.width)x$($Desktop.height), expected=0,0 ${HardDisplayWidth}x${HardDisplayHeight}"
    }
}

function Get-WindowMeasurement {
    param([System.IntPtr]$Handle)

    $outer = New-Object GuiTestPcWindowLayout+RECT
    $client = New-Object GuiTestPcWindowLayout+RECT
    $origin = New-Object GuiTestPcWindowLayout+POINT
    if (-not [GuiTestPcWindowLayout]::GetWindowRect($Handle, [ref]$outer)) {
        throw "GetWindowRect failed for hwnd=0x$($Handle.ToInt64().ToString('X'))"
    }
    if (-not [GuiTestPcWindowLayout]::GetClientRect($Handle, [ref]$client)) {
        throw "GetClientRect failed for hwnd=0x$($Handle.ToInt64().ToString('X'))"
    }
    if (-not [GuiTestPcWindowLayout]::ClientToScreen($Handle, [ref]$origin)) {
        throw "ClientToScreen failed for hwnd=0x$($Handle.ToInt64().ToString('X'))"
    }
    $outerWidth = $outer.Right - $outer.Left
    $outerHeight = $outer.Bottom - $outer.Top
    $clientWidth = $client.Right - $client.Left
    $clientHeight = $client.Bottom - $client.Top
    if ($outerWidth -le 0 -or $outerHeight -le 0 -or $clientWidth -le 0 -or $clientHeight -le 0) {
        throw "Invalid window dimensions for hwnd=0x$($Handle.ToInt64().ToString('X'))"
    }
    return [ordered]@{
        outer = [ordered]@{ x = $outer.Left; y = $outer.Top; width = $outerWidth; height = $outerHeight }
        client = [ordered]@{ x = $origin.X; y = $origin.Y; width = $clientWidth; height = $clientHeight }
        inset = [ordered]@{ left = $origin.X - $outer.Left; top = $origin.Y - $outer.Top }
    }
}

function Read-LayoutConfig {
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    } catch {
        throw "Cannot read layout config '$ConfigPath': $($_.Exception.Message)"
    }
}

function New-LayoutConfig {
    param(
        [hashtable]$Reference,
        [hashtable]$Desktop
    )

    return [ordered]@{
        format = $HardLayoutFormat
        policy_id = $HardPolicyId
        layout_mode = $HardLayoutMode
        locked = $true
        created_at = (Get-Date).ToString("s")
        coordinate_space = "physical_desktop_pixels"
        display = [ordered]@{
            x = $Desktop.x
            y = $Desktop.y
            width = $HardDisplayWidth
            height = $HardDisplayHeight
        }
        grid = [ordered]@{
            columns = $HardGridColumns
            rows = $HardGridRows
            origin_x = 0
            origin_y = 0
            gap_x = $HardGridGapX
            gap_y = $HardGridGapY
        }
        expected_client = [ordered]@{
            width = $HardClientWidth
            height = $HardClientHeight
        }
        expected_outer = [ordered]@{
            width = $HardOuterWidth
            height = $HardOuterHeight
        }
        note = "Slots 1-20 use one fixed overlapping position with a 1280x720 physical client area. Windows are resized and moved before playback."
    }
}

function Save-LayoutConfig {
    param($Config)

    $parent = Split-Path -Parent $ConfigPath
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
}

function Test-Near {
    param([int]$Actual, [int]$Expected, [int]$Tolerance = 2)
    return [Math]::Abs($Actual - $Expected) -le $Tolerance
}

function Get-LayoutTarget {
    param([int]$Slot, $Config)

    if ([string]$Config.layout_mode -eq "stacked") {
        return [ordered]@{
            x = [int]$Config.grid.origin_x
            y = [int]$Config.grid.origin_y
        }
    }
    $index = $Slot - 1
    $column = $index % [int]$Config.grid.columns
    $row = [int][Math]::Floor($index / [int]$Config.grid.columns)
    return [ordered]@{
        x = [int]$Config.grid.origin_x + ($column * ([int]$Config.expected_outer.width + [int]$Config.grid.gap_x))
        y = [int]$Config.grid.origin_y + ($row * ([int]$Config.expected_outer.height + [int]$Config.grid.gap_y))
    }
}

function Get-SlotWindows {
    param([int[]]$Slots)

    if (-not (Test-Path -LiteralPath $SlotPidMapPath)) {
        throw "Slot PID map not found: $SlotPidMapPath"
    }
    $pidMap = Get-Content -LiteralPath $SlotPidMapPath -Raw | ConvertFrom-Json
    $items = @()
    foreach ($slot in $Slots) {
        $entry = $pidMap.PSObject.Properties[[string]$slot]
        if (-not $entry -or -not $entry.Value.Pid) {
            $items += [ordered]@{ slot = $slot; missing = "slot PID is not registered" }
            continue
        }
        $processId = [int]$entry.Value.Pid
        try {
            $process = Get-Process -Id $processId -ErrorAction Stop
        } catch {
            $items += [ordered]@{ slot = $slot; pid = $processId; missing = "process is not running" }
            continue
        }
        $handle = [System.IntPtr]$process.MainWindowHandle
        if ($handle -eq [System.IntPtr]::Zero) {
            $items += [ordered]@{ slot = $slot; pid = $processId; missing = "game window is not ready" }
            continue
        }
        try {
            $measurement = Get-WindowMeasurement $handle
            $items += [ordered]@{
                slot = $slot
                pid = $processId
                hwnd = $handle.ToInt64()
                title = $process.MainWindowTitle
                measurement = $measurement
            }
        } catch {
            $items += [ordered]@{ slot = $slot; pid = $processId; hwnd = $handle.ToInt64(); missing = $_.Exception.Message }
        }
    }
    return $items
}

function Get-LayoutStatus {
    param(
        [int[]]$Slots,
        $Config,
        [hashtable]$Desktop
    )

    $items = Get-SlotWindows -Slots $Slots
    $missing = @($items | Where-Object { $_.missing } | ForEach-Object { [int]$_.slot })
    $sizeMismatches = @()
    $positionMismatches = @()
    $details = @()
    $displayMatches = $false
    if ($Config) {
        $displayMatches =
            ([int]$Config.display.x -eq [int]$Desktop.x) -and
            ([int]$Config.display.y -eq [int]$Desktop.y) -and
            ([int]$Config.display.width -eq [int]$Desktop.width) -and
            ([int]$Config.display.height -eq [int]$Desktop.height)
    }

    foreach ($item in $items) {
        if ($item.missing) {
            $details += $item
            continue
        }
        $measurement = $item.measurement
        $sizeOk = $false
        $positionOk = $false
        $target = $null
        if ($Config) {
            $sizeOk =
                (Test-Near $measurement.client.width ([int]$Config.expected_client.width)) -and
                (Test-Near $measurement.client.height ([int]$Config.expected_client.height)) -and
                (Test-Near $measurement.outer.width ([int]$Config.expected_outer.width)) -and
                (Test-Near $measurement.outer.height ([int]$Config.expected_outer.height))
            $target = Get-LayoutTarget -Slot ([int]$item.slot) -Config $Config
            $positionOk =
                (Test-Near $measurement.outer.x ([int]$target.x)) -and
                (Test-Near $measurement.outer.y ([int]$target.y))
            if (-not $sizeOk) {
                $sizeMismatches += [int]$item.slot
            }
            if (-not $positionOk) {
                $positionMismatches += [int]$item.slot
            }
        }
        $details += [ordered]@{
            slot = [int]$item.slot
            pid = [int]$item.pid
            hwnd = [int64]$item.hwnd
            title = $item.title
            current = $measurement
            target_outer = $target
            size_ok = $sizeOk
            position_ok = $positionOk
        }
    }

    $ready = ($Config -ne $null) -and $displayMatches -and $missing.Count -eq 0 -and $sizeMismatches.Count -eq 0 -and $positionMismatches.Count -eq 0
    return [ordered]@{
        ok = $ready
        action = $Action
        policy_id = $HardPolicyId
        config_path = $ConfigPath
        config_exists = ($Config -ne $null)
        desktop = $Desktop
        display_ok = $displayMatches
        slots = $Slots
        missing_slots = $missing
        size_mismatch_slots = $sizeMismatches
        position_mismatch_slots = $positionMismatches
        items = $details
        ready = $ready
    }
}

function Move-SlotWindows {
    param(
        [int[]]$Slots,
        $Config
    )

    $items = Get-SlotWindows -Slots $Slots
    $failures = @($items | Where-Object { $_.missing })
    if ($failures.Count -gt 0) {
        throw "Cannot arrange until every selected game window is ready."
    }
    foreach ($item in $items) {
        $target = Get-LayoutTarget -Slot ([int]$item.slot) -Config $Config
        $handle = [System.IntPtr][int64]$item.hwnd
        [GuiTestPcWindowLayout]::ShowWindow($handle, [GuiTestPcWindowLayout]::SW_RESTORE) | Out-Null
        $flags = [GuiTestPcWindowLayout]::SWP_NOZORDER -bor [GuiTestPcWindowLayout]::SWP_NOACTIVATE -bor [GuiTestPcWindowLayout]::SWP_SHOWWINDOW -bor [GuiTestPcWindowLayout]::SWP_NOOWNERZORDER
        if (-not [GuiTestPcWindowLayout]::SetWindowPos(
            $handle,
            [System.IntPtr]::Zero,
            [int]$target.x,
            [int]$target.y,
            [int]$Config.expected_outer.width,
            [int]$Config.expected_outer.height,
            $flags
        )) {
            $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            throw "SetWindowPos failed for slot $($item.slot), Win32 error $code"
        }
    }
}

function Emit-Result {
    param($Result)
    if ($Json) {
        $Result | ConvertTo-Json -Depth 12 -Compress
    } else {
        $Result | ConvertTo-Json -Depth 12
    }
}

try {
    $slots = @(Parse-SlotList $SlotList)
    $desktop = Get-VirtualDesktop
    $config = Read-LayoutConfig
    Assert-HardLayoutConfig -Config $config -Desktop $desktop

    $before = Get-LayoutStatus -Slots $slots -Config $config -Desktop $desktop
    $moved = @()
    if ($Action -in @("arrange", "ensure") -and -not $before.ready) {
        if (-not $before.display_ok) {
            throw "Desktop physical resolution changed. The stored PICO layout cannot be trusted until the layout is recreated."
        }
        if ($before.missing_slots.Count -gt 0) {
            throw "Missing game windows for slots: $($before.missing_slots -join ',')"
        }
        $moved = @(@($before.size_mismatch_slots) + @($before.position_mismatch_slots) | Sort-Object -Unique)
        Move-SlotWindows -Slots $moved -Config $config
        Start-Sleep -Milliseconds 250
    }
    $after = Get-LayoutStatus -Slots $slots -Config $config -Desktop $desktop
    $after.moved_slots = $moved
    $after.action = $Action
    Emit-Result $after
    if (-not $after.ready) {
        exit 1
    }
} catch {
    Emit-Result ([ordered]@{
        ok = $false
        action = $Action
        config_path = $ConfigPath
        error = $_.Exception.Message
    })
    exit 1
}

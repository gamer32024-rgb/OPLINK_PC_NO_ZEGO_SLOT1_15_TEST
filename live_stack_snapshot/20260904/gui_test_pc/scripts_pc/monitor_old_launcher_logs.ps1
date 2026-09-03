param(
    [string]$Slots = "1-15",
    [int]$PollSeconds = 2,
    [int]$DurationMinutes = 60,
    [string]$OutputFile = "",
    [string]$StopFile = ""
)

$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if ([string]::IsNullOrWhiteSpace($OutputFile)) {
    $OutputFile = Join-Path $root "logs_pc\old_launcher_monitor_current.log"
}
if ([string]::IsNullOrWhiteSpace($StopFile)) {
    $StopFile = Join-Path $root "logs_pc\stop_old_launcher_monitor.flag"
}

function Ensure-ParentDir {
    param([string]$Path)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

function Write-Monitor {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"), $Message
    Write-Host $line
    Add-Content -LiteralPath $OutputFile -Value $line -Encoding UTF8
}

function Parse-SlotList {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) {
        return @(1..15)
    }
    $set = New-Object "System.Collections.Generic.HashSet[int]"
    foreach ($part in ($Text -split ",")) {
        $item = $part.Trim()
        if ($item -match "^(\d+)-(\d+)$") {
            $a = [int]$Matches[1]
            $b = [int]$Matches[2]
            if ($a -gt $b) {
                $tmp = $a
                $a = $b
                $b = $tmp
            }
            for ($i = $a; $i -le $b; $i++) {
                if ($i -ge 1 -and $i -le 15) {
                    $set.Add($i) | Out-Null
                }
            }
        } elseif ($item -match "^\d+$") {
            $i = [int]$item
            if ($i -ge 1 -and $i -le 15) {
                $set.Add($i) | Out-Null
            }
        }
    }
    return @($set | Sort-Object)
}

function Format-Slot {
    param([int]$Slot)
    return "SCG{0:D3}" -f $Slot
}

function Add-WatchFile {
    param(
        [System.Collections.ArrayList]$List,
        [string]$Label,
        [string]$Path,
        [string]$Pattern = "",
        [switch]$ImportantOnly
    )
    $position = 0L
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        $position = (Get-Item -LiteralPath $Path).Length
    }
    [void]$List.Add([pscustomobject]@{
        Label = $Label
        Path = $Path
        Pattern = $Pattern
        ImportantOnly = [bool]$ImportantOnly
        Position = $position
        Missing = -not (Test-Path -LiteralPath $Path -PathType Leaf)
    })
}

function Read-NewText {
    param(
        [string]$Path,
        [ref]$Position
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    $stream = $null
    try {
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        if ($stream.Length -lt $Position.Value) {
            $Position.Value = 0L
        }
        $length = [int]($stream.Length - $Position.Value)
        if ($length -le 0) {
            return ""
        }
        [void]$stream.Seek($Position.Value, [System.IO.SeekOrigin]::Begin)
        $buffer = New-Object byte[] $length
        [void]$stream.Read($buffer, 0, $length)
        $Position.Value = $stream.Length
        return [System.Text.Encoding]::UTF8.GetString($buffer)
    } catch {
        return ""
    } finally {
        if ($stream) {
            $stream.Dispose()
        }
    }
}

function Watch-AppCrashEvents {
    param([ref]$Since)
    $events = @(Get-WinEvent -FilterHashtable @{LogName="Application"; StartTime=$Since.Value} -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.ProviderName -match "Application Error|Windows Error Reporting") -and
            ($_.Message -match "StarCG\.exe|GameAssembly\.dll")
        } |
        Sort-Object TimeCreated)
    foreach ($event in $events) {
        $msg = ("" + $event.Message) -replace "`r?`n", " "
        $msg = $msg -replace "\s+", " "
        Write-Monitor ("[WER] {0} id={1} provider={2} {3}" -f $event.TimeCreated.ToString("yyyy-MM-dd HH:mm:ss"), $event.Id, $event.ProviderName, $msg)
        if ($event.TimeCreated -gt $Since.Value) {
            $Since.Value = $event.TimeCreated.AddMilliseconds(1)
        }
    }
}

function Get-AccountLength {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return "missing"
    }
    return (Get-Item -LiteralPath $Path).Length
}

function Watch-AccountSizes {
    param(
        [int[]]$SlotNumbers,
        [hashtable]$State
    )
    foreach ($slot in $SlotNumbers) {
        $name = Format-Slot $slot
        $runtime = Join-Path (Join-Path $env:USERPROFILE "AppData\LocalLow\CrossGate\$name") "account"
        $backup = "D:\15game\account_slots\$name\account"
        foreach ($entry in @(
            @{Key="$name runtime"; Path=$runtime},
            @{Key="$name backup"; Path=$backup}
        )) {
            $len = Get-AccountLength $entry.Path
            $key = $entry.Key
            if (-not $State.ContainsKey($key)) {
                $State[$key] = $len
                Write-Monitor ("[ACCOUNT] {0} length={1} path={2}" -f $key, $len, $entry.Path)
                if ($len -is [int] -and $len -gt 0 -and $len -lt 40) {
                    Write-Monitor ("[ACCOUNT-WARN] {0} suspicious_initial_length={1} path={2}" -f $key, $len, $entry.Path)
                }
                continue
            }
            if (("" + $State[$key]) -ne ("" + $len)) {
                Write-Monitor ("[ACCOUNT] {0} length_changed {1} -> {2} path={3}" -f $key, $State[$key], $len, $entry.Path)
                $State[$key] = $len
                if ($len -is [int] -and $len -gt 0 -and $len -lt 40) {
                    Write-Monitor ("[ACCOUNT-WARN] {0} suspicious_changed_length={1} path={2}" -f $key, $len, $entry.Path)
                }
            }
        }
    }
}

function Watch-StarCGProcesses {
    param([ref]$Previous)
    $items = @(Get-CimInstance Win32_Process -Filter "Name='StarCG.exe'" -ErrorAction SilentlyContinue | Sort-Object ProcessId)
    $signature = ($items | ForEach-Object { "{0}:{1}" -f $_.ProcessId, $_.ExecutablePath }) -join "|"
    if ($signature -eq $Previous.Value) {
        return
    }
    $Previous.Value = $signature
    if ($items.Count -eq 0) {
        Write-Monitor "[PROC] no StarCG.exe process"
        return
    }
    foreach ($item in $items) {
        $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
        $title = ""
        $responding = ""
        if ($process) {
            $title = $process.MainWindowTitle
            $responding = $process.Responding
        }
        Write-Monitor ("[PROC] pid={0} responding={1} title={2} path={3}" -f $item.ProcessId, $responding, $title, $item.ExecutablePath)
    }
}

Ensure-ParentDir $OutputFile
if (Test-Path -LiteralPath $StopFile) {
    Remove-Item -LiteralPath $StopFile -Force -ErrorAction SilentlyContinue
}
Set-Content -LiteralPath $OutputFile -Value "" -Encoding UTF8

$slotNumbers = @(Parse-SlotList $Slots)
$watchFiles = New-Object System.Collections.ArrayList
Add-WatchFile -List $watchFiles -Label "launcher_action" -Path "D:\15game\launcher_action.log"
Add-WatchFile -List $watchFiles -Label "bypass_stable" -Path "D:\15game\bypass_stable.log"
Add-WatchFile -List $watchFiles -Label "bypass_stdout" -Path "D:\15game\bypass_stable_stdout.log"
Add-WatchFile -List $watchFiles -Label "bypass_stderr" -Path "D:\15game\bypass_stable_stderr.log"

$playerPattern = "BindIP|ForceBind|FORCEDIP|Curl error|127\.0\.239\.148|Address not available|getaddrinfo|Connection refused|Crash!!!|LoginPanel|GameAssembly|QianNiao|account"
foreach ($slot in $slotNumbers) {
    $name = Format-Slot $slot
    $player = Join-Path (Join-Path $env:USERPROFILE "AppData\LocalLow\CrossGate\$name") "Player.log"
    Add-WatchFile -List $watchFiles -Label "$name Player.log" -Path $player -Pattern $playerPattern -ImportantOnly
}

Write-Monitor ("[START] monitor started pid={0} slots={1} poll={2}s duration={3}m" -f $PID, ($slotNumbers -join ","), $PollSeconds, $DurationMinutes)
Write-Monitor ("[START] output={0}" -f $OutputFile)
Write-Monitor ("[START] stop_file={0}" -f $StopFile)
Write-Monitor "[START] old launcher target=D:\15game"

foreach ($watch in $watchFiles) {
    Write-Monitor ("[BASELINE] watch label={0} exists={1} length={2} path={3}" -f $watch.Label, (Test-Path -LiteralPath $watch.Path -PathType Leaf), $watch.Position, $watch.Path)
}

$accountState = @{}
Watch-AccountSizes -SlotNumbers $slotNumbers -State $accountState
$lastEventTime = Get-Date
$processSignature = ""
$deadline = (Get-Date).AddMinutes($DurationMinutes)

while ((Get-Date) -lt $deadline) {
    if (Test-Path -LiteralPath $StopFile) {
        Write-Monitor "[STOP] stop file detected"
        break
    }
    foreach ($watch in $watchFiles) {
        if (-not (Test-Path -LiteralPath $watch.Path -PathType Leaf)) {
            if (-not $watch.Missing) {
                $watch.Missing = $true
                Write-Monitor ("[{0}] missing path={1}" -f $watch.Label, $watch.Path)
            }
            continue
        }
        if ($watch.Missing) {
            $watch.Missing = $false
            $watch.Position = 0L
            Write-Monitor ("[{0}] appeared path={1}" -f $watch.Label, $watch.Path)
        }
        $pos = [ref]$watch.Position
        $text = Read-NewText -Path $watch.Path -Position $pos
        $watch.Position = $pos.Value
        if ([string]::IsNullOrEmpty($text)) {
            continue
        }
        $lines = $text -split "`r?`n"
        foreach ($line in $lines) {
            if ([string]::IsNullOrWhiteSpace($line)) {
                continue
            }
            if ($watch.ImportantOnly -and $watch.Pattern -and ($line -notmatch $watch.Pattern)) {
                continue
            }
            Write-Monitor ("[{0}] {1}" -f $watch.Label, $line)
        }
    }
    Watch-AppCrashEvents -Since ([ref]$lastEventTime)
    Watch-AccountSizes -SlotNumbers $slotNumbers -State $accountState
    Watch-StarCGProcesses -Previous ([ref]$processSignature)
    Start-Sleep -Seconds ([Math]::Max(1, $PollSeconds))
}

Write-Monitor "[END] monitor stopped"

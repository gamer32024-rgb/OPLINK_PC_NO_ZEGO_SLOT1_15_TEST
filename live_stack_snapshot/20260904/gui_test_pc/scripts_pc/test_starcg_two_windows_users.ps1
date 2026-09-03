param(
    [string]$SlotList = "1,2",
    [string]$ConfigPath = "",
    [string]$GameRoot = "D:\TWFULLPC1.2.76",
    [int]$LaunchGapSeconds = 12,
    [int]$WindowTimeoutSeconds = 45,
    [switch]$AllowExisting
)

$ErrorActionPreference = "Stop"

function Parse-SlotList {
    param([string]$Text)
    $result = New-Object "System.Collections.Generic.HashSet[int]"
    foreach ($part in ($Text -split ",")) {
        $item = $part.Trim()
        if (-not $item) {
            continue
        }
        if ($item -match "^(\d+)-(\d+)$") {
            $start = [int]$Matches[1]
            $end = [int]$Matches[2]
            if ($start -gt $end) {
                $tmp = $start
                $start = $end
                $end = $tmp
            }
            foreach ($slot in $start..$end) {
                if ($slot -ge 1 -and $slot -le 15) {
                    $result.Add($slot) | Out-Null
                }
            }
        } elseif ($item -match "^\d+$") {
            $slot = [int]$item
            if ($slot -ge 1 -and $slot -le 15) {
                $result.Add($slot) | Out-Null
            }
        }
    }
    return @($result | Sort-Object)
}

function Unprotect-Password {
    param([string]$ProtectedPassword)
    $secure = ConvertTo-SecureString -String $ProtectedPassword
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        if ($ptr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
    }
}

function Get-ProcessOwnerText {
    param([int]$ProcessId)
    $item = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $item) {
        return ""
    }
    $owner = Invoke-CimMethod -InputObject $item -MethodName GetOwner -ErrorAction SilentlyContinue
    if ($owner -and $owner.ReturnValue -eq 0) {
        return ("{0}\{1}" -f $owner.Domain, $owner.User)
    }
    return ""
}

function Wait-StarCGWindow {
    param(
        [int]$ProcessId,
        [int]$TimeoutSeconds
    )
    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSeconds))
    do {
        $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($process -and $process.MainWindowHandle -ne 0) {
            return $process
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $null
}

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path (Split-Path -Parent $PSScriptRoot) "config_pc\starcg_windows_users.json"
}

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Missing user config: $ConfigPath. Run setup_starcg_slot_users.ps1 first."
}

$exePath = Join-Path $GameRoot "StarCG.exe"
if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    throw "Missing game executable: $exePath"
}

$existingProcesses = @(Get-Process -Name "StarCG" -ErrorAction SilentlyContinue)
if ($existingProcesses.Count -gt 0 -and -not $AllowExisting) {
    $ids = ($existingProcesses | Select-Object -ExpandProperty Id) -join ","
    throw "Existing StarCG.exe processes detected: $ids. Close them first or pass -AllowExisting."
}

$slots = @(Parse-SlotList $SlotList)
if ($slots.Count -eq 0) {
    throw "No valid slots in SlotList=$SlotList"
}

$config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$launched = @()

foreach ($slot in $slots) {
    $slotEntry = $config.slots.PSObject.Properties[[string]$slot]
    if (-not $slotEntry) {
        throw "Slot $slot is not configured in $ConfigPath"
    }
    $entry = $slotEntry.Value
    $password = Unprotect-Password -ProtectedPassword ([string]$entry.password_dpapi)
    $securePassword = ConvertTo-SecureString -String $password -AsPlainText -Force
    $userName = [string]$entry.username
    $domain = [string]$entry.domain
    $credentialUser = if ([string]::IsNullOrWhiteSpace($domain) -or $domain -eq ".") {
        ".\$userName"
    } else {
        "$domain\$userName"
    }
    $credential = New-Object System.Management.Automation.PSCredential($credentialUser, $securePassword)

    Write-Host ("launching slot {0} as {1}" -f $slot, $credentialUser)
    $process = Start-Process `
        -FilePath $exePath `
        -WorkingDirectory $GameRoot `
        -Credential $credential `
        -LoadUserProfile `
        -PassThru

    $windowProcess = Wait-StarCGWindow -ProcessId $process.Id -TimeoutSeconds $WindowTimeoutSeconds
    $ownerText = Get-ProcessOwnerText -ProcessId $process.Id
    $launched += [pscustomobject]@{
        Slot = $slot
        User = $credentialUser
        Pid = $process.Id
        Owner = $ownerText
        SessionId = $process.SessionId
        HasWindow = [bool]$windowProcess
        Title = if ($windowProcess) { $windowProcess.MainWindowTitle } else { "" }
    }

    if ($slot -ne $slots[-1]) {
        Start-Sleep -Seconds ([Math]::Max(0, $LaunchGapSeconds))
    }
}

$launched | Format-Table -AutoSize

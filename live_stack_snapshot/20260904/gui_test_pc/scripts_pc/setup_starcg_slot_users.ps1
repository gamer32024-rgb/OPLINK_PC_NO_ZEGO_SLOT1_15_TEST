param(
    [string]$SlotList = "1,2",
    [string]$UserPrefix = "scg_slot",
    [string]$ConfigPath = "",
    [string]$GameRoot = "D:\TWFULLPC1.2.76",
    [string]$ProjectRoot = "",
    [switch]$ResetPassword
)

$ErrorActionPreference = "Stop"
$MaxSlot = 20

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

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
                if ($slot -ge 1 -and $slot -le $MaxSlot) {
                    $result.Add($slot) | Out-Null
                }
            }
        } elseif ($item -match "^\d+$") {
            $slot = [int]$item
            if ($slot -ge 1 -and $slot -le $MaxSlot) {
                $result.Add($slot) | Out-Null
            }
        }
    }
    return @($result | Sort-Object)
}

function New-RandomPassword {
    $bytes = New-Object byte[] 24
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    return ("GtP!{0}a1" -f ([Convert]::ToBase64String($bytes).Substring(0, 22)))
}

function Protect-Password {
    param([string]$Password)
    $secure = ConvertTo-SecureString -String $Password -AsPlainText -Force
    return ($secure | ConvertFrom-SecureString)
}

if (-not (Test-Admin)) {
    throw "This setup must be run as Administrator because it creates local Windows users."
}

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
        $ProjectRoot = Split-Path -Parent $PSScriptRoot
    }
    $ConfigPath = Join-Path $ProjectRoot "config_pc\starcg_windows_users.json"
} elseif ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}

$slots = @(Parse-SlotList $SlotList)
if ($slots.Count -eq 0) {
    throw "No valid slots in SlotList=$SlotList"
}

$configDir = Split-Path -Parent $ConfigPath
if ($configDir -and -not (Test-Path -LiteralPath $configDir)) {
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null
}

$existing = @{}
$existingCreatedAt = ""
if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
    $raw = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8
    if (-not [string]::IsNullOrWhiteSpace($raw)) {
        $json = $raw | ConvertFrom-Json
        $existingCreatedAt = [string]$json.created_at
        foreach ($property in $json.slots.PSObject.Properties) {
            $existing[$property.Name] = $property.Value
        }
    }
}

$slotConfig = [ordered]@{}
foreach ($key in @($existing.Keys | Sort-Object { [int]$_ })) {
    $slotConfig[$key] = $existing[$key]
}
foreach ($slot in $slots) {
    $key = [string]$slot
    $userName = "{0}{1:D2}" -f $UserPrefix, $slot
    $localUser = Get-LocalUser -Name $userName -ErrorAction SilentlyContinue
    $password = $null
    $protectedPassword = $null

    if ($existing.ContainsKey($key) -and -not $ResetPassword) {
        $protectedPassword = [string]$existing[$key].password_dpapi
    } else {
        $password = New-RandomPassword
        $protectedPassword = Protect-Password -Password $password
    }

    if (-not $localUser) {
        if (-not $password) {
            $password = New-RandomPassword
            $protectedPassword = Protect-Password -Password $password
        }
        $securePassword = ConvertTo-SecureString -String $password -AsPlainText -Force
        New-LocalUser `
            -Name $userName `
            -Password $securePassword `
            -Description ("GUI_TEST_PC StarCG slot {0:D2} test user" -f $slot) `
            -PasswordNeverExpires `
            -UserMayNotChangePassword | Out-Null
        Write-Host "created local user $userName"
    } elseif ($ResetPassword) {
        $securePassword = ConvertTo-SecureString -String $password -AsPlainText -Force
        Set-LocalUser -Name $userName -Password $securePassword
        Write-Host "reset password for local user $userName"
    } else {
        Write-Host "local user exists $userName"
    }

    $slotConfig[$key] = [ordered]@{
        slot = $slot
        username = $userName
        domain = "."
        password_dpapi = $protectedPassword
    }
}

$payload = [ordered]@{
    version = 1
    created_at = if ($existingCreatedAt) { $existingCreatedAt } else { (Get-Date).ToString("s") }
    updated_at = (Get-Date).ToString("s")
    note = "Passwords are encrypted with Windows DPAPI for the current user account."
    slots = $slotConfig
}

$payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ConfigPath -Encoding UTF8

if (Test-Path -LiteralPath $GameRoot -PathType Container) {
    foreach ($slot in $slots) {
        $userName = "{0}{1:D2}" -f $UserPrefix, $slot
        & icacls $GameRoot /grant "${env:COMPUTERNAME}\${userName}:(OI)(CI)M" /T /C | Out-Null
        Write-Host "granted modify permission on $GameRoot to $userName"
    }
}

if (Test-Path -LiteralPath $ProjectRoot -PathType Container) {
    $logsRoot = Join-Path $ProjectRoot "logs_pc"
    if (-not (Test-Path -LiteralPath $logsRoot)) {
        New-Item -ItemType Directory -Force -Path $logsRoot | Out-Null
    }
    foreach ($slot in $slots) {
        $userName = "{0}{1:D2}" -f $UserPrefix, $slot
        & icacls $ProjectRoot /grant "${env:COMPUTERNAME}\${userName}:(OI)(CI)RX" /T /C | Out-Null
        & icacls $logsRoot /grant "${env:COMPUTERNAME}\${userName}:(OI)(CI)M" /T /C | Out-Null
        Write-Host "granted GUI_TEST_PC read/execute and log write permission to $userName"
    }
}

Write-Host "wrote $ConfigPath"

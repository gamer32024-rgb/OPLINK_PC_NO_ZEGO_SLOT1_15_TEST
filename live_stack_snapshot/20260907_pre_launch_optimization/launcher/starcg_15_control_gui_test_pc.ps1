param(
    [ValidateSet("status", "prepare", "start", "restart", "stop", "start-missing", "repair-bad", "relabel", "bind-test", "snapshot-login", "restore-login", "watch-login")]
    [string]$Action = "status",
    [string]$SlotList = "",
    [string]$Source = "D:\TWFULLPC1.2.76",
    [string]$TargetRoot = "D:\TWFULLPC1.2.76",
    [string]$BypassDir = "D:\15game",
    [int]$Slots = 20,
    [int]$DelaySeconds = 8,
    [int]$FinalBypassSeconds = 20,
    [int]$WatchSeconds = 86400,
    [int]$WatchIntervalSeconds = 10,
    [string]$LogPath = "D:\15game\launcher_action.log",
    [string]$ForceBindConfig = "D:\15game\forcebindip_config.txt",
    [string]$AccountSlotRoot = "D:\15game\account_slots",
    [string]$LoginPresetPath = "",
    [string]$WindowsUserConfigPath = "",
    [string]$ForceBindIPPath = "",
    [string]$NetBindLauncherPath = "",
    [string]$NetBindLogPath = "",
    [string]$BindIP = "",
    [switch]$UseNetBind,
    [switch]$UseWindowsUsers,
    [switch]$AllowVpnDefaultRoute,
    [switch]$NoForceBindIP,
    [switch]$Json
)

$ErrorActionPreference = "Stop"
$selfPid = $PID
$controlMutex = $null
$controlLockTaken = $false
$projectRoot = Split-Path -Parent $PSScriptRoot
$windowLayoutScript = Join-Path $projectRoot "scripts_pc\arrange_starcg_windows_pc.ps1"
$windowLayoutConfig = Join-Path $projectRoot "config_pc\window_layout.json"

function Write-LauncherLog {
    param([string]$Message)
    $dir = Split-Path -Parent $LogPath
    try {
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
        }
        $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
        Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
        Write-Host $line
    } catch {
        Write-Host ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message)
    }
}

function Enter-ControlLock {
    $mutatingActions = @("prepare", "start", "restart", "stop", "start-missing", "repair-bad", "snapshot-login", "restore-login")
    if ($Action -notin $mutatingActions) {
        return
    }

    $script:controlMutex = New-Object System.Threading.Mutex($false, "Global\StarCG15ControlLock")
    $script:controlLockTaken = $script:controlMutex.WaitOne(0)
    if (-not $script:controlLockTaken) {
        Write-LauncherLog "another control action is already running; ignored action=$Action slots=$SlotList"
        exit 10
    }
}

function Exit-ControlLock {
    if ($script:controlMutex) {
        if ($script:controlLockTaken) {
            $script:controlMutex.ReleaseMutex()
        }
        $script:controlMutex.Dispose()
    }
}

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Parse-SlotList {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) {
        return @(1..$Slots)
    }

    $result = New-Object "System.Collections.Generic.HashSet[int]"
    foreach ($part in ($Text -split ",")) {
        $item = $part.Trim()
        if ($item -match "^(\d+)-(\d+)$") {
            $start = [int]$Matches[1]
            $end = [int]$Matches[2]
            if ($start -gt $end) {
                $tmp = $start
                $start = $end
                $end = $tmp
            }
            foreach ($slot in $start..$end) {
                if ($slot -ge 1 -and $slot -le $Slots) {
                    $result.Add($slot) | Out-Null
                }
            }
        } elseif ($item -match "^\d+$") {
            $slot = [int]$item
            if ($slot -ge 1 -and $slot -le $Slots) {
                $result.Add($slot) | Out-Null
            }
        }
    }

    return @($result | Sort-Object)
}

function Get-SlotPath {
    param([int]$Slot)
    return $Source
}

function Get-SlotExePath {
    param([int]$Slot)
    return (Join-Path (Get-SlotPath $Slot) "StarCG.exe")
}

function Get-SlotProductName {
    param([int]$Slot)
    return ("SCG{0:D3}" -f $Slot)
}

function Get-CrossGateLocalLowRoot {
    return (Join-Path $env:USERPROFILE "AppData\LocalLow\CrossGate")
}

function Get-OriginalPersistentDataPath {
    return (Join-Path (Get-CrossGateLocalLowRoot) "StarCG")
}

function Get-SlotPersistentDataPath {
    param([int]$Slot)
    return (Join-Path (Get-CrossGateLocalLowRoot) (Get-SlotProductName $Slot))
}

function Get-SlotAccountBackupPath {
    param([int]$Slot)
    return (Join-Path $AccountSlotRoot (Get-SlotProductName $Slot))
}

function Get-SlotMutableGameRoot {
    param([int]$Slot)
    return (Join-Path (Get-SlotAccountBackupPath $Slot) "game_root")
}

function Get-MutableGameRelativePaths {
    @(
        "StarCG_Data\chat"
    )
}

function Initialize-SlotMutableGameData {
    param([int]$Slot)

    foreach ($relativePath in (Get-MutableGameRelativePaths)) {
        $sourcePath = Join-Path $Source $relativePath
        $destinationPath = Join-Path (Get-SlotMutableGameRoot $Slot) $relativePath
        if (-not (Test-Path -LiteralPath $destinationPath)) {
            $destinationParent = Split-Path -Parent $destinationPath
            if ($destinationParent -and -not (Test-Path -LiteralPath $destinationParent)) {
                New-Item -ItemType Directory -Force -Path $destinationParent | Out-Null
            }
            if (Test-Path -LiteralPath $sourcePath -PathType Container) {
                Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Recurse -Force
            } elseif (Test-Path -LiteralPath $sourcePath -PathType Leaf) {
                Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force
            } else {
                if ($relativePath -notmatch "\.[a-zA-Z0-9]{1,10}$") {
                    New-Item -ItemType Directory -Force -Path $destinationPath | Out-Null
                }
            }
        }
    }
}

function Get-SlotFileRedirectPairs {
    param([int]$Slot)

    $pairs = @(
        [pscustomobject]@{
            From = Get-OriginalPersistentDataPath
            To = Get-SlotPersistentDataPath $Slot
        }
    )

    foreach ($relativePath in (Get-MutableGameRelativePaths)) {
        $sourcePath = Join-Path $Source $relativePath
        $destinationPath = Join-Path (Get-SlotMutableGameRoot $Slot) $relativePath
        $pairs += [pscustomobject]@{
            From = $sourcePath
            To = $destinationPath
        }
    }

    return $pairs
}

function Get-SlotPidMapPath {
    return (Join-Path $BypassDir "gui_test_pc_slot_pids.json")
}

function Read-SlotPidMap {
    $path = Get-SlotPidMapPath
    $map = @{}
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return $map
    }

    try {
        $raw = Get-Content -LiteralPath $path -Raw -ErrorAction Stop
        if ([string]::IsNullOrWhiteSpace($raw)) {
            return $map
        }
        $json = $raw | ConvertFrom-Json -ErrorAction Stop
        foreach ($property in $json.PSObject.Properties) {
            $map[$property.Name] = $property.Value
        }
    } catch {
        Write-LauncherLog "slot pid map read failed path=$path error=$($_.Exception.Message)"
    }
    return $map
}

function Write-SlotPidMap {
    param([hashtable]$Map)

    $path = Get-SlotPidMapPath
    $dir = Split-Path -Parent $path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }

    $ordered = [ordered]@{}
    foreach ($key in @($Map.Keys | Sort-Object { [int]$_ })) {
        $value = $Map[$key]
        $ordered[$key] = [ordered]@{
            Pid = [int]$value.Pid
            Exe = [string]$value.Exe
            StartedAt = [string]$value.StartedAt
            Product = [string]$value.Product
            WindowsUser = [string]$value.WindowsUser
        }
    }
    $json = $ordered | ConvertTo-Json -Depth 4
    if ([string]::IsNullOrWhiteSpace($json)) {
        $json = "{}"
    }
    [System.IO.File]::WriteAllText($path, $json, [System.Text.Encoding]::UTF8)
}

function Test-StarCGPidAlive {
    param([int]$ProcessId)

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    return ($process -and $process.ProcessName -eq "StarCG")
}

function Get-MappedSlotPid {
    param([int]$Slot)

    $map = Read-SlotPidMap
    $key = [string]$Slot
    if (-not $map.ContainsKey($key)) {
        return $null
    }

    $mappedProcessId = [int]$map[$key].Pid
    if (Test-StarCGPidAlive $mappedProcessId) {
        return $mappedProcessId
    }

    $map.Remove($key)
    Write-SlotPidMap -Map $map
    return $null
}

function Register-SlotProcess {
    param(
        [int]$Slot,
        [int]$ProcessId,
        [string]$WindowsUser = ""
    )

    if ($ProcessId -le 0) {
        return
    }

    $map = Read-SlotPidMap
    $map[[string]$Slot] = [pscustomobject]@{
        Pid = $ProcessId
        Exe = Get-SlotExePath $Slot
        StartedAt = (Get-Date).ToString("s")
        Product = Get-SlotProductName $Slot
        WindowsUser = $WindowsUser
    }
    Write-SlotPidMap -Map $map
    $userText = if ($WindowsUser) { " user=$WindowsUser" } else { "" }
    Write-LauncherLog "registered slot $Slot pid=$ProcessId$userText source=$(Get-SlotExePath $Slot)"
}

function Unregister-SlotProcess {
    param([int]$Slot)

    $map = Read-SlotPidMap
    $key = [string]$Slot
    if ($map.ContainsKey($key)) {
        $map.Remove($key)
        Write-SlotPidMap -Map $map
    }
}

function Get-LoginDataFiles {
    @("account", "config.ini", "system_config.ini", "group_settings.data", "miniChatPanelSettings.ini")
}

function Test-UsefulLoginDataFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    try {
        $item = Get-Item -LiteralPath $Path
        if ($item.Length -le 0) {
            return $false
        }
        # Observed broken StarCG account files are 24 bytes; known-good template/backups are 56 bytes.
        if ($item.Name -eq "account" -and $item.Length -lt 40) {
            return $false
        }
        return $true
    } catch {
        return $false
    }
}

function Test-SameFileContent {
    param(
        [string]$Left,
        [string]$Right
    )
    if (-not (Test-Path -LiteralPath $Left -PathType Leaf) -or -not (Test-Path -LiteralPath $Right -PathType Leaf)) {
        return $false
    }

    $leftItem = Get-Item -LiteralPath $Left
    $rightItem = Get-Item -LiteralPath $Right
    if ($leftItem.Length -ne $rightItem.Length) {
        return $false
    }

    try {
        $leftHash = (Get-FileHash -LiteralPath $Left -Algorithm SHA256).Hash
        $rightHash = (Get-FileHash -LiteralPath $Right -Algorithm SHA256).Hash
        return ($leftHash -eq $rightHash)
    } catch {
        return $false
    }
}

function Copy-UsefulLoginDataFile {
    param(
        [string]$SourcePath,
        [string]$DestinationPath
    )
    if (-not (Test-UsefulLoginDataFile $SourcePath)) {
        return $false
    }
    if ((Test-Path -LiteralPath $DestinationPath -PathType Leaf) -and (Test-SameFileContent $SourcePath $DestinationPath)) {
        return $false
    }

    $destinationDir = Split-Path -Parent $DestinationPath
    if ($destinationDir -and -not (Test-Path -LiteralPath $destinationDir)) {
        New-Item -ItemType Directory -Force -Path $destinationDir | Out-Null
    }
    Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Force
    return $true
}

function Get-LoginPresetPath {
    if (-not [string]::IsNullOrWhiteSpace($LoginPresetPath)) {
        return $LoginPresetPath
    }
    $projectRoot = Split-Path -Parent $PSScriptRoot
    return (Join-Path $projectRoot "config_pc\starcg_login_presets.csv")
}

function Get-SlotLoginPreset {
    param([int]$Slot)

    $path = Get-LoginPresetPath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return $null
    }

    try {
        $rows = @(Import-Csv -LiteralPath $path -Encoding UTF8)
        foreach ($row in $rows) {
            if ([int]$row.Slot -eq $Slot) {
                return $row
            }
        }
    } catch {
        Write-LauncherLog "login preset read failed path=$path error=$($_.Exception.Message)"
    }
    return $null
}

function Format-StarCGPhoneAccount {
    param([string]$Account)
    if ([string]::IsNullOrWhiteSpace($Account)) {
        return ""
    }
    $value = $Account.Trim()
    if ($value.StartsWith("+")) {
        return $value
    }
    if ($value.StartsWith("852") -and $value.Length -eq 11) {
        return "+$value"
    }
    return "+852$value"
}

function Set-IniValue {
    param(
        [string]$Path,
        [string]$Section,
        [string]$Key,
        [string]$Value
    )

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }

    $lines = @()
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        $lines = @(Get-Content -LiteralPath $Path -Encoding UTF8)
    }

    $result = New-Object System.Collections.Generic.List[string]
    $inTarget = $false
    $sectionFound = $false
    $keyWritten = $false

    foreach ($line in $lines) {
        if ($line -match '^\s*\[(.+)\]\s*$') {
            if ($inTarget -and -not $keyWritten) {
                $result.Add("$Key=$Value") | Out-Null
                $keyWritten = $true
            }
            $inTarget = ($Matches[1] -eq $Section)
            if ($inTarget) {
                $sectionFound = $true
            }
            $result.Add($line) | Out-Null
            continue
        }

        if ($inTarget -and $line -match "^\s*$([regex]::Escape($Key))\s*=") {
            if (-not $keyWritten) {
                $result.Add("$Key=$Value") | Out-Null
                $keyWritten = $true
            }
            continue
        }
        $result.Add($line) | Out-Null
    }

    if (-not $sectionFound) {
        if ($result.Count -gt 0 -and $result[$result.Count - 1] -ne "") {
            $result.Add("") | Out-Null
        }
        $result.Add("[$Section]") | Out-Null
        $result.Add("$Key=$Value") | Out-Null
    } elseif ($inTarget -and -not $keyWritten) {
        $result.Add("$Key=$Value") | Out-Null
    }

    Set-Content -LiteralPath $Path -Value $result -Encoding UTF8
}

function Apply-SlotLoginPreset {
    param([int]$Slot)

    $preset = Get-SlotLoginPreset -Slot $Slot
    if (-not $preset) {
        return $false
    }

    $account = Format-StarCGPhoneAccount $preset.Account
    if ([string]::IsNullOrWhiteSpace($account)) {
        return $false
    }

    $slotDataDir = Get-SlotPersistentDataPath $Slot
    $backupDir = Get-SlotAccountBackupPath $Slot
    $slotConfig = Join-Path $slotDataDir "config.ini"
    $backupConfig = Join-Path $backupDir "config.ini"

    Set-IniValue -Path $slotConfig -Section "System" -Key "PhoneAreaCodeOrderMigrated" -Value "1"
    Set-IniValue -Path $slotConfig -Section "System" -Key "PrivacyMode" -Value "0"
    Set-IniValue -Path $slotConfig -Section "System" -Key "LastQuitAccount" -Value $account

    Set-IniValue -Path $backupConfig -Section "System" -Key "PhoneAreaCodeOrderMigrated" -Value "1"
    Set-IniValue -Path $backupConfig -Section "System" -Key "PrivacyMode" -Value "0"
    Set-IniValue -Path $backupConfig -Section "System" -Key "LastQuitAccount" -Value $account

    Write-LauncherLog "applied login preset slot $Slot account=$account passwordPreset=$([bool](-not [string]::IsNullOrWhiteSpace($preset.Password)))"
    return $true
}

function Save-SlotLoginData {
    param(
        [int]$Slot,
        [switch]$Quiet
    )

    $sourceDir = Get-SlotPersistentDataPath $Slot
    if (-not (Test-Path -LiteralPath $sourceDir)) {
        return $false
    }

    $backupDir = Get-SlotAccountBackupPath $Slot
    $copied = @()
    foreach ($fileName in (Get-LoginDataFiles)) {
        $sourcePath = Join-Path $sourceDir $fileName
        $destinationPath = Join-Path $backupDir $fileName
        if (Copy-UsefulLoginDataFile -SourcePath $sourcePath -DestinationPath $destinationPath) {
            $copied += $fileName
        }
    }

    if ($copied.Count -gt 0 -and -not $Quiet) {
        Write-LauncherLog "snapshotted login data slot $Slot files=$($copied -join ',')"
    }
    return ($copied.Count -gt 0)
}

function Restore-SlotLoginData {
    param([int]$Slot)

    $slotDataDir = Get-SlotPersistentDataPath $Slot
    $backupDir = Get-SlotAccountBackupPath $Slot
    $templateDir = Get-OriginalPersistentDataPath
    $copied = @()

    if (-not (Test-Path -LiteralPath $slotDataDir)) {
        New-Item -ItemType Directory -Force -Path $slotDataDir | Out-Null
    }

    if (Test-UsefulLoginDataFile (Join-Path $slotDataDir "account")) {
        Save-SlotLoginData -Slot $Slot -Quiet | Out-Null
    }

    foreach ($fileName in (Get-LoginDataFiles)) {
        $destinationPath = Join-Path $slotDataDir $fileName
        $backupPath = Join-Path $backupDir $fileName
        $templatePath = Join-Path $templateDir $fileName

        if (Copy-UsefulLoginDataFile -SourcePath $backupPath -DestinationPath $destinationPath) {
            $copied += "${fileName}:backup"
            continue
        }

        if (-not (Test-UsefulLoginDataFile $destinationPath)) {
            if (Copy-UsefulLoginDataFile -SourcePath $templatePath -DestinationPath $destinationPath) {
                $copied += "${fileName}:template"
            }
        }
    }

    if ($copied.Count -gt 0) {
        Write-LauncherLog "restored login data slot $Slot files=$($copied -join ',')"
        Save-SlotLoginData -Slot $Slot -Quiet | Out-Null
    }

    Apply-SlotLoginPreset -Slot $Slot | Out-Null
}

function Get-SlotLoginDataStatus {
    param([int]$Slot)

    if ($UseWindowsUsers) {
        try {
            return ("windows-user:{0}" -f (Get-SlotWindowsUserLabel $Slot))
        } catch {
            return "windows-user:missing"
        }
    }

    $slotDataDir = Get-SlotPersistentDataPath $Slot
    $backupDir = Get-SlotAccountBackupPath $Slot
    $currentAccount = Join-Path $slotDataDir "account"
    $backupAccount = Join-Path $backupDir "account"

    $parts = @()
    if (Test-UsefulLoginDataFile $currentAccount) {
        $item = Get-Item -LiteralPath $currentAccount
        $parts += ("current:{0}B" -f $item.Length)
    } else {
        $parts += "current:missing"
    }

    if (Test-UsefulLoginDataFile $backupAccount) {
        $item = Get-Item -LiteralPath $backupAccount
        $parts += ("backup:{0}B" -f $item.Length)
    } else {
        $parts += "backup:missing"
    }

    return ($parts -join " ")
}

function Save-AllSlotLoginData {
    foreach ($slot in 1..$Slots) {
        Save-SlotLoginData -Slot $slot | Out-Null
    }
}

function Restore-AllSlotLoginData {
    foreach ($slot in 1..$Slots) {
        Restore-SlotLoginData -Slot $slot
    }
}

function Find-Handle64 {
    $candidates = @(
        (Join-Path $BypassDir "handle64.exe"),
        "C:\Program Files\Sysinternals\handle64.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $wingetRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path -LiteralPath $wingetRoot) {
        $found = Get-ChildItem -LiteralPath $wingetRoot -Filter "handle64.exe" -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($found) {
            return $found.FullName
        }
    }

    $command = Get-Command "handle64.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    return $null
}

function Read-KeyValueFile {
    param([string]$Path)
    $config = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $config
    }

    foreach ($line in (Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue)) {
        if ($line -match "^\s*([^#=]+?)\s*=\s*(.*?)\s*$") {
            $config[$Matches[1].Trim()] = $Matches[2].Trim()
        }
    }
    return $config
}

function Quote-ProcessArgument {
    param([string]$Value)
    return ('"{0}"' -f ($Value -replace '"', '\"'))
}

function Get-WindowsUserConfigPath {
    if (-not [string]::IsNullOrWhiteSpace($WindowsUserConfigPath)) {
        return $WindowsUserConfigPath
    }
    $projectRoot = Split-Path -Parent $PSScriptRoot
    return (Join-Path $projectRoot "config_pc\starcg_windows_users.json")
}

function Read-WindowsUserConfig {
    if (-not $UseWindowsUsers) {
        return $null
    }

    $path = Get-WindowsUserConfigPath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Windows user config not found: $path. Run scripts_pc\setup_starcg_slot_users.ps1 first."
    }

    try {
        return (Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json)
    } catch {
        throw "Windows user config read failed path=$path error=$($_.Exception.Message)"
    }
}

function Get-SlotWindowsUserEntry {
    param([int]$Slot)

    if (-not $UseWindowsUsers) {
        return $null
    }

    $config = Read-WindowsUserConfig
    if (-not $config -or -not $config.slots) {
        throw "Windows user config has no slots: $(Get-WindowsUserConfigPath)"
    }

    $property = $config.slots.PSObject.Properties[[string]$Slot]
    if (-not $property) {
        throw "No Windows user configured for slot $Slot in $(Get-WindowsUserConfigPath)"
    }
    return $property.Value
}

function Get-SlotWindowsCredential {
    param([int]$Slot)

    $entry = Get-SlotWindowsUserEntry -Slot $Slot
    if (-not $entry) {
        return $null
    }

    $username = [string]$entry.username
    $domain = [string]$entry.domain
    $protectedPassword = [string]$entry.password_dpapi
    if ([string]::IsNullOrWhiteSpace($username) -or [string]::IsNullOrWhiteSpace($protectedPassword)) {
        throw "Incomplete Windows user credential for slot $Slot in $(Get-WindowsUserConfigPath)"
    }

    $credentialUser = if ([string]::IsNullOrWhiteSpace($domain) -or $domain -eq ".") {
        ".\$username"
    } else {
        "$domain\$username"
    }
    $securePassword = ConvertTo-SecureString -String $protectedPassword
    return (New-Object System.Management.Automation.PSCredential($credentialUser, $securePassword))
}

function Get-SlotWindowsUserLabel {
    param([int]$Slot)
    $entry = Get-SlotWindowsUserEntry -Slot $Slot
    if (-not $entry) {
        return ""
    }
    $domain = [string]$entry.domain
    $username = [string]$entry.username
    if ([string]::IsNullOrWhiteSpace($domain) -or $domain -eq ".") {
        return ".\$username"
    }
    return "$domain\$username"
}

function Start-SlotProcess {
    param(
        [int]$Slot,
        [string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory,
        [switch]$HiddenWindow
    )

    $startArgs = @{
        FilePath = $FilePath
        WorkingDirectory = $WorkingDirectory
        PassThru = $true
    }
    if ($Arguments -and $Arguments.Count -gt 0) {
        $startArgs.ArgumentList = (($Arguments | ForEach-Object { Quote-ProcessArgument $_ }) -join " ")
    }
    if ($HiddenWindow) {
        $startArgs.WindowStyle = "Hidden"
    }

    $credential = Get-SlotWindowsCredential -Slot $Slot
    if ($credential) {
        $startArgs.Credential = $credential
        $startArgs.LoadUserProfile = $true
        Write-LauncherLog "launching slot $Slot as Windows user $(Get-SlotWindowsUserLabel $Slot)"
    }

    return (Start-Process @startArgs)
}

function Write-AsciiNoBom {
    param(
        [string]$Path,
        [string]$Text
    )
    [System.IO.File]::WriteAllText($Path, $Text, [System.Text.Encoding]::ASCII)
}

function Patch-AsciiProductName {
    param(
        [string]$Path,
        [string]$OldName,
        [string]$NewName
    )

    $old = [System.Text.Encoding]::ASCII.GetBytes($OldName)
    $new = [System.Text.Encoding]::ASCII.GetBytes($NewName)
    if ($new.Length -ne $old.Length) {
        throw "Product name '$NewName' must be exactly $($old.Length) ASCII bytes."
    }

    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $replacements = 0
    for ($i = 0; $i -le $bytes.Length - $old.Length; $i++) {
        $matches = $true
        for ($j = 0; $j -lt $old.Length; $j++) {
            if ($bytes[$i + $j] -ne $old[$j]) {
                $matches = $false
                break
            }
        }

        if ($matches) {
            for ($j = 0; $j -lt $new.Length; $j++) {
                $bytes[$i + $j] = $new[$j]
            }
            $replacements++
        }
    }

    if ($replacements -lt 1) {
        throw "Could not find product name marker '$OldName' in $Path"
    }
    [System.IO.File]::WriteAllBytes($Path, $bytes)
}

function Set-SlotUnityProductName {
    param(
        [int]$Slot,
        [string]$ProductName
    )

    if ($ProductName -ne "StarCG") {
        throw "single-source mode cannot patch Unity product name per slot; use GUI_TEST_PC netbind profile redirect instead"
    }
    return

    $sourceDataPath = Join-Path $Source "StarCG_Data"
    $slotDataPath = Join-Path (Get-SlotPath $Slot) "StarCG_Data"
    $sourceAppInfo = Join-Path $sourceDataPath "app.info"
    $sourceManager = Join-Path $sourceDataPath "globalgamemanagers"
    $slotAppInfo = Join-Path $slotDataPath "app.info"
    $slotManager = Join-Path $slotDataPath "globalgamemanagers"

    if (-not (Test-Path -LiteralPath $sourceAppInfo) -or -not (Test-Path -LiteralPath $sourceManager)) {
        throw "Source StarCG_Data app.info/globalgamemanagers not found under $sourceDataPath"
    }
    if (-not (Test-Path -LiteralPath $slotDataPath)) {
        throw "Slot StarCG_Data not found: $slotDataPath"
    }

    Copy-Item -LiteralPath $sourceAppInfo -Destination $slotAppInfo -Force
    Copy-Item -LiteralPath $sourceManager -Destination $slotManager -Force

    if ($ProductName -ne "StarCG") {
        Write-AsciiNoBom -Path $slotAppInfo -Text "CrossGate`n$ProductName"
        Patch-AsciiProductName -Path $slotManager -OldName "StarCG" -NewName $ProductName
    }
}

function Get-DefaultNetBindLauncherPath {
    $projectRoot = Split-Path -Parent $PSScriptRoot
    return (Join-Path $projectRoot "netbind_pc\build_ninja\GuiTestNetBindLauncher.exe")
}

function Get-DefaultNetBindLogPath {
    if (-not [string]::IsNullOrWhiteSpace($NetBindLogPath)) {
        return $NetBindLogPath
    }
    $dir = Split-Path -Parent $LogPath
    if ([string]::IsNullOrWhiteSpace($dir)) {
        $dir = $PSScriptRoot
    }
    return (Join-Path $dir "gui_test_pc_netbind_hook.log")
}

function Get-ForceBindSettings {
    param([int]$Slot)

    if ($NoForceBindIP) {
        return $null
    }

    $config = Read-KeyValueFile $ForceBindConfig
    $mode = if ($UseNetBind) { "netbind" } else { "forcebindip" }
    if ($UseNetBind) {
        $path = $NetBindLauncherPath
        if ([string]::IsNullOrWhiteSpace($path)) {
            $path = Get-DefaultNetBindLauncherPath
        }
    } else {
        $path = $ForceBindIPPath
        if ([string]::IsNullOrWhiteSpace($path)) {
            $path = $config["FORCEBINDIP_PATH"]
        }
    }

    $ip = $BindIP
    $group = ""
    $adapter = ""
    $interfaceIndex = ""
    $interfaceDescription = ""
    $macAddress = ""
    $gateway = ""
    $slotKey = "SLOT_{0:D2}_IP" -f $Slot
    $slotGroupKey = "SLOT_{0:D2}_GROUP" -f $Slot
    $slotAdapterKey = "SLOT_{0:D2}_ADAPTER" -f $Slot
    $slotInterfaceIndexKey = "SLOT_{0:D2}_INTERFACE_INDEX" -f $Slot
    $slotDescriptionKey = "SLOT_{0:D2}_DESCRIPTION" -f $Slot
    $slotMacKey = "SLOT_{0:D2}_MAC" -f $Slot
    $slotGatewayKey = "SLOT_{0:D2}_GATEWAY" -f $Slot
    if ([string]::IsNullOrWhiteSpace($ip) -and $config.ContainsKey($slotKey)) {
        $ip = $config[$slotKey]
    }
    if ($config.ContainsKey($slotGroupKey)) {
        $group = $config[$slotGroupKey]
    }
    if ($config.ContainsKey($slotAdapterKey)) {
        $adapter = $config[$slotAdapterKey]
    }
    if ($config.ContainsKey($slotInterfaceIndexKey)) {
        $interfaceIndex = $config[$slotInterfaceIndexKey]
    }
    if ($config.ContainsKey($slotDescriptionKey)) {
        $interfaceDescription = $config[$slotDescriptionKey]
    }
    if ($config.ContainsKey($slotMacKey)) {
        $macAddress = $config[$slotMacKey]
    }
    if ($config.ContainsKey($slotGatewayKey)) {
        $gateway = $config[$slotGatewayKey]
    }
    if ([string]::IsNullOrWhiteSpace($adapter) -and -not [string]::IsNullOrWhiteSpace($group)) {
        $groupAdapterKey = "GROUP_{0}_ADAPTER" -f $group.Trim().ToUpperInvariant()
        if ($config.ContainsKey($groupAdapterKey)) {
            $adapter = $config[$groupAdapterKey]
        }
    }
    if ([string]::IsNullOrWhiteSpace($interfaceIndex) -and -not [string]::IsNullOrWhiteSpace($group)) {
        $groupInterfaceIndexKey = "GROUP_{0}_INTERFACE_INDEX" -f $group.Trim().ToUpperInvariant()
        if ($config.ContainsKey($groupInterfaceIndexKey)) {
            $interfaceIndex = $config[$groupInterfaceIndexKey]
        }
    }
    if ([string]::IsNullOrWhiteSpace($interfaceDescription) -and -not [string]::IsNullOrWhiteSpace($group)) {
        $groupDescriptionKey = "GROUP_{0}_DESCRIPTION" -f $group.Trim().ToUpperInvariant()
        if ($config.ContainsKey($groupDescriptionKey)) {
            $interfaceDescription = $config[$groupDescriptionKey]
        }
    }
    if ([string]::IsNullOrWhiteSpace($macAddress) -and -not [string]::IsNullOrWhiteSpace($group)) {
        $groupMacKey = "GROUP_{0}_MAC" -f $group.Trim().ToUpperInvariant()
        if ($config.ContainsKey($groupMacKey)) {
            $macAddress = $config[$groupMacKey]
        }
    }
    if ([string]::IsNullOrWhiteSpace($gateway) -and -not [string]::IsNullOrWhiteSpace($group)) {
        $groupGatewayKey = "GROUP_{0}_GATEWAY" -f $group.Trim().ToUpperInvariant()
        if ($config.ContainsKey($groupGatewayKey)) {
            $gateway = $config[$groupGatewayKey]
        }
    }
    $resolutionAttempts = @()
    if (-not [string]::IsNullOrWhiteSpace($macAddress)) {
        $resolutionAttempts += @{ Type = "mac"; Value = $macAddress }
    }
    if (-not [string]::IsNullOrWhiteSpace($interfaceDescription)) {
        $resolutionAttempts += @{ Type = "description"; Value = $interfaceDescription }
    }
    if (-not [string]::IsNullOrWhiteSpace($gateway)) {
        $resolutionAttempts += @{ Type = "gateway"; Value = $gateway }
    }
    if (-not [string]::IsNullOrWhiteSpace($adapter)) {
        $resolutionAttempts += @{ Type = "adapter"; Value = $adapter }
    }
    # Interface indexes can be reassigned when USB, Tailscale, or VPN adapters
    # reconnect. Use the numeric index only after stable adapter identities fail.
    if (-not [string]::IsNullOrWhiteSpace($interfaceIndex)) {
        $resolutionAttempts += @{ Type = "ifindex"; Value = $interfaceIndex }
    }

    foreach ($attempt in $resolutionAttempts) {
        if (-not [string]::IsNullOrWhiteSpace($ip)) {
            break
        }
        if ($attempt.Type -eq "mac") {
            $resolved = Resolve-AdapterIPv4 -MacAddress $attempt.Value
        } elseif ($attempt.Type -eq "ifindex") {
            $resolved = Resolve-AdapterIPv4 -InterfaceIndex ([int]$attempt.Value)
        } elseif ($attempt.Type -eq "gateway") {
            $resolved = Resolve-AdapterIPv4 -Gateway $attempt.Value
        } elseif ($attempt.Type -eq "description") {
            $resolved = Resolve-AdapterIPv4 -InterfaceDescription $attempt.Value
        } else {
            $resolved = Resolve-AdapterIPv4 -AdapterAlias $attempt.Value
        }

        if ($resolved -and -not [string]::IsNullOrWhiteSpace($resolved.IP)) {
            $ip = $resolved.IP
            $adapter = $resolved.Adapter
            $interfaceIndex = $resolved.InterfaceIndex
            $interfaceDescription = $resolved.InterfaceDescription
            $macAddress = $resolved.MacAddress
            $gateway = $resolved.Gateway
            break
        }
    }
    if ([string]::IsNullOrWhiteSpace($ip)) {
        $ip = $config["DEFAULT_IP"]
    }
    if ([string]::IsNullOrWhiteSpace($ip)) {
        $ip = $config["BIND_IP"]
    }

    if ([string]::IsNullOrWhiteSpace($path) -or [string]::IsNullOrWhiteSpace($ip)) {
        return $null
    }
    if (-not (Test-Path -LiteralPath $path)) {
        Write-LauncherLog "$mode path not found: $path"
        return $null
    }

    return [pscustomobject]@{
        Mode = $mode
        Path = (Resolve-Path -LiteralPath $path).Path
        IP = $ip
        Group = $group
        Adapter = $adapter
        InterfaceIndex = $interfaceIndex
        InterfaceDescription = $interfaceDescription
        MacAddress = $macAddress
        Gateway = $gateway
    }
}

function Resolve-AdapterIPv4 {
    param(
        [string]$AdapterAlias = "",
        [string]$InterfaceDescription = "",
        [int]$InterfaceIndex = 0,
        [string]$MacAddress = "",
        [string]$Gateway = ""
    )

    if (-not [string]::IsNullOrWhiteSpace($MacAddress)) {
        $normalizedMac = ($MacAddress -replace "[:-]", "").ToUpperInvariant()
        $adapter = Get-NetAdapter -ErrorAction SilentlyContinue |
            Where-Object { (($_.MacAddress -replace "[:-]", "").ToUpperInvariant()) -eq $normalizedMac } |
            Sort-Object @{Expression={if ($_.Status -eq "Up") {0} else {1}}}, InterfaceIndex |
            Select-Object -First 1
    } elseif (-not [string]::IsNullOrWhiteSpace($InterfaceDescription)) {
        $adapter = Get-NetAdapter -ErrorAction SilentlyContinue |
            Where-Object { $_.InterfaceDescription -eq $InterfaceDescription } |
            Sort-Object @{Expression={if ($_.Status -eq "Up") {0} else {1}}}, InterfaceIndex |
            Select-Object -First 1
    } elseif (-not [string]::IsNullOrWhiteSpace($AdapterAlias)) {
        $adapter = Get-NetAdapter -Name $AdapterAlias -ErrorAction SilentlyContinue
    } elseif ($InterfaceIndex -gt 0) {
        $adapter = Get-NetAdapter -InterfaceIndex $InterfaceIndex -ErrorAction SilentlyContinue
    } elseif (-not [string]::IsNullOrWhiteSpace($Gateway)) {
        $configByGateway = Get-NetIPConfiguration -ErrorAction SilentlyContinue |
            Where-Object { @($_.IPv4DefaultGateway | Where-Object { $_.NextHop -eq $Gateway }).Count -gt 0 } |
            Select-Object -First 1
        if ($configByGateway) {
            $adapter = Get-NetAdapter -InterfaceIndex $configByGateway.InterfaceIndex -ErrorAction SilentlyContinue
        }
    }
    if (-not $adapter -or $adapter.Status -ne "Up") {
        $label = if ($MacAddress) { "mac=$MacAddress" } elseif ($InterfaceDescription) { $InterfaceDescription } elseif ($AdapterAlias) { $AdapterAlias } elseif ($InterfaceIndex -gt 0) { "ifIndex=$InterfaceIndex" } elseif ($Gateway) { "gateway=$Gateway" } else { "adapter" }
        Write-LauncherLog "adapter not up or not found: $label"
        return [pscustomobject]@{ IP = ""; Adapter = $label; InterfaceIndex = $InterfaceIndex; InterfaceDescription = $InterfaceDescription; MacAddress = $MacAddress; Gateway = $Gateway }
    }

    $config = Get-NetIPConfiguration -InterfaceIndex $adapter.ifIndex -ErrorAction SilentlyContinue
    $addresses = @($config.IPv4Address |
        Where-Object {
            $_.IPAddress -and
            $_.IPAddress -notlike "169.254.*" -and
            $_.IPAddress -ne "127.0.0.1"
        } |
        Select-Object -ExpandProperty IPAddress)

    if ($addresses.Count -lt 1) {
        Write-LauncherLog "no usable IPv4 on adapter: $($adapter.Name)"
        return [pscustomobject]@{ IP = ""; Adapter = $adapter.Name; InterfaceIndex = $adapter.ifIndex; InterfaceDescription = $adapter.InterfaceDescription; MacAddress = $adapter.MacAddress; Gateway = "" }
    }

    $gatewayText = ""
    if ($config.IPv4DefaultGateway) {
        $gatewayText = @($config.IPv4DefaultGateway | Select-Object -ExpandProperty NextHop)[0]
    }
    return [pscustomobject]@{ IP = $addresses[0]; Adapter = $adapter.Name; InterfaceIndex = $adapter.ifIndex; InterfaceDescription = $adapter.InterfaceDescription; MacAddress = $adapter.MacAddress; Gateway = $gatewayText }
}

function Get-NetworkPreflight {
    $defaultRoutes = @()
    foreach ($route in @(Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue | Sort-Object RouteMetric, InterfaceIndex)) {
        $adapter = Get-NetAdapter -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue
        $ipConfig = Get-NetIPConfiguration -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue
        $ipv4 = ""
        if ($ipConfig -and $ipConfig.IPv4Address) {
            $ipv4 = @($ipConfig.IPv4Address | Where-Object { $_.IPAddress } | Select-Object -ExpandProperty IPAddress)[0]
        }
        $name = if ($adapter) { $adapter.Name } else { "" }
        $description = if ($adapter) { $adapter.InterfaceDescription } else { "" }
        $text = "$name $description"
        $vpnLike = ($text -match "(?i)vpn|wireguard|wintun|tap|tun|openvpn|surfshark|nord|tailscale|zerotier")
        $defaultRoutes += [pscustomobject]@{
            InterfaceIndex = $route.InterfaceIndex
            InterfaceAlias = $route.InterfaceAlias
            Adapter = $name
            Description = $description
            Status = if ($adapter) { $adapter.Status } else { "" }
            NextHop = $route.NextHop
            RouteMetric = $route.RouteMetric
            InterfaceMetric = $route.InterfaceMetric
            IPv4 = $ipv4
            VpnLike = [bool]$vpnLike
        }
    }
    $primary = @($defaultRoutes | Sort-Object RouteMetric, InterfaceMetric, InterfaceIndex | Select-Object -First 1)
    $vpnRoutes = @($defaultRoutes | Where-Object { $_.VpnLike -and $_.Status -eq "Up" })
    return [pscustomobject]@{
        VpnDefaultRouteActive = ($vpnRoutes.Count -gt 0)
        VpnRouteCount = $vpnRoutes.Count
        PrimaryDefaultRoute = if ($primary.Count -gt 0) { $primary[0] } else { $null }
        DefaultRoutes = $defaultRoutes
    }
}

function Assert-NetBindPreflight {
    if (-not $UseNetBind -or $AllowVpnDefaultRoute) {
        return
    }
    $preflight = Get-NetworkPreflight
    if ($preflight.VpnDefaultRouteActive) {
        $primary = $preflight.PrimaryDefaultRoute
        $adapter = if ($primary) { $primary.Adapter } else { "" }
        $description = if ($primary) { $primary.Description } else { "" }
        $message = "blocked netbind start: VPN default route active adapter=$adapter description=$description; turn off VPN before starting StarCG"
        Write-LauncherLog $message
        throw $message
    }
}

function Get-StarCGCimProcesses {
    $cimByPid = @{}
    foreach ($item in @(Get-CimInstance Win32_Process -Filter "Name='StarCG.exe'" -ErrorAction SilentlyContinue)) {
        $cimByPid[[int]$item.ProcessId] = $item
    }

    @(Get-Process -Name "StarCG" -ErrorAction SilentlyContinue | ForEach-Object {
        $exe = $_.Path
        if ([string]::IsNullOrWhiteSpace($exe) -and $cimByPid.ContainsKey([int]$_.Id)) {
            $exe = $cimByPid[[int]$_.Id].ExecutablePath
        }
        [pscustomobject]@{
            ProcessId = $_.Id
            ExecutablePath = $exe
            Name = "StarCG.exe"
        }
    })
}

function Find-NewStarCGProcess {
    param(
        [int[]]$BeforePids,
        [int]$TimeoutSeconds = 12
    )

    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSeconds))
    $sourceExe = (Get-SlotExePath 1)
    do {
        $items = @(Get-StarCGCimProcesses | Where-Object {
            ($BeforePids -notcontains [int]$_.ProcessId) -and
            ([string]::IsNullOrWhiteSpace($_.ExecutablePath) -or $_.ExecutablePath -eq $sourceExe)
        } | Sort-Object ProcessId)
        if ($items.Count -gt 0) {
            return [int]$items[0].ProcessId
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    return $null
}

function Register-StartedSlotProcess {
    param(
        [int]$Slot,
        [int[]]$BeforePids,
        [object[]]$LaunchOutput,
        [string]$WindowsUser = "",
        [int]$TimeoutSeconds = 12
    )

    $startedProcessId = $null
    foreach ($line in @($LaunchOutput)) {
        $text = [string]$line
        if ($text -match "started pid=(\d+)") {
            $startedProcessId = [int]$Matches[1]
            break
        }
    }

    if (-not $startedProcessId) {
        $startedProcessId = Find-NewStarCGProcess -BeforePids $BeforePids -TimeoutSeconds $TimeoutSeconds
    }

    if ($startedProcessId) {
        Register-SlotProcess -Slot $Slot -ProcessId $startedProcessId -WindowsUser $WindowsUser
    } else {
        Write-LauncherLog "warning: could not map started slot $Slot to a StarCG pid"
    }
}

function Test-ProcessLooksLikeSlot {
    param(
        [object]$ProcessItem,
        [int]$Slot
    )

    $mappedPid = Get-MappedSlotPid $Slot
    if ($mappedPid -and [int]$ProcessItem.ProcessId -eq [int]$mappedPid) {
        return $true
    }

    $process = Get-Process -Id $ProcessItem.ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return $false
    }

    $title = $process.MainWindowTitle
    if ([string]::IsNullOrWhiteSpace($title)) {
        return $false
    }

    $slot2 = "{0:D2}" -f $Slot
    $slot3 = "{0:D3}" -f $Slot
    if ($title -match ("^\[{0}\](?:\s|$)" -f [regex]::Escape($slot2))) {
        return $true
    }
    if ($title -match ("^SCG{0}\b" -f [regex]::Escape($slot3))) {
        return $true
    }
    if ($title -match ("^{0}\b" -f [regex]::Escape($slot3))) {
        return $true
    }

    return $false
}

function Get-SlotCimProcesses {
    param([int]$Slot)
    $mappedPid = Get-MappedSlotPid $Slot
    $all = @(Get-StarCGCimProcesses)
    if ($mappedPid) {
        return @($all | Where-Object { [int]$_.ProcessId -eq [int]$mappedPid })
    }
    return @($all | Where-Object { Test-ProcessLooksLikeSlot $_ $Slot })
}

function Stop-SlotNetBindLaunchers {
    param([int]$Slot)

    $product = Get-SlotProductName $Slot
    $slotDataPath = Get-SlotPersistentDataPath $Slot
    $slotMutablePath = Get-SlotMutableGameRoot $Slot
    $matches = @(Get-CimInstance Win32_Process -Filter "Name='GuiTestNetBindLauncher.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $commandLine = [string]$_.CommandLine
            $commandLine -like "*$product*" -or
            $commandLine -like "*$slotDataPath*" -or
            $commandLine -like "*$slotMutablePath*"
        })

    foreach ($item in $matches) {
        Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue
        Write-LauncherLog "stopped slot $Slot netbind launcher pid=$($item.ProcessId)"
    }
}

function Get-SlotStatus {
    $all = @(Get-StarCGCimProcesses)
    for ($slot = 1; $slot -le $Slots; $slot++) {
        $exe = Get-SlotExePath $slot
        $mappedPid = Get-MappedSlotPid $slot
        if ($mappedPid) {
            $items = @($all | Where-Object { [int]$_.ProcessId -eq [int]$mappedPid })
        } else {
            $items = @($all | Where-Object { Test-ProcessLooksLikeSlot $_ $slot })
        }
        $product = "SCG{0:D3}" -f $slot

        if ($items.Count -eq 0) {
            [pscustomobject]@{
                Slot = $slot
                Status = "NotRunning"
                Responding = $false
                Pids = ""
                Title = ""
                Product = $product
                LoginData = Get-SlotLoginDataStatus $slot
                Exe = $exe
            }
            continue
        }

        $pidList = @()
        $titles = @()
        $respondingValues = @()
        $hasWindow = $false
        foreach ($item in $items) {
            $pidList += $item.ProcessId
            $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
            if ($process) {
                $respondingValues += [bool]$process.Responding
                if ($process.MainWindowHandle -ne 0) {
                    $hasWindow = $true
                }
                if ($process.MainWindowTitle) {
                    $titles += $process.MainWindowTitle
                }
            }
        }

        $responding = ($respondingValues.Count -gt 0 -and -not ($respondingValues -contains $false))
        $status = "Running"
        if ($items.Count -gt 1) {
            $status = "Multiple"
        } elseif (-not $hasWindow) {
            $status = "Starting"
        } elseif (-not $responding) {
            $status = "NotResponding"
        }

        [pscustomobject]@{
            Slot = $slot
            Status = $status
            Responding = $responding
            Pids = ($pidList -join ",")
            Title = (($titles | Select-Object -Unique) -join " | ")
            Product = $product
            LoginData = Get-SlotLoginDataStatus $slot
            Exe = $exe
        }
    }
}

try {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class StarCGTitleApi {
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool SetWindowText(IntPtr hWnd, string lpString);
}
"@ -ErrorAction Stop
} catch {
    if ($_.Exception.Message -notlike "*already exists*") {
        throw
    }
}

function Set-SlotWindowTitle {
    param([int]$Slot)
    foreach ($item in (Get-SlotCimProcesses $Slot)) {
        $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
        if (-not $process -or $process.MainWindowHandle -eq 0) {
            continue
        }
        $newTitle = "[{0:D2}]" -f $Slot
        [StarCGTitleApi]::SetWindowText($process.MainWindowHandle, $newTitle) | Out-Null
    }
}

function Wait-SlotWindowReady {
    param(
        [int]$Slot,
        [int]$TimeoutSeconds = 20
    )

    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSeconds))
    do {
        foreach ($item in (Get-SlotCimProcesses $Slot)) {
            $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
            if ($process -and $process.MainWindowHandle -ne 0) {
                Write-LauncherLog "slot $Slot window ready pid=$($item.ProcessId)"
                return $true
            }
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    Write-LauncherLog "warning: slot $Slot window not ready after $TimeoutSeconds seconds"
    return $false
}

function Set-AllWindowTitles {
    foreach ($slot in 1..$Slots) {
        Set-SlotWindowTitle $slot
    }
}

function Assert-SlotsReady {
    $exePath = Join-Path $Source "StarCG.exe"
    $dataPath = Join-Path $Source "StarCG_Data"
    if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
        throw "Missing source executable: $exePath"
    }
    if (-not (Test-Path -LiteralPath $dataPath -PathType Container)) {
        throw "Missing source StarCG_Data: $dataPath"
    }
    Write-LauncherLog "single-source slots ready source=$Source targetRootIgnored=$TargetRoot"
}

function Stop-OldBypass {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessId -ne $selfPid -and
            ($_.CommandLine -like "*bypass_admin_v2.ps1*" -or $_.CommandLine -like "*bypass_admin_stable.ps1*") -and
            $_.CommandLine -notlike "*Get-CimInstance*"
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-LauncherLog "stopped old bypass pid=$($_.ProcessId)"
        }
}

function Write-BypassConfig {
    param(
        [string]$ConfigPath,
        [string]$GamePath,
        [string]$HandlePath
    )
    $lines = @("GAME_PATH=$GamePath", "HANDLE_PATH=$HandlePath")
    [System.IO.File]::WriteAllLines($ConfigPath, $lines, [System.Text.Encoding]::UTF8)
}

function Start-Bypass {
    $bypassScript = Join-Path $BypassDir "bypass_admin_stable.ps1"
    if (-not (Test-Path -LiteralPath $bypassScript)) {
        throw "Missing bypass script: $bypassScript"
    }

    $handlePath = Find-Handle64
    if (-not $handlePath) {
        throw "handle64.exe not found."
    }

    New-Item -ItemType Directory -Force -Path $BypassDir | Out-Null
    $configPath = Join-Path $BypassDir "config.txt"
    $bypassLog = Join-Path $BypassDir "bypass_stable.log"
    $outPath = Join-Path $BypassDir "bypass_stable_stdout.log"
    $errPath = Join-Path $BypassDir "bypass_stable_stderr.log"

    Write-BypassConfig -ConfigPath $configPath -GamePath (Get-SlotExePath 1) -HandlePath $handlePath
    Remove-Item -LiteralPath $bypassLog, $outPath, $errPath -ErrorAction SilentlyContinue

    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$bypassScript`" -ConfigPath `"$configPath`" -LogPath `"$bypassLog`""
    $process = Start-Process -FilePath "powershell.exe" `
        -ArgumentList $arguments `
        -WorkingDirectory (Split-Path -Parent $bypassScript) `
        -WindowStyle Hidden `
        -RedirectStandardOutput $outPath `
        -RedirectStandardError $errPath `
        -PassThru

    Write-LauncherLog "started bypass pid=$($process.Id)"
    Start-Sleep -Seconds 6
}

function Start-LoginDataWatcher {
    param([int]$Slot)

    $scriptPath = $PSCommandPath
    if ([string]::IsNullOrWhiteSpace($scriptPath)) {
        $scriptPath = $MyInvocation.MyCommand.Path
    }

    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $scriptPath,
        "-Action",
        "watch-login",
        "-SlotList",
        ([string]$Slot),
        "-Source",
        $Source,
        "-TargetRoot",
        $TargetRoot,
        "-BypassDir",
        $BypassDir,
        "-Slots",
        ([string]$Slots),
        "-LogPath",
        $LogPath,
        "-AccountSlotRoot",
        $AccountSlotRoot,
        "-WatchSeconds",
        ([string]$WatchSeconds),
        "-WatchIntervalSeconds",
        ([string]$WatchIntervalSeconds)
    )

    $argumentLine = ($arguments | ForEach-Object { Quote-ProcessArgument $_ }) -join " "
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList $argumentLine `
        -WorkingDirectory $PSScriptRoot `
        -WindowStyle Hidden | Out-Null

    Write-LauncherLog "started login watcher slot $Slot"
}

function Watch-SlotLoginData {
    param([int]$Slot)

    $mutexName = "Global\StarCGLoginWatchSCG{0:D3}" -f $Slot
    $mutex = New-Object System.Threading.Mutex($false, $mutexName)
    $lockTaken = $false
    try {
        try {
            $lockTaken = $mutex.WaitOne(0)
        } catch [System.Threading.AbandonedMutexException] {
            $lockTaken = $true
        }

        if (-not $lockTaken) {
            Write-LauncherLog "login watcher already running slot $Slot"
            return
        }

        $interval = [Math]::Max(1, $WatchIntervalSeconds)
        $watchLimit = [Math]::Max(30, $WatchSeconds)
        $startupGrace = [Math]::Min(120, $watchLimit)
        $startedAt = Get-Date
        $deadline = $startedAt.AddSeconds($watchLimit)
        $startupDeadline = $startedAt.AddSeconds($startupGrace)
        $seenProcess = $false

        Write-LauncherLog "login watcher active slot $Slot seconds=$watchLimit interval=$interval mode=save-on-exit"
        while ((Get-Date) -lt $deadline) {
            try {
                $items = @(Get-SlotCimProcesses $Slot)
            } catch {
                $items = @()
                Write-LauncherLog "login watcher process check failed slot $Slot error=$($_.Exception.Message)"
            }

            if ($items.Count -gt 0) {
                $seenProcess = $true
            }

            if ($seenProcess -and $items.Count -eq 0) {
                break
            }
            if (-not $seenProcess -and (Get-Date) -ge $startupDeadline) {
                Write-LauncherLog "login watcher ended slot $Slot reason=no-process-seen"
                return
            }

            Start-Sleep -Seconds $interval
        }

        Start-Sleep -Seconds 1
        try {
            Save-SlotLoginData -Slot $Slot | Out-Null
        } catch {
            Write-LauncherLog "login watcher final save failed slot $Slot error=$($_.Exception.Message)"
        }
        Write-LauncherLog "login watcher ended slot $Slot"
    } finally {
        if ($lockTaken) {
            $mutex.ReleaseMutex() | Out-Null
        }
        if ($mutex) {
            $mutex.Dispose()
        }
    }
}

function Start-Slot {
    param([int]$Slot)
    $slotPath = Get-SlotPath $Slot
    $exePath = Get-SlotExePath $Slot
    if (-not (Test-Path -LiteralPath $exePath)) {
        throw "Missing slot executable: $exePath"
    }

    $bind = Get-ForceBindSettings $Slot
    if ($UseNetBind -and -not $NoForceBindIP -and (-not $bind -or $bind.Mode -ne "netbind")) {
        $message = "blocked slot $Slot start: NetBind group could not resolve to an active IPv4 adapter"
        Write-LauncherLog $message
        throw $message
    }
    $windowsUserLabel = ""
    if ($UseWindowsUsers) {
        $windowsUserLabel = Get-SlotWindowsUserLabel $Slot
        Write-LauncherLog "slot $Slot using Windows user profile isolation user=$windowsUserLabel"
    }
    if ($bind -and $bind.Mode -eq "netbind") {
        Write-LauncherLog "slot $Slot using GUI_TEST_PC netbind ip binding"
    } else {
        if ($UseWindowsUsers) {
            Write-LauncherLog "warning: slot $Slot Windows user isolation is active but IP binding is disabled or legacy"
        } else {
            Write-LauncherLog "warning: slot $Slot single-source mode without netbind does not isolate registry/profile data"
        }
    }

    if (-not $UseWindowsUsers) {
        Restore-SlotLoginData -Slot $Slot
    }
    Initialize-SlotMutableGameData -Slot $Slot

    $beforePids = @(Get-StarCGCimProcesses | Select-Object -ExpandProperty ProcessId)
    if ($bind) {
        $groupText = if ($bind.Group) { " group=$($bind.Group)" } else { "" }
        $adapterText = if ($bind.Adapter) { " adapter=$($bind.Adapter)" } else { "" }
        if ($bind.Mode -eq "netbind") {
            $netBindArgs = @(
                "--bind-ip",
                $bind.IP,
                "--cwd",
                $slotPath,
                "--log",
                (Get-DefaultNetBindLogPath)
            )
            foreach ($pair in (Get-SlotFileRedirectPairs $Slot)) {
                $netBindArgs += @(
                    "--redirect-pair",
                    $pair.From,
                    $pair.To
                )
            }
            $netBindArgs += @(
                "--",
                $exePath
            )
            if ($UseWindowsUsers) {
                $netBindProcess = Start-SlotProcess -Slot $Slot -FilePath $bind.Path -Arguments $netBindArgs -WorkingDirectory $slotPath -HiddenWindow
                Register-StartedSlotProcess -Slot $Slot -BeforePids $beforePids -LaunchOutput @() -WindowsUser $windowsUserLabel -TimeoutSeconds 30
                if ($netBindProcess) {
                    Write-LauncherLog "started slot $Slot netbind launcher pid=$($netBindProcess.Id) as $windowsUserLabel"
                }
            } else {
                $launchOutput = @(& $bind.Path @netBindArgs 2>&1)
                $launchExitCode = $LASTEXITCODE
                if ($launchExitCode -ne 0) {
                    throw "NetBind launcher failed slot $Slot rc=$launchExitCode output=$($launchOutput -join ' | ')"
                }
                Register-StartedSlotProcess -Slot $Slot -BeforePids $beforePids -LaunchOutput $launchOutput
            }
            $slotProduct = Get-SlotProductName $Slot
            Write-LauncherLog "slot $Slot registry redirect: Software\CrossGate\StarCG -> Software\CrossGate\$slotProduct"
            Write-LauncherLog "started slot $Slot with GUI_TEST_PC netbind ip=$($bind.IP)$groupText$adapterText source=$exePath profile=StarCG->$((Get-SlotProductName $Slot)) windowsUser=$windowsUserLabel redirects=$(@(Get-SlotFileRedirectPairs $Slot).Count)"
            if (-not $UseWindowsUsers) {
                Start-LoginDataWatcher -Slot $Slot
            }
        } else {
            $prefix = @()
            $config = Read-KeyValueFile $ForceBindConfig
            $useDelayedInjection = $config["USE_DELAYED_INJECTION"]
            if ($useDelayedInjection -match "^(1|true|yes)$") {
                $prefix += "-i"
            }
            $prefix += $bind.IP
            $prefix += $exePath
            Start-SlotProcess -Slot $Slot -FilePath $bind.Path -Arguments $prefix -WorkingDirectory $slotPath -HiddenWindow | Out-Null
            Register-StartedSlotProcess -Slot $Slot -BeforePids $beforePids -LaunchOutput @() -WindowsUser $windowsUserLabel -TimeoutSeconds 30
            Write-LauncherLog "started slot $Slot with ForceBindIP ip=$($bind.IP)$groupText$adapterText windowsUser=$windowsUserLabel"
            if (-not $UseWindowsUsers) {
                Start-LoginDataWatcher -Slot $Slot
            }
        }
    } else {
        $process = Start-SlotProcess -Slot $Slot -FilePath $exePath -WorkingDirectory $slotPath
        if ($process) {
            Register-SlotProcess -Slot $Slot -ProcessId $process.Id -WindowsUser $windowsUserLabel
        }
        Write-LauncherLog "started slot $Slot without ForceBindIP windowsUser=$windowsUserLabel"
        if (-not $UseWindowsUsers) {
            Start-LoginDataWatcher -Slot $Slot
        }
    }
}

function Stop-Slot {
    param([int]$Slot)
    if (-not $UseWindowsUsers) {
        Save-SlotLoginData -Slot $Slot | Out-Null
    }

    $items = @(Get-SlotCimProcesses $Slot)
    foreach ($item in $items) {
        $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
        if ($process -and $process.MainWindowHandle -ne 0) {
            $process.CloseMainWindow() | Out-Null
        }
    }

    if ($items.Count -gt 0) {
        Start-Sleep -Seconds 3
        if (-not $UseWindowsUsers) {
            Save-SlotLoginData -Slot $Slot | Out-Null
        }
    }

    foreach ($item in $items) {
        $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue
            Write-LauncherLog "stopped slot $Slot pid=$($item.ProcessId)"
        } else {
            Write-LauncherLog "closed slot $Slot pid=$($item.ProcessId)"
        }
    }
    Stop-SlotNetBindLaunchers -Slot $Slot
    Unregister-SlotProcess -Slot $Slot
}

function Stop-SlotsBatch {
    param([int[]]$SlotNumbers)

    $requested = @($SlotNumbers | ForEach-Object { [int]$_ } | Sort-Object -Unique)
    $itemsBySlot = @{}
    $hasProcesses = $false

    foreach ($slot in $requested) {
        if (-not $UseWindowsUsers) {
            Save-SlotLoginData -Slot $slot | Out-Null
        }
        $items = @(Get-SlotCimProcesses $slot)
        $itemsBySlot[[string]$slot] = $items
        foreach ($item in $items) {
            $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
            if ($process) {
                $hasProcesses = $true
                if ($process.MainWindowHandle -ne 0) {
                    $process.CloseMainWindow() | Out-Null
                }
            }
        }
    }

    if ($hasProcesses) {
        Write-LauncherLog "batch close requested slots=$($requested -join ',')"
        Start-Sleep -Seconds 3
    }

    $handledPids = @{}
    foreach ($slot in $requested) {
        if ($hasProcesses -and -not $UseWindowsUsers) {
            Save-SlotLoginData -Slot $slot | Out-Null
        }
        foreach ($item in @($itemsBySlot[[string]$slot])) {
            $pidKey = [string]$item.ProcessId
            if ($handledPids.ContainsKey($pidKey)) {
                continue
            }
            $handledPids[$pidKey] = $true
            $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
            if ($process) {
                Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue
                Write-LauncherLog "batch stopped slot $slot pid=$($item.ProcessId)"
            } else {
                Write-LauncherLog "batch closed slot $slot pid=$($item.ProcessId)"
            }
        }
        Stop-SlotNetBindLaunchers -Slot $slot
        Unregister-SlotProcess -Slot $slot
    }
}

function Start-SlotsWithBypass {
    param(
        [int[]]$SlotNumbers,
        [switch]$Restart
    )

    Assert-SlotsReady
    Assert-NetBindPreflight
    if ($UseNetBind -and -not $NoForceBindIP) {
        $unresolvedSlots = @(
            foreach ($slot in $SlotNumbers) {
                $bind = Get-ForceBindSettings $slot
                if (-not $bind -or $bind.Mode -ne "netbind") {
                    [int]$slot
                }
            }
        )
        if ($unresolvedSlots.Count -gt 0) {
            $message = "blocked netbind batch start: no active adapter/IP for slots=$($unresolvedSlots -join ',')"
            Write-LauncherLog $message
            throw $message
        }
    }
    Stop-OldBypass

    try {
        Start-Bypass
        $startedSlots = New-Object "System.Collections.Generic.List[int]"
        $failedSlots = New-Object "System.Collections.Generic.List[string]"
        foreach ($slot in $SlotNumbers) {
            if ($Restart) {
                Stop-Slot $slot
                Start-Sleep -Seconds 2
            } elseif (@(Get-SlotCimProcesses $slot).Count -gt 0) {
                Write-LauncherLog "slot $slot already running; skipped"
                continue
            }

            try {
                Start-Slot $slot
                Wait-SlotWindowReady -Slot $slot -TimeoutSeconds ([Math]::Max(10, $DelaySeconds * 2)) | Out-Null
                Set-SlotWindowTitle $slot
                $startedSlots.Add([int]$slot) | Out-Null
                Start-Sleep -Seconds $DelaySeconds
            } catch {
                $failure = "slot $slot start failed: $($_.Exception.Message)"
                $failedSlots.Add($failure) | Out-Null
                Write-LauncherLog $failure
                Start-Sleep -Seconds 1
            }
        }

        if ($startedSlots.Count -gt 0) {
            $layoutReady = $false
            foreach ($attempt in 1..3) {
                try {
                    Ensure-RunningWindowLayout
                    $layoutReady = $true
                    break
                } catch {
                    Write-LauncherLog "batch final layout attempt $attempt/3 failed: $($_.Exception.Message)"
                    if ($attempt -lt 3) {
                        Start-Sleep -Seconds 3
                    }
                }
            }
            if (-not $layoutReady) {
                Write-LauncherLog "warning: all possible slots were launched but final window layout is not ready"
            }
        }

        if ($FinalBypassSeconds -gt 0) {
            Write-LauncherLog "keeping bypass active for $FinalBypassSeconds seconds"
            Start-Sleep -Seconds $FinalBypassSeconds
        }
        if ($failedSlots.Count -gt 0) {
            throw ("one or more slots failed to start: " + ($failedSlots -join " | "))
        }
    } finally {
        Stop-OldBypass
        Set-AllWindowTitles
    }
}

function Ensure-RunningWindowLayout {
    if (-not (Test-Path -LiteralPath $windowLayoutScript -PathType Leaf)) {
        throw "Window layout script not found: $windowLayoutScript"
    }
    if (-not (Test-Path -LiteralPath $windowLayoutConfig -PathType Leaf)) {
        throw "Window layout config not found: $windowLayoutConfig"
    }

    $runningSlots = @(
        Get-SlotStatus |
            Where-Object { $_.Status -eq "Running" } |
            Select-Object -ExpandProperty Slot |
            Sort-Object -Unique
    )
    if ($runningSlots.Count -lt 1) {
        return
    }

    $slotText = $runningSlots -join ","
    $layoutOutput = @(
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $windowLayoutScript `
            -Action ensure `
            -SlotList $slotText `
            -SlotPidMapPath (Get-SlotPidMapPath) `
            -ConfigPath $windowLayoutConfig `
            -Json 2>&1
    )
    $layoutExitCode = $LASTEXITCODE
    $layoutResult = $null
    for ($index = $layoutOutput.Count - 1; $index -ge 0; $index--) {
        $line = $layoutOutput[$index]
        try {
            $candidate = [string]$line | ConvertFrom-Json -ErrorAction Stop
            if ($candidate) {
                $layoutResult = $candidate
                break
            }
        } catch {
        }
    }

    if ($layoutExitCode -ne 0 -or -not $layoutResult -or -not $layoutResult.ready) {
        $reason = if ($layoutResult -and $layoutResult.error) {
            [string]$layoutResult.error
        } else {
            ($layoutOutput -join " | ")
        }
        throw "Fixed stacked 720p window layout failed for slots ${slotText}: $reason"
    }

    Write-LauncherLog "fixed stacked 720p window layout ready slots=$slotText moved=$($layoutResult.moved_slots -join ',')"
}

try {
    Enter-ControlLock

    if (-not (Test-Admin) -and $Action -notin @("status", "relabel", "watch-login")) {
        Write-LauncherLog "warning: action '$Action' is not elevated"
    }

    $selectedSlots = @(Parse-SlotList $SlotList)

    switch ($Action) {
        "prepare" {
            Assert-SlotsReady
            Restore-AllSlotLoginData
            Set-AllWindowTitles
        }
        "start" {
            Start-SlotsWithBypass -SlotNumbers $selectedSlots
        }
        "restart" {
            Start-SlotsWithBypass -SlotNumbers $selectedSlots -Restart
        }
        "stop" {
            Stop-SlotsBatch -SlotNumbers $selectedSlots
        }
        "snapshot-login" {
            foreach ($slot in $selectedSlots) {
                Save-SlotLoginData -Slot $slot | Out-Null
            }
        }
        "restore-login" {
            foreach ($slot in $selectedSlots) {
                Restore-SlotLoginData -Slot $slot
            }
        }
        "watch-login" {
            foreach ($slot in $selectedSlots) {
                Watch-SlotLoginData -Slot $slot
            }
            return
        }
        "start-missing" {
            $missing = @(
                Get-SlotStatus |
                    Where-Object {
                        $_.Status -ne "Running" -and
                        $selectedSlots -contains ([int]$_.Slot)
                    } |
                    Select-Object -ExpandProperty Slot |
                    ForEach-Object { [int]$_ } |
                    Sort-Object -Unique
            )
            if ($missing.Count -gt 0) {
                Write-LauncherLog "start-missing repairing slots=$($missing -join ',')"
                Stop-SlotsBatch -SlotNumbers $missing
                Start-SlotsWithBypass -SlotNumbers $missing
            } else {
                Write-LauncherLog "no missing slots"
            }
        }
        "repair-bad" {
            $bad = @(
                Get-SlotStatus |
                    Where-Object {
                        $_.Status -in @("NotRunning", "NotResponding", "Multiple") -and
                        $selectedSlots -contains ([int]$_.Slot)
                    } |
                    Select-Object -ExpandProperty Slot
            )
            if ($bad.Count -gt 0) {
                Start-SlotsWithBypass -SlotNumbers $bad -Restart
            } else {
                Write-LauncherLog "no bad slots"
            }
        }
        "relabel" {
            Set-AllWindowTitles
        }
        "bind-test" {
            $preflight = Get-NetworkPreflight
            $bindResults = foreach ($slot in $selectedSlots) {
                $bind = Get-ForceBindSettings $slot
                if ($bind) {
                    [pscustomobject]@{
                        Slot = $slot
                        Binder = $bind.Mode
                        BinderPath = $bind.Path
                        ForceBindIP = if ($bind.Mode -eq "forcebindip") { $bind.Path } else { "" }
                        NetBind = if ($bind.Mode -eq "netbind") { $bind.Path } else { "" }
                        Group = $bind.Group
                        Adapter = $bind.Adapter
                        InterfaceIndex = $bind.InterfaceIndex
                        Description = $bind.InterfaceDescription
                        MacAddress = $bind.MacAddress
                        Gateway = $bind.Gateway
                        ResolvedIP = $bind.IP
                    }
                } else {
                    [pscustomobject]@{
                        Slot = $slot
                        Binder = ""
                        BinderPath = ""
                        ForceBindIP = ""
                        NetBind = ""
                        Group = ""
                        Adapter = ""
                        InterfaceIndex = ""
                        Description = ""
                        MacAddress = ""
                        Gateway = ""
                        ResolvedIP = ""
                    }
                }
            }
            if ($Json) {
                [pscustomobject]@{
                    Preflight = $preflight
                    Slots = $bindResults
                } | ConvertTo-Json -Depth 6
            } else {
                $preflight
                $bindResults
            }
            return
        }
        "status" {
        }
    }

    $status = @(Get-SlotStatus)
    if ($Json) {
        $status | ConvertTo-Json -Depth 4
    } else {
        $status | Format-Table -AutoSize
    }
} finally {
    Exit-ControlLock
}

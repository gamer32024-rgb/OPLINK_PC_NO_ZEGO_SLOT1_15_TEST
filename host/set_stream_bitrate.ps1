[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(250, 20000)]
    [int]$BitrateKbps,
    [ValidateRange(10, 120)]
    [int]$WaitSeconds = 60
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$ProfilePath = Join-Path $Root "stream_profile.json"
$ApiHealthUrl = "http://127.0.0.1:5112/api/v1/health"
$profile = [ordered]@{ bitrate_kbps = $BitrateKbps }
$json = $profile | ConvertTo-Json
$temporaryPath = "$ProfilePath.tmp"
$hadPreviousProfile = Test-Path -LiteralPath $ProfilePath -PathType Leaf
$previousProfileContent = if ($hadPreviousProfile) {
    Get-Content -LiteralPath $ProfilePath -Raw -Encoding UTF8
} else {
    $null
}
$previousBitrateKbps = 3000
if ($hadPreviousProfile) {
    try {
        $previousBitrateKbps = [int](ConvertFrom-Json $previousProfileContent).bitrate_kbps
        if ($previousBitrateKbps -lt 250 -or $previousBitrateKbps -gt 20000) {
            throw "previous bitrate_kbps is out of range"
        }
    } catch {
        throw "Existing stream profile is invalid; no change was made: $($_.Exception.Message)"
    }
}

function Write-ProfileContent {
    param([string]$Content)
    [System.IO.File]::WriteAllText(
        $temporaryPath,
        $Content.TrimEnd("`r", "`n") + "`n",
        [System.Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporaryPath -Destination $ProfilePath -Force
}

function Wait-ForStreamBitrate {
    param([int]$ExpectedBitrateKbps, [datetime]$Deadline)
    do {
        Start-Sleep -Seconds 1
        try {
            $health = Invoke-RestMethod -Uri $ApiHealthUrl -TimeoutSec 2
            if ([bool]$health.ok -and [int]$health.profile.bitrate_kbps -eq $ExpectedBitrateKbps) {
                return $true
            }
        } catch {
            # The API is briefly unavailable while the watchdog replaces the stream host.
        }
    } while ((Get-Date) -lt $Deadline)
    return $false
}

Write-ProfileContent -Content $json
Write-Host "Requested OPLINK stream bitrate: $BitrateKbps kbps."
Write-Host "The watchdog will restart only the stream host; the IPA does not change."

$deadline = (Get-Date).AddSeconds($WaitSeconds)
if (Wait-ForStreamBitrate -ExpectedBitrateKbps $BitrateKbps -Deadline $deadline) {
    Write-Host "OPLINK stream is ready at $BitrateKbps kbps."
    exit 0
}

if ($hadPreviousProfile) {
    Write-ProfileContent -Content $previousProfileContent
} else {
    Remove-Item -LiteralPath $ProfilePath -Force -ErrorAction SilentlyContinue
}
Write-Warning "The test profile failed; restored $previousBitrateKbps kbps and requested watchdog recovery."
$rollbackReady = Wait-ForStreamBitrate `
    -ExpectedBitrateKbps $previousBitrateKbps `
    -Deadline (Get-Date).AddSeconds($WaitSeconds)
if (!$rollbackReady) {
    Write-Warning "The previous profile was restored, but the stream host has not reported ready yet."
}
throw "OPLINK stream did not become ready at $BitrateKbps kbps within $WaitSeconds seconds; the previous profile was restored."

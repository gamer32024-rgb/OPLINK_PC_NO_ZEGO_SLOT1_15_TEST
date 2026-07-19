[CmdletBinding()]
param([switch]$IgnoreMissing)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$StatePath = Join-Path $Root "runtime\state.json"
$ActivePublisherPath = Join-Path $Root "runtime\active_publisher.json"

if (!(Test-Path -LiteralPath $StatePath) -and !(Test-Path -LiteralPath $ActivePublisherPath)) {
    if ($IgnoreMissing) { return }
    throw "No active stream state was found."
}

$ids = @()
if (Test-Path -LiteralPath $StatePath) {
    $state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $ids += @($state.pids.mediamtx, $state.pids.api) + @($state.pids.publishers | ForEach-Object { $_.pid })
}
if (Test-Path -LiteralPath $ActivePublisherPath) {
    try {
        $activePublisher = Get-Content -LiteralPath $ActivePublisherPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($activePublisher.publisher_pid) { $ids += [int]$activePublisher.publisher_pid }
        $ids += @($activePublisher.publishers | ForEach-Object { $_.pid })
    } catch {
    }
}
foreach ($id in $ids | Where-Object { $_ } | Sort-Object -Unique) {
    $process = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($process) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
}
Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $ActivePublisherPath -Force -ErrorAction SilentlyContinue
Write-Host "Stopped the OPLINK_PC slots 1-15 test processes."

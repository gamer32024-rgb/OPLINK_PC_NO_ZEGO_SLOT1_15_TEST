[CmdletBinding()]
param([switch]$IgnoreMissing)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$StatePath = Join-Path $Root "runtime\state.json"

if (!(Test-Path -LiteralPath $StatePath)) {
    if ($IgnoreMissing) { return }
    throw "No active state file was found: $StatePath"
}

$state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
$ids = @($state.pids.mediamtx, $state.pids.api) + @($state.pids.publishers | ForEach-Object { $_.pid })
foreach ($id in $ids | Where-Object { $_ } | Sort-Object -Unique) {
    $process = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($process) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
}
Remove-Item -LiteralPath $StatePath -Force
Write-Host "Stopped the OPLINK_PC slots 1-15 test processes."

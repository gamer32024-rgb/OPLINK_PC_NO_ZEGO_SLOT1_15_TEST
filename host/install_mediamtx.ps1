[CmdletBinding()]
param([string]$Version = "1.19.2")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$Tools = Join-Path $Root "tools\mediamtx"
$Archive = Join-Path $Tools "mediamtx.zip"
$Uri = "https://github.com/bluenviron/mediamtx/releases/download/v$Version/mediamtx_v${Version}_windows_amd64.zip"

New-Item -ItemType Directory -Force -Path $Tools | Out-Null
Invoke-WebRequest -Uri $Uri -OutFile $Archive
Expand-Archive -LiteralPath $Archive -DestinationPath $Tools -Force
Remove-Item -LiteralPath $Archive -Force
$Exe = Join-Path $Tools "mediamtx.exe"
if (!(Test-Path -LiteralPath $Exe)) { throw "MediaMTX executable was not found after extraction." }
Write-Host $Exe


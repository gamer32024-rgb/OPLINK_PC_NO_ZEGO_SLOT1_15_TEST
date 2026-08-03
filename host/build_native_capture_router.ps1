[CmdletBinding()]
param(
    [string]$Configuration = "Release",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
$Source = Join-Path $Root "native_capture_router"
$Build = Join-Path $Root "runtime\native_capture_router_build"
$Output = Join-Path $Root "tools\oplink_capture_router"
$Exe = Join-Path $Output "oplink_capture_router.exe"
$VsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"

if (!(Test-Path -LiteralPath $VsWhere -PathType Leaf)) {
    throw "Visual Studio Build Tools were not found."
}
$VsInstall = & $VsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (!$VsInstall) {
    throw "Visual C++ x64 build tools are required."
}

$CMake = (Get-Command cmake.exe -ErrorAction Stop).Source
$VsDevCmd = Join-Path $VsInstall "Common7\Tools\VsDevCmd.bat"
if (!(Test-Path -LiteralPath $VsDevCmd -PathType Leaf)) {
    throw "Visual Studio developer command environment was not found."
}

$BundledNinja = Join-Path $VsInstall "Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"
if (Test-Path -LiteralPath $BundledNinja -PathType Leaf) {
    $Ninja = $BundledNinja
} else {
    $Ninja = (Get-Command ninja.exe -ErrorAction Stop).Source
}

if ($Force -and (Test-Path -LiteralPath $Build)) {
    $ResolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $ResolvedBuild = [IO.Path]::GetFullPath($Build)
    if (!$ResolvedBuild.StartsWith("$ResolvedRoot\runtime\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a build directory outside host\runtime."
    }
    Remove-Item -LiteralPath $Build -Recurse -Force
}
New-Item -ItemType Directory -Path $Build -Force | Out-Null
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$BuildCommand = Join-Path $Build "build_router.cmd"
@"
@echo off
call "$VsDevCmd" -arch=x64 -host_arch=x64
if errorlevel 1 exit /b %errorlevel%
"$CMake" -S "$Source" -B "$Build" -G Ninja -DCMAKE_BUILD_TYPE=$Configuration -DCMAKE_MAKE_PROGRAM="$Ninja"
if errorlevel 1 exit /b %errorlevel%
"$CMake" --build "$Build" --target oplink_capture_router
exit /b %errorlevel%
"@ | Set-Content -LiteralPath $BuildCommand -Encoding ASCII

& $env:ComSpec /d /c "`"$BuildCommand`""
if ($LASTEXITCODE -ne 0) {
    throw "Native capture router configure/build failed."
}

$BuiltExe = Join-Path $Build "oplink_capture_router.exe"
if (!(Test-Path -LiteralPath $BuiltExe -PathType Leaf)) {
    throw "Native capture router output was not found."
}
Copy-Item -LiteralPath $BuiltExe -Destination $Exe -Force
Get-Item -LiteralPath $Exe

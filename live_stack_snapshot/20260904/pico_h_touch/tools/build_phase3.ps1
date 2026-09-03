[CmdletBinding()]
param(
    [string] $Configuration = "Release",
    [string] $BuildDir = "",
    [string] $ArmToolchainBin = "C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\14.2 rel1\bin",
    [string] $PythonExe = ""
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FirmwareDir = Join-Path $Root "firmware"
$SdkDir = (Resolve-Path (Join-Path $Root "third_party\pico-sdk")).Path

if (-not $BuildDir) {
    $BuildDir = Join-Path $Root "b3"
}

$Gcc = Join-Path $ArmToolchainBin "arm-none-eabi-gcc.exe"
if (-not (Test-Path -LiteralPath $Gcc)) {
    throw "ARM GCC not found: $Gcc"
}

$env:Path = "$ArmToolchainBin;$env:Path"
$env:PICO_SDK_PATH = $SdkDir

cmake -S $FirmwareDir -B $BuildDir -G Ninja `
    "-DPICO_BOARD=pico" `
    "-DCMAKE_BUILD_TYPE=$Configuration" `
    "-DPICO_SDK_PATH=$SdkDir" `
    "-DPICO_NO_PICOTOOL=1"

cmake --build $BuildDir --target pico_h_p3 --parallel 1

$Bin = Join-Path $BuildDir "pico_h_phase3_composite_touch.bin"
$Uf2 = Join-Path $BuildDir "pico_h_phase3_composite_touch.uf2"

if (-not (Test-Path -LiteralPath $Bin)) {
    throw "BIN was not produced: $Bin"
}

if (-not $PythonExe) {
    $KnownPython = Join-Path $Root "..\..\star_cros_bot\.venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $KnownPython) {
        $PythonExe = (Resolve-Path -LiteralPath $KnownPython).Path
    } else {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $PythonCommand) {
            throw "Python was not found. Pass -PythonExe or install python.exe in PATH."
        }
        $PythonExe = $PythonCommand.Source
    }
}

& $PythonExe (Join-Path $Root "tools\bin_to_uf2.py") $Bin $Uf2

if (-not (Test-Path -LiteralPath $Uf2)) {
    throw "UF2 was not produced: $Uf2"
}

$Hash = Get-FileHash -LiteralPath $Uf2 -Algorithm SHA256

[pscustomobject]@{
    UF2 = $Uf2
    Bytes = (Get-Item -LiteralPath $Uf2).Length
    SHA256 = $Hash.Hash
}

@echo off
setlocal

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "BUILD_DIR=%ROOT%\build_ninja"
set "VCVARS=C:\BuildTools\VC\Auxiliary\Build\vcvars64.bat"

if not exist "%VCVARS%" (
  echo Missing Visual Studio Build Tools vcvars64.bat: %VCVARS%
  exit /b 1
)

call "%VCVARS%" >nul
cmake -S "%ROOT%" -B "%BUILD_DIR%" -G Ninja || exit /b 1
cmake --build "%BUILD_DIR%" --config Release || exit /b 1

echo Built:
echo   %BUILD_DIR%\GuiTestNetBindLauncher.exe
echo   %BUILD_DIR%\GuiTestNetBindHook64.dll

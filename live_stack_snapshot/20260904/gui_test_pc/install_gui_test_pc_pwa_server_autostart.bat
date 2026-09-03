@echo off
setlocal
set "TASK_NAME=GUI_TEST_PC_PWA_Server"
set "RUN_VALUE=GUI_TEST_PC_PWA_Server"
set "SCRIPT_DIR=%~dp0"
set "VBS_PATH=%SCRIPT_DIR%start_gui_test_pc_pwa_server_hidden.vbs"

if "%~1"=="/?" goto :help
if /I "%~1"=="help" goto :help

if not exist "%VBS_PATH%" (
  echo Missing "%VBS_PATH%"
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$taskName=$env:TASK_NAME; $vbs=$env:VBS_PATH; " ^
  "if (!(Test-Path -LiteralPath $vbs)) { throw ('Missing ' + $vbs) }; " ^
  "$action=New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('\"' + $vbs + '\"'); " ^
  "$trigger=New-ScheduledTaskTrigger -AtLogOn; " ^
  "$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew; " ^
  "Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description 'Start GUI_TEST_PC PWA/API server on port 5100 only.' -Force | Out-Null" 2>nul

if errorlevel 1 (
  echo Scheduled task access was denied. Installing per-user Run entry instead.
  reg.exe add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "%RUN_VALUE%" /t REG_SZ /d "wscript.exe \"%VBS_PATH%\"" /f >nul
  if errorlevel 1 (
    echo Failed to install %TASK_NAME% by either method.
    exit /b 1
  )
  echo Installed %TASK_NAME% as a per-user Run entry.
  echo It starts only the GUI_TEST_PC PWA/API server on port 5100.
  exit /b 0
)

reg.exe delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "%RUN_VALUE%" /f >nul 2>nul

echo Installed %TASK_NAME%.
echo It starts only the GUI_TEST_PC PWA/API server on port 5100.
echo It does not start StarCrosBot, LDPlayer, GUI_TEST, or ngrok.
exit /b 0

:help
echo Install Windows logon autostart for GUI_TEST_PC PWA/API server.
echo Task name: %TASK_NAME%
echo Starts: %VBS_PATH%
echo This does not start StarCrosBot, LDPlayer, GUI_TEST, or ngrok.
exit /b 0

@echo off
setlocal
set TASK_NAME=GUI_TEST_PC_PWA_Server
set RUN_VALUE=GUI_TEST_PC_PWA_Server

schtasks /Query /TN "%TASK_NAME%" /V /FO LIST
if errorlevel 1 (
  echo %TASK_NAME% scheduled task is not installed.
)

reg.exe query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "%RUN_VALUE%"
if errorlevel 1 (
  echo %RUN_VALUE% per-user Run entry is not installed.
)

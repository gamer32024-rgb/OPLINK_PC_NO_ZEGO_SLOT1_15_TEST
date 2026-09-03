@echo off
setlocal
set TASK_NAME=GUI_TEST_PC_PWA_Server
set RUN_VALUE=GUI_TEST_PC_PWA_Server

schtasks /Delete /TN "%TASK_NAME%" /F >nul 2>nul
reg.exe delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "%RUN_VALUE%" /f >nul 2>nul

echo Removed %TASK_NAME% scheduled task and per-user Run entry if present.

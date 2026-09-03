@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
"C:\Users\andyb\Documents\star_cros_bot\.venv\Scripts\python.exe" "%~dp0pc_touch_probe_15.py" --slots 1-15 --taps 10 --diagnose-on-fail %*
echo.
echo Touch probe finished. Log is under logs_pc\touch_probe_15_last.log
pause

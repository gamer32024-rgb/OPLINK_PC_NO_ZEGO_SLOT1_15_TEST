@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set GUI_TEST_PC_HOST=0.0.0.0
set GUI_TEST_PC_PORT=5100
"C:\Users\andyb\Documents\star_cros_bot\.venv\Scripts\python.exe" "%~dp0gui_test_pc_server.py"

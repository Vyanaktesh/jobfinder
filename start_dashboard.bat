@echo off
REM Double-click to open the job dashboard with working Run buttons.
cd /d "%~dp0"
python scripts\control_server.py
pause

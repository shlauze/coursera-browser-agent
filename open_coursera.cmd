@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "coursera_start.py" --login-only
if errorlevel 1 pause

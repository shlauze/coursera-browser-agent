@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 -m v2.cli configure-groq
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -X utf8 -m v2.cli check-groq
pause

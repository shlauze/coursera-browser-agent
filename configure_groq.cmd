@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup_new_pc.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 "configure_new_pc.py" %*
set "setupExitCode=%errorlevel%"
pause
exit /b %setupExitCode%

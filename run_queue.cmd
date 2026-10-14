@echo off
setlocal
cd /d "%~dp0"
set "GROQ_API_KEY="
if not exist ".venv\Scripts\python.exe" (
  echo Run setup_new_pc.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 "course_queue.py" --monitor %*
set "runnerExitCode=%errorlevel%"
echo.
pause
exit /b %runnerExitCode%

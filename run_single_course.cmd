@echo off
setlocal
cd /d "%~dp0"
set "GROQ_API_KEY="
".venv\Scripts\python.exe" -X utf8 "coursera_start.py" --module 1 --through 14 --minutes 180 %*
echo.
pause

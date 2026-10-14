@echo off
setlocal
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js is required in PATH for the offline DOM regressions.
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Project Python environment is missing: .venv\Scripts\python.exe
  exit /b 1
)
".venv\Scripts\python.exe" -m unittest discover -s tests -p "test_*.py" -v
exit /b %errorlevel%

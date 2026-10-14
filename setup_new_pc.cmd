@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 exit /b 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_new_pc.ps1" %*
set "setup_exit_code=%errorlevel%"
echo.
if not "%setup_exit_code%"=="0" echo Setup stopped. See the message above.
pause
exit /b %setup_exit_code%

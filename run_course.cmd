@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "GROQ_API_KEY="
if not exist ".venv\Scripts\python.exe" (
  echo Run setup_new_pc.cmd first.
  pause
  exit /b 1
)
if not exist "logs" mkdir "logs"
set /a restartCount=0
set "resumeFlag="
set "unattended="
for %%A in (%*) do if /I "%%~A"=="--yes" set "unattended=1"

:run
".venv\Scripts\python.exe" -X utf8 -X faulthandler "course_queue.py" --monitor %* %resumeFlag%
set "runnerExitCode=%errorlevel%"
if "%runnerExitCode%"=="0" goto done
if "%runnerExitCode%"=="2" goto done
if "%runnerExitCode%"=="130" goto done
rem Windows console Ctrl+C can also return STATUS_CONTROL_C_EXIT.
if "%runnerExitCode%"=="-1073741510" goto done

set "queueState=UNREADABLE"
for /f "delims=" %%S in ('powershell -NoProfile -NonInteractive -Command "try { $q = ConvertFrom-Json -InputObject (Get-Content -LiteralPath state\queue.json -Raw -ErrorAction Stop) -ErrorAction Stop; if ($q.status -eq 'running') { 'RUNNING' } else { 'STOPPED' } } catch { 'UNREADABLE' }"') do set "queueState=%%S"
if not "%queueState%"=="RUNNING" goto done

echo [%date% %time%] Unexpected exit %runnerExitCode%, queue still running, restart %restartCount%/3>>"logs\runner_supervisor.log"
if %restartCount% GEQ 3 (
  echo The course process stopped unexpectedly after three restarts. See logs\runner_supervisor.log.
  goto done
)
set /a restartCount+=1
set /a restartDelay=restartCount*10
set "resumeFlag=--yes"
echo Course process stopped unexpectedly ^(exit %runnerExitCode%^). Restarting in %restartDelay% seconds after checking saved site progress.
timeout /t %restartDelay% /nobreak >nul
goto run

:done
echo.
if not defined unattended pause
exit /b %runnerExitCode%

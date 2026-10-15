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
set /a transientCount=0
set /a maxProgressItems=0
set /a maxProgressModules=0
set /a maxProgressCertificates=0
set "resumeFlag="
set "unattended="
for %%A in (%*) do if /I "%%~A"=="--yes" set "unattended=1"
rem Retain the highest verified progress seen in this invocation. A retry may
rem rebuild counters from zero; revisiting previously completed items is not
rem new progress and must not reset the consecutive-failure limit.
call :readProgress
if "%progressRead%"=="READY" (
  set /a maxProgressItems=progressItems
  set /a maxProgressModules=progressModules
  set /a maxProgressCertificates=progressCertificates
)

:run
".venv\Scripts\python.exe" -X utf8 -X faulthandler "course_queue.py" --monitor %* %resumeFlag%
set "runnerExitCode=%errorlevel%"
if "%runnerExitCode%"=="0" goto done
if "%runnerExitCode%"=="2" goto done
if "%runnerExitCode%"=="3" goto transient
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

:transient
rem Python uses exit 3 only for a classified read failure. A new run checks
rem saved quiz results before navigating, and keeps the same course selected.
set "queueState=UNREADABLE"
for /f "delims=" %%S in ('powershell -NoProfile -NonInteractive -Command "try { $q = ConvertFrom-Json -InputObject (Get-Content -LiteralPath state\queue.json -Raw -ErrorAction Stop) -ErrorAction Stop; if ($q.status -eq 'attention') { 'ATTENTION' } else { 'STOPPED' } } catch { 'UNREADABLE' }"') do set "queueState=%%S"
if not "%queueState%"=="ATTENTION" goto done
call :readProgress
if not "%progressRead%"=="READY" goto transient_budget
if %progressItems% GTR %maxProgressItems% goto progress_advanced
if %progressModules% GTR %maxProgressModules% goto progress_advanced
if %progressCertificates% GTR %maxProgressCertificates% goto progress_advanced
goto transient_budget

:progress_advanced
set /a transientCount=0
echo [%date% %time%] Verified course progress advanced; transient retry streak reset.>>"logs\runner_supervisor.log"
if %progressItems% GTR %maxProgressItems% set /a maxProgressItems=progressItems
if %progressModules% GTR %maxProgressModules% set /a maxProgressModules=progressModules
if %progressCertificates% GTR %maxProgressCertificates% set /a maxProgressCertificates=progressCertificates

:transient_budget
if %transientCount% GEQ 3 (
  echo Temporary course read still failed after three automatic restarts. See the monitor and incident.json in the latest logs folder.
  set "runnerExitCode=2"
  goto done
)
set /a transientCount+=1
set /a transientDelay=transientCount*30
set "resumeFlag=--yes"
echo [%date% %time%] Recoverable read failure, retry %transientCount%/3 after %transientDelay% seconds>>"logs\runner_supervisor.log"
echo Coursera read failed temporarily. Resuming the same saved course in %transientDelay% seconds.
timeout /t %transientDelay% /nobreak >nul
goto run

:done
echo.
if not defined unattended pause
exit /b %runnerExitCode%

:readProgress
set "progressRead=UNREADABLE"
set /a progressItems=0
set /a progressModules=0
set /a progressCertificates=0
for /f "tokens=1-3" %%I in ('powershell -NoProfile -NonInteractive -Command "try { $q = ConvertFrom-Json -InputObject (Get-Content -LiteralPath state\queue.json -Raw -ErrorAction Stop) -ErrorAction Stop; $items=0; $modules=0; $certs=0; foreach($c in @($q.courses)) { if($null -eq $c) { continue }; $i=[int]$c.items_completed; $m=[int]$c.modules_completed; if($i -gt 0) { $items += $i }; if($m -gt 0) { $modules += $m }; if($c.status -eq 'completed' -and $c.completion_method -eq 'certificate' -and $c.certificate_verified_at -and $c.certificate_page_url) { $certs++ } }; Write-Output ($items.ToString() + ' ' + $modules.ToString() + ' ' + $certs.ToString()) } catch { 'UNREADABLE' }"') do (
  set "progressItems=%%I"
  set "progressModules=%%J"
  set "progressCertificates=%%K"
  set "progressRead=READY"
)
if "%progressItems%"=="UNREADABLE" set "progressRead=UNREADABLE"
exit /b

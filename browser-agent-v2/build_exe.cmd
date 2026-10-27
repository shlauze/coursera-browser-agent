@echo off
setlocal
cd /d "%~dp0"
if not exist ".build-venv\Scripts\python.exe" py -3 -m venv .build-venv
if errorlevel 1 exit /b 1
".build-venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1
".build-venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --console --name CourseraAgent-0.2.89 --collect-all playwright --add-data "v2/assessment_dom.js;v2" --add-data "v2/notebook_bridge.js;v2" windows_launcher.py
if errorlevel 1 exit /b 1
dist\CourseraAgent-0.2.89.exe --self-test
exit /b %errorlevel%

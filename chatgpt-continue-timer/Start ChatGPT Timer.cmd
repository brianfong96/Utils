@echo off
setlocal
cd /d "%~dp0"
if exist "dist\ChatGPTContinueTimer.exe" (
  start "" "dist\ChatGPTContinueTimer.exe"
  exit /b 0
)
where py >nul 2>&1
if errorlevel 1 (
  echo Install Python 3.11 or later from python.org, then double-click this file again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -c "import pywinauto, psutil" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto :failed
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0app.py"
exit /b 0
:failed
echo Setup failed. Review the error above, then try again.
pause
exit /b 1

@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 goto :failed
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name ChatGPTContinueTimer --hidden-import comtypes.stream app.py
if errorlevel 1 goto :failed
echo Built: %CD%\dist\ChatGPTContinueTimer.exe
pause
exit /b 0
:failed
echo Build failed. Install Python 3.11 or later and review any errors above.
pause
exit /b 1

@echo off
setlocal
title Build Disk Usage Report
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
  echo Python 3 was not found. Install Python and try again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating an isolated build environment...
  py -3 -m venv .venv
  if errorlevel 1 goto :failed
)

echo Installing build requirements...
".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto :failed

echo Building the standalone app...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean DiskUsageReport.spec
if errorlevel 1 goto :failed

echo.
echo Build complete: %CD%\dist\DiskUsageReport.exe
pause
exit /b 0

:failed
echo.
echo The build failed. Review the messages above and try again.
pause
exit /b 1

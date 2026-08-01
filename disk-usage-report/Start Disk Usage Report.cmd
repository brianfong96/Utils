@echo off
setlocal
title Disk Usage Report Server
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "disk_usage_report.py" %*
  set "REPORT_EXIT=%ERRORLEVEL%"
) else (
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 "disk_usage_report.py" %*
    set "REPORT_EXIT=%ERRORLEVEL%"
  ) else (
    where python >nul 2>&1
    if errorlevel 1 (
      echo.
      echo Python 3 was not found. Install Python, then double-click this file again.
      pause
      exit /b 1
    )
    python "disk_usage_report.py" %*
    set "REPORT_EXIT=%ERRORLEVEL%"
  )
)

if not "%REPORT_EXIT%"=="0" (
  echo.
  echo Disk Usage Report stopped with an error.
  pause
)
exit /b %REPORT_EXIT%

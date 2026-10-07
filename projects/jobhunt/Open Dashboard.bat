@echo off
title Job Search - Dashboard
cd /d "%~dp0"

REM Straight to the dashboard, no menu. Double-click and go.

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo.
    echo   Python was not found. Install it from python.org and tick
    echo   "Add python.exe to PATH" on the first screen.
    echo.
    pause
    exit /b 1
)

if not exist "data\jobs.db" %PY% jobhunt.py init

echo.
echo   Dashboard starting - your browser will open shortly.
echo   Keep this window open while you use the app.
echo   Press Ctrl+C to stop.
echo.
%PY% jobhunt.py serve
pause

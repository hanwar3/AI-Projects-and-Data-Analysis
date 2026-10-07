@echo off
setlocal
cd /d "%~dp0"
title RFP Monitor - keep this window open (minimize it); close it to stop

rem Already running? Just open the dashboard.
netstat -ano | findstr /R /C:":8501 .*LISTENING" >nul
if %errorlevel%==0 (
    start "" http://localhost:8501
    exit /b
)

if not exist ".venv\Scripts\python.exe" (
    echo First run: installing Python packages, this takes a few minutes...
    where uv >nul 2>nul
    if errorlevel 1 (
        python -m venv .venv || goto :fail
        ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
    ) else (
        uv venv .venv --python 3.11 || goto :fail
        uv pip install --python .venv\Scripts\python.exe -r requirements.txt || goto :fail
    )
)

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 6; Start-Process 'http://localhost:8501'"
".venv\Scripts\python.exe" -m streamlit run app.py
exit /b

:fail
echo Setup failed - see the messages above.
pause

@echo off
setlocal EnableDelayedExpansion
title Job Search App
cd /d "%~dp0"

REM ===========================================================================
REM  Job Search App launcher
REM  Double-click this file. Everything runs on this machine; nothing uploads.
REM ===========================================================================

REM --- Find a usable Python -------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo.
    echo   Python was not found on this computer.
    echo.
    echo   Install it from https://www.python.org/downloads/
    echo   On the first screen, tick "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

REM --- First run: create the database ---------------------------------------
if not exist "data\jobs.db" (
    echo.
    echo   First run - setting up the database...
    echo.
    %PY% jobhunt.py init
    echo.
    pause
)

:menu
cls
echo.
echo   ================================================================
echo                          JOB SEARCH APP
echo   ================================================================
echo.
for /f "delims=" %%R in ('%PY% jobhunt.py _active-region 2^>nul') do set "REGION=%%R"
if defined REGION (
    echo    Region:  !REGION!
    echo.
)
echo     1.  Open dashboard                 ^(browse, rank, track^)
echo     2.  Search for new jobs            ^(free sources only^)
echo     3.  Search for new jobs            ^(everything, may cost credit^)
echo     4.  Search a different country/region
echo.
echo     5.  Show my pipeline               ^(applied, stale, follow-ups^)
echo     6.  Show top matches in terminal
echo.
echo     7.  Settings check                 ^(which API keys are set up^)
echo     8.  Edit my profile                ^(titles, skills, region^)
echo     9.  Add a company to watch         ^(paste a careers URL^)
echo.
echo     0.  Exit
echo.
set "choice="
set /p "choice=   Choose (0-9): "

if "%choice%"=="1" goto dashboard
if "%choice%"=="2" goto search_free
if "%choice%"=="3" goto search_all
if "%choice%"=="4" goto search_region
if "%choice%"=="5" goto board
if "%choice%"=="6" goto top
if "%choice%"=="7" goto doctor
if "%choice%"=="8" goto profile
if "%choice%"=="9" goto watch
if "%choice%"=="0" exit /b 0
goto menu

:dashboard
cls
echo.
echo   Starting the dashboard. Your browser will open in a moment.
echo   Leave this window open while you use it.
echo.
echo   Press Ctrl+C here when you are done.
echo.
%PY% jobhunt.py serve
echo.
pause
goto menu

:search_free
cls
echo.
echo   Searching free sources only. Nothing will be charged.
echo.
%PY% jobhunt.py search --free-only
echo.
pause
goto menu

:search_all
cls
echo.
echo   Searching every enabled source, including LinkedIn/Indeed.
echo   These use Apify credit. The monthly cap in your profile is enforced,
echo   and the run stops before exceeding it.
echo.
set "ok="
set /p "ok=   Continue? (y/n): "
if /i not "!ok!"=="y" goto menu
echo.
%PY% jobhunt.py search
echo.
pause
goto menu

:search_region
cls
echo.
%PY% jobhunt.py regions
echo.
set "reg="
set /p "reg=   Region key (blank to cancel): "
if "!reg!"=="" goto menu
echo.
echo   Searching !reg! ...
echo.
%PY% jobhunt.py search --region !reg! --free-only
echo.
echo   ----------------------------------------------------------------
echo   Re-ranking everything for this region...
%PY% jobhunt.py rescore --region !reg!
echo.
echo   To make !reg! your default, choose 8 and set:  active_region: !reg!
echo.
pause
goto menu

:board
cls
echo.
%PY% jobhunt.py board
echo.
pause
goto menu

:top
cls
echo.
%PY% jobhunt.py top --n 25 --min 40
echo.
pause
goto menu

:doctor
cls
echo.
%PY% jobhunt.py doctor
echo.
echo   ----------------------------------------------------------------
echo   To add API keys, edit the ".env" file in this folder.
echo   Copy ".env.example" to ".env" if it does not exist yet.
echo   Every key listed there is free.
echo.
set "ok="
set /p "ok=   Open .env now? (y/n): "
if /i "!ok!"=="y" (
    if not exist ".env" copy ".env.example" ".env" >nul
    notepad ".env"
)
goto menu

:profile
cls
echo.
echo   Opening your profile. Edit titles, skills, locations or active_region.
echo   Save and close Notepad, then the app will re-rank your jobs.
echo.
notepad "profiles\example.yaml"
echo.
echo   Re-ranking with the updated profile...
%PY% jobhunt.py rescore
echo.
pause
goto menu

:watch
cls
echo.
echo   Open a company's careers page, copy the address bar, paste it here.
echo   Works with Greenhouse, Lever, Ashby, Workable and Recruitee.
echo.
echo   Example:  https://boards.greenhouse.io/wri
echo.
set "url="
set /p "url=   Careers URL (blank to cancel): "
if "!url!"=="" goto menu
echo.
%PY% jobhunt.py watch add-url "!url!"
echo.
pause
goto menu

@echo off
title Cyberpunk TCG Set Price Tracker
for %%I in ("%~dp0..") do set "REPO_ROOT=%%~fI"
if exist "%REPO_ROOT%\.git" (
    REM Best-effort fast-forward; ignore exit code if offline, detached, or dirty
    git -C "%REPO_ROOT%" pull --ff-only 2>nul
)
python "%REPO_ROOT%\run_tracker.py" track-sets %*
if %ERRORLEVEL% neq 0 (
    echo.
    pause
    exit /b %ERRORLEVEL%
)

set "REPORT="
if exist "%REPO_ROOT%\.latest_sets_report" set /p REPORT=<"%REPO_ROOT%\.latest_sets_report"
if not defined REPORT if exist "set_price_trajectories.html" set "REPORT=%CD%\set_price_trajectories.html"
if not defined REPORT if exist "%REPO_ROOT%\set_price_trajectories.html" set "REPORT=%REPO_ROOT%\set_price_trajectories.html"

if defined REPORT start "" "%REPORT%"
exit /b 0

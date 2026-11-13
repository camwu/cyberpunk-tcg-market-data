@echo off
title WNC Beta vs Retail Iconics Comparison
for %%I in ("%~dp0..") do set "REPO_ROOT=%%~fI"
if exist "%REPO_ROOT%\.git" (
    REM Best-effort fast-forward; ignore exit code if offline, detached, or dirty
    git -C "%REPO_ROOT%" pull --ff-only 2>nul
)
python "%REPO_ROOT%\run_tracker.py" compare-iconics %*
if %ERRORLEVEL% neq 0 (
    echo.
    pause
    exit /b %ERRORLEVEL%
)

set "REPORT="
if exist "%REPO_ROOT%\.latest_iconics_report" set /p REPORT=<"%REPO_ROOT%\.latest_iconics_report"
if not defined REPORT if exist "wnc_iconic_price_trajectories.html" set "REPORT=%CD%\wnc_iconic_price_trajectories.html"
if not defined REPORT if exist "%REPO_ROOT%\wnc_iconic_price_trajectories.html" set "REPORT=%REPO_ROOT%\wnc_iconic_price_trajectories.html"

if defined REPORT start "" "%REPORT%"
exit /b 0

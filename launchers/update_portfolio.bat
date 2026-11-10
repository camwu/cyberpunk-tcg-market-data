@echo off
title Cyberpunk TCG Portfolio Tracker
echo ================================================================
echo   CYBERPUNK TCG - UPDATING PORTFOLIO AND MARKET PRICES
echo ================================================================
for %%I in ("%~dp0..") do set "REPO_ROOT=%%~fI"
if exist "%REPO_ROOT%\.git" (
    REM Best-effort fast-forward; ignore exit code if offline, detached, or dirty
    git -C "%REPO_ROOT%" pull --ff-only 2>nul
)
python "%REPO_ROOT%\run_tracker.py" %*
if %ERRORLEVEL% neq 0 (
    echo.
    pause
    exit /b %ERRORLEVEL%
)

set "REPORT="
if exist "%REPO_ROOT%\.latest_report" set /p REPORT=<"%REPO_ROOT%\.latest_report"
if not defined REPORT if exist "portfolio_summary.md" set "REPORT=%CD%\portfolio_summary.md"
if not defined REPORT if exist "%REPO_ROOT%\portfolio_summary.md" set "REPORT=%REPO_ROOT%\portfolio_summary.md"

if defined REPORT start "" "%REPORT%"

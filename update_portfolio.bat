@echo off
title Cyberpunk TCG Portfolio Tracker
echo ================================================================
echo   CYBERPUNK TCG - UPDATING PORTFOLIO AND MARKET PRICES
echo ================================================================
python "%~dp0run_tracker.py" %*
if %ERRORLEVEL% neq 0 (
    echo.
    pause
    exit /b %ERRORLEVEL%
)

set "REPORT="
if exist "%~dp0.latest_report" set /p REPORT=<"%~dp0.latest_report"
if not defined REPORT if exist "LATEST_PORTFOLIO_SUMMARY.md" set "REPORT=%CD%\LATEST_PORTFOLIO_SUMMARY.md"
if not defined REPORT if exist "%~dp0LATEST_PORTFOLIO_SUMMARY.md" set "REPORT=%~dp0LATEST_PORTFOLIO_SUMMARY.md"

if not defined REPORT goto :eof

where code >nul 2>&1
if %ERRORLEVEL% equ 0 (
    call code -r "%REPORT%"
) else (
    start "" "%REPORT%"
)

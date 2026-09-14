@echo off
title Cyberpunk TCG Portfolio Tracker
echo ================================================================
echo   CYBERPUNK TCG - UPDATING PORTFOLIO AND MARKET PRICES
echo ================================================================
python "%~dp0run_tracker.py" %*
if %ERRORLEVEL% equ 0 (
    set "REPORT="
    if exist "LATEST_PORTFOLIO_SUMMARY.md" set "REPORT=LATEST_PORTFOLIO_SUMMARY.md"
    if not defined REPORT if exist "%~dp0LATEST_PORTFOLIO_SUMMARY.md" set "REPORT=%~dp0LATEST_PORTFOLIO_SUMMARY.md"
    if defined REPORT (
        where code >nul 2>&1 && (
            start "" code -r "%REPORT%"
        ) || (
            start "" "%REPORT%"
        )
    )
) else (
    echo.
    pause
)

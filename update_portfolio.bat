@echo off
title Cyberpunk TCG Portfolio Tracker
echo ================================================================
echo   CYBERPUNK TCG - UPDATING PORTFOLIO AND MARKET PRICES
echo ================================================================
python "%~dp0run_tracker.py" %*
if %ERRORLEVEL% equ 0 (
    if exist "%~dp0LATEST_PORTFOLIO_SUMMARY.md" (
        start "" "%~dp0LATEST_PORTFOLIO_SUMMARY.md"
    )
)
echo.
pause

@echo off
title Cyberpunk TCG - Add Cash Purchase
echo ================================================================
echo   CYBERPUNK TCG - RECORD CASH PURCHASE (NO RECEIPT)
echo ================================================================
python "%~dp0run_tracker.py" add-cash %*
if %ERRORLEVEL% neq 0 (
    echo.
    pause
    exit /b %ERRORLEVEL%
)
if "%~1"=="" pause

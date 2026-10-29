@echo off
title Cyberpunk TCG - Add Manual Purchase
echo ================================================================
echo   CYBERPUNK TCG - RECORD MANUAL PURCHASE (NO RECEIPT)
echo ================================================================
python "%~dp0run_tracker.py" add-manual %*
if %ERRORLEVEL% neq 0 (
    echo.
    pause
    exit /b %ERRORLEVEL%
)
if "%~1"=="" pause

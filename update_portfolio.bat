@echo off
title Cyberpunk TCG Portfolio Tracker
echo ================================================================
echo   CYBERPUNK TCG - UPDATING PORTFOLIO AND MARKET PRICES
echo ================================================================
python "%~dp0run_tracker.py" %*
if %ERRORLEVEL% equ 0 (
    set "REPORT="
    for /f "delims=" %%I in ('python -c "from tracker.config import load_config; print(load_config().output_report)" 2^>nul') do (
        if exist "%%I" set "REPORT=%%I"
    )
    if not defined REPORT if exist "LATEST_PORTFOLIO_SUMMARY.md" set "REPORT=LATEST_PORTFOLIO_SUMMARY.md"
    if not defined REPORT if exist "%~dp0LATEST_PORTFOLIO_SUMMARY.md" set "REPORT=%~dp0LATEST_PORTFOLIO_SUMMARY.md"
    if defined REPORT (
        set "VSCODE="
        for /f "delims=" %%I in ('where code.cmd 2^>nul') do (
            if not defined VSCODE if exist "%%~dpI..\Code.exe" set "VSCODE=%%~dpI..\Code.exe"
        )
        if not defined VSCODE if exist "%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe" set "VSCODE=%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"
        if not defined VSCODE if exist "%ProgramFiles%\Microsoft VS Code\Code.exe" set "VSCODE=%ProgramFiles%\Microsoft VS Code\Code.exe"
        if defined VSCODE (
            start "" "%VSCODE%" -r "%REPORT%"
        ) else (
            start "" "%REPORT%"
        )
    )
) else (
    echo.
    pause
)

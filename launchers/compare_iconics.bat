@echo off
title WNC Beta vs Retail Iconics Comparison
for %%I in ("%~dp0..") do set "REPO_ROOT=%%~fI"
python "%REPO_ROOT%\run_tracker.py" compare-iconics %*
set "RC=%ERRORLEVEL%"
if %RC% neq 0 goto :end

set "REPORT="
if exist "%REPO_ROOT%\.latest_iconics_report" set /p REPORT=<"%REPO_ROOT%\.latest_iconics_report"
if not defined REPORT if exist "wnc_iconics_comparison.md" set "REPORT=%CD%\wnc_iconics_comparison.md"
if not defined REPORT if exist "%REPO_ROOT%\wnc_iconics_comparison.md" set "REPORT=%REPO_ROOT%\wnc_iconics_comparison.md"
if defined REPORT start "" "%REPORT%"

:end
if "%~1"=="" pause
exit /b %RC%

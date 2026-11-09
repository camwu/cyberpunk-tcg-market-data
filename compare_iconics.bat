@echo off
title WNC Beta vs Retail Iconics Comparison
python "%~dp0run_tracker.py" compare-iconics %*
set "RC=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %RC%

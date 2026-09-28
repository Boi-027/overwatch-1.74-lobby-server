@echo off
title Overwatch 1.74 - Retail route
cd /d "%~dp0"
py -B tools\launch_retail.py %*
set "LAUNCH_RESULT=%ERRORLEVEL%"
if not "%LAUNCH_RESULT%"=="0" pause
exit /b %LAUNCH_RESULT%

@echo off
rem Starts the lobby server and the game. Options: START.bat --help
title Overwatch 1.74 lobby server
cd /d "%~dp0"
where py >nul 2>nul || (
  echo Python is not installed. Install Python 3.10 or newer ^(64-bit^) from python.org, then start again.
  start "" https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
py -3 -B -m ow174 %*
if errorlevel 1 pause

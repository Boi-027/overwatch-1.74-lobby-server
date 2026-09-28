@echo off
REM One-click setup & launcher for the Overwatch 1.74 lobby server.
REM Just double-click this file. It installs what it needs, downloads the
REM relay, lets you pick your Overwatch.exe, and starts the game for you.
title Overwatch 1.74 - Start
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"

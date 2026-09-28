@echo off
REM Collects logs and build info into diagnostics.txt to debug a failed launch.
REM Double-click this AFTER a failed retail attempt, then send diagnostics.txt.
title Overwatch 1.74 - Diagnose
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0diagnose.ps1"
pause

@echo off
REM ==========================================================================
REM  Overwatch 1.74 - RETAIL route launcher (full main menu with the hero).
REM  Starts the lobby + local Battle.net, launches the client with --BNetServer,
REM  and injects the TLS-strip relay. Pass your Overwatch.exe:
REM      run_retail.bat "X:\path\Overwatch\_retail_\Overwatch.exe"
REM  (or the older form: run_retail.bat --game-exe "X:\path\Overwatch.exe")
REM ==========================================================================
title Overwatch 1.74 - Retail route
cd /d "%~dp0"

REM Stop any old lobby/bnet helpers first, so a code update ALWAYS loads instead
REM of the launcher "Reusing" a server that still holds the previous code.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | ? { $_.Name -match 'python|py' -and $_.CommandLine -match 'lobbyserv\.py|bnet\.main' } | % { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }" >nul 2>&1

py -B tools\launch_retail.py %*
set "LAUNCH_RESULT=%ERRORLEVEL%"
if not "%LAUNCH_RESULT%"=="0" pause
exit /b %LAUNCH_RESULT%

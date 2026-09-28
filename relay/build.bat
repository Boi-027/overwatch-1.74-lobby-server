@echo off
setlocal
cd /d "%~dp0"
where cl >nul 2>nul
if errorlevel 1 (
  echo Open an x64 Native Tools Command Prompt for Visual Studio, then run this script.
  exit /b 1
)
cl /nologo /LD /O2 /W3 /EHsc /D_CRT_SECURE_NO_WARNINGS owwfd_relay.cpp /link ws2_32.lib /MACHINE:X64 /OUT:owwfd_relay.dll
exit /b %errorlevel%

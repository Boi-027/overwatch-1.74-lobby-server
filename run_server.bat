@echo off
title Overwatch 1.74 Lobby Server
cd /d "%~dp0"
py -u server\lobbyserv.py %*
pause

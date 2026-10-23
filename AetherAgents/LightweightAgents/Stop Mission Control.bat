@echo off
setlocal
cd /d "%~dp0Bridge"
python -m aether_bridge.launcher stop
timeout /t 2 >nul

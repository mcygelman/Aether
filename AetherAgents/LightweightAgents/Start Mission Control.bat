@echo off
setlocal
cd /d "%~dp0Bridge"
python -m aether_bridge.launcher start --edition atelier
if errorlevel 1 (
  echo.
  echo Mission Control could not start. Read the message above, then press any key.
  pause >nul
)

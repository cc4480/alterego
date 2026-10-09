@echo off
timeout /t 8 /nobreak >nul
taskkill /f /im python.exe >nul 2>&1
timeout /t 3 /nobreak >nul
set PC_BRIDGE_FULL_ACCESS=1
set SEARXNG_URL=http://127.0.0.1:8888
cd /d %USERPROFILE%\pc-mcp-bridge
start "pc-bridge" /min python pc-agent\server.py

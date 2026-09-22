@echo off
title Smart Lamp Voice Dashboard Launcher
cd /d "%~dp0"

echo =======================================================
echo   Smart Lamp Voice Dashboard Launcher
echo =======================================================
echo.

echo [1/3] Dong va giai phong cac tien trinh cu tren Port 8088...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8088 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo [2/3] Dang khoi dong Voice Receiver Server tren http://localhost:8088 ...
echo [3/3] Mo trinh duyiet Web UI...
echo.

start "" "http://localhost:8088"

python scratch\udp_receiver.py

if errorlevel 1 (
    echo.
    echo Press any key to close...
    pause > nul
)

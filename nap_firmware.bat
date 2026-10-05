@echo off
cd /d "%~dp0"
echo ====================================================================
echo   SMART DESK LAMP - NAP FIRMWARE ESP32-S3
echo ====================================================================
echo.
echo [*] Bat dau nap code vao cong COM3...
python -m platformio run --target upload
echo.
if %ERRORLEVEL% EQU 0 (
    echo ====================================================================
    echo   [SUCCESS] NAP CODE THANH CONG 100%! DEN DANG KHOI DONG LAI.
    echo ====================================================================
) else (
    echo.
    echo [!] Neu gap loi Access is denied, hay chuot phai vao file nay
    echo     chon "Run as administrator" (Chay voi quyen Administrator).
)
echo.
pause

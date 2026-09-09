@echo off
chcp 65001 > nul
echo =========================================================
echo   KHOI DONG GIAO DIEN DANG BAI FACEBOOK HANG LOAT (WEB UI)
echo =========================================================
echo.

echo Dang khoi dong Web Dashboard tai: http://127.0.0.1:8000
echo.

start "" http://127.0.0.1:8000
python server.py
pause

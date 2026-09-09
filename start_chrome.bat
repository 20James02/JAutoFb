@echo off
chcp 65001 > nul
echo ===================================================
echo   KHOI DONG GOOGLE CHROME VOI REMOTE DEBUGGING (9222)
echo ===================================================
echo.

set CHROME_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist %CHROME_PATH% (
    set CHROME_PATH="C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
)

if not exist %CHROME_PATH% (
    echo [LOI] Khong tim thay Google Chrome tai duong dan mac dinh!
    pause
    exit /b 1
)

set PROFILE_DIR=%~dp0chrome_fb_profile

echo [THONG TIN] Su dung profile rieng de khong can tat Chrome dang dung:
echo   %PROFILE_DIR%
echo.
echo Cua so Chrome se duoc mo ra. 
echo 1. Hay dang nhap Facebook tren cua so do (chi can lam 1 lan duy nhat).
echo 2. Giu cua so Chrome do mo.
echo 3. Chay script fb_poster.py de dang bai tu dong.
echo.

start "" %CHROME_PATH% --remote-debugging-port=9222 --user-data-dir="%PROFILE_DIR%" https://www.facebook.com
echo [OK] Da mo Chrome voi cong 9222.

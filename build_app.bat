@echo off
chcp 65001 >nul
title PhishingTool Builder
echo.
echo ================================================
echo   PhishingTool - Build standalone app
echo ================================================
echo.

:: Kiểm tra Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Khong tim thay Python. Cai Python 3.11+ truoc khi build.
    pause
    exit /b 1
)

:: Kiểm tra / cài PyInstaller
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Cai dat PyInstaller...
    pip install pyinstaller
)

:: Cài requirements nếu chưa có
echo [INFO] Kiem tra dependencies...
pip install -r requirements.txt -q

:: Xóa build cũ (phải close PhishingTool.exe trước nếu đang chạy)
echo [INFO] Xoa build cu...
if exist "dist\PhishingTool" rmdir /s /q "dist\PhishingTool" 2>nul
if exist "build\PhishingTool" rmdir /s /q "build\PhishingTool" 2>nul

:: Build
echo.
echo [INFO] Dang build... (co the mat 5-10 phut)
echo.
python -m PyInstaller PhishingTool.spec -y

if errorlevel 1 (
    echo.
    echo [LOI] Build that bai. Xem log o tren.
    pause
    exit /b 1
)

:: Tạo config.ini mẫu trong dist nếu chưa có
:: Copy config.ini thật vào dist (ưu tiên config.ini gốc, fallback config.example.ini)
if exist "config.ini" (
    copy "config.ini" "dist\PhishingTool\config.ini" >nul
    echo [INFO] Da copy config.ini -> dist\PhishingTool\config.ini
) else if not exist "dist\PhishingTool\config.ini" (
    copy "config.example.ini" "dist\PhishingTool\config.ini" >nul
    echo [INFO] Da copy config.example.ini -> dist\PhishingTool\config.ini
    echo [INFO] Sua file config.ini do truoc khi dung app.
)

:: Tạo thư mục reports trong dist
if not exist "dist\PhishingTool\reports" mkdir "dist\PhishingTool\reports"

echo.
echo ================================================
echo   BUILD THANH CONG!
echo.
echo   App nam tai: dist\PhishingTool\PhishingTool.exe
echo.
echo   Chia se toan bo folder dist\PhishingTool\ cho dong doi.
echo   Dong doi can:
echo     1. Mo file config.ini, dien thong tin SMTP + API key
echo     2. Double-click PhishingTool.exe de chay
echo ================================================
echo.
pause

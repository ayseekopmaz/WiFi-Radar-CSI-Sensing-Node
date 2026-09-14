@echo off
echo ====================================
echo    WiFi RADAR - Baslatiliyor...
echo ====================================
echo.

:: Yonetici kontrolu
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Yonetici yetkisi gerekiyor!
    echo [!] Bu dosyaya sag tiklayip "Yonetici olarak calistir" secin.
    echo.
    pause
    exit /b 1
)

:: Python kontrolu
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Python bulunamadi! Python 3.8+ yukleyin.
    pause
    exit /b 1
)

:: Bagimliliklari kontrol et
echo [*] Bagimliliklar kontrol ediliyor...
pip install -r requirements.txt -q

echo.
echo [*] WiFi Radar baslatiliyor...
echo [*] Tarayicinizda http://localhost:5000 adresini acin
echo.

python app.py

pause

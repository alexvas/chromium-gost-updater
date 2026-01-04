@echo off
REM Скрипт для поиска записей Chromium Gost Browser в реестре Windows
REM Использование: find-chromium-gost-registry.bat

echo ========================================
echo Поиск Chromium Gost Browser в реестре Windows
echo ========================================
echo.

set FOUND=0

REM Приоритет 1: BLBeacon разделы
echo [1] Проверка BLBeacon разделов...
reg query "HKCU\Software\ChromiumGost\BLBeacon" /v version >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo   [НАЙДЕНО] HKCU\Software\ChromiumGost\BLBeacon
    reg query "HKCU\Software\ChromiumGost\BLBeacon" /v version
    set FOUND=1
)

reg query "HKCU\Software\Chromium Gost\BLBeacon" /v version >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo   [НАЙДЕНО] HKCU\Software\Chromium Gost\BLBeacon
    reg query "HKCU\Software\Chromium Gost\BLBeacon" /v version
    set FOUND=1
)

reg query "HKLM\SOFTWARE\ChromiumGost\BLBeacon" /v version >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo   [НАЙДЕНО] HKLM\SOFTWARE\ChromiumGost\BLBeacon
    reg query "HKLM\SOFTWARE\ChromiumGost\BLBeacon" /v version
    set FOUND=1
)

reg query "HKLM\SOFTWARE\Chromium Gost\BLBeacon" /v version >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo   [НАЙДЕНО] HKLM\SOFTWARE\Chromium Gost\BLBeacon
    reg query "HKLM\SOFTWARE\Chromium Gost\BLBeacon" /v version
    set FOUND=1
)

REM Приоритет 2: App Paths
echo.
echo [2] Проверка App Paths...
reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chromium.exe" >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo   [НАЙДЕНО] HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chromium.exe
    reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chromium.exe"
    set FOUND=1
)

reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chromium-gost.exe" >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo   [НАЙДЕНО] HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chromium-gost.exe
    reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chromium-gost.exe"
    set FOUND=1
)

reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chromium.exe" >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo   [НАЙДЕНО] HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chromium.exe
    reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chromium.exe"
    set FOUND=1
)

reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chromium-gost.exe" >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo   [НАЙДЕНО] HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chromium-gost.exe
    reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chromium-gost.exe"
    set FOUND=1
)

REM Приоритет 3: Uninstall раздел (базовый поиск)
echo.
echo [3] Проверка Uninstall раздела...
echo   Поиск записей с "Chromium" и "Gost" в DisplayName...
echo   (Это может занять некоторое время...)
echo.

REM Поиск в HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall
for /f "tokens=*" %%i in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall" 2^>nul ^| findstr /i "chromium gost"') do (
    echo   [НАЙДЕНО] %%i
    reg query "%%i" /v DisplayName 2>nul
    reg query "%%i" /v DisplayVersion 2>nul
    reg query "%%i" /v Version 2>nul
    set FOUND=1
)

REM Поиск в HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall
for /f "tokens=*" %%i in ('reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall" 2^>nul ^| findstr /i "chromium gost"') do (
    echo   [НАЙДЕНО] %%i
    reg query "%%i" /v DisplayName 2>nul
    reg query "%%i" /v DisplayVersion 2>nul
    reg query "%%i" /v Version 2>nul
    set FOUND=1
)

REM Более детальный поиск по DisplayName
echo   Детальный поиск по DisplayName...
for /f "tokens=*" %%i in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall" /s /v DisplayName 2^>nul ^| findstr /i "chromium.*gost gost.*chromium"') do (
    echo   [НАЙДЕНО] %%i
    set FOUND=1
)

for /f "tokens=*" %%i in ('reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall" /s /v DisplayName 2^>nul ^| findstr /i "chromium.*gost gost.*chromium"') do (
    echo   [НАЙДЕНО] %%i
    set FOUND=1
)

REM Приоритет 4: Стандартные пути установки
echo.
echo [4] Проверка стандартных путей установки...
if exist "C:\Program Files\Chromium Gost\Application\chromium.exe" (
    echo   [НАЙДЕНО] C:\Program Files\Chromium Gost\Application\chromium.exe
    set FOUND=1
)

if exist "C:\Program Files (x86)\Chromium Gost\Application\chromium.exe" (
    echo   [НАЙДЕНО] C:\Program Files (x86)\Chromium Gost\Application\chromium.exe
    set FOUND=1
)

if exist "C:\Program Files\ChromiumGost\Application\chromium.exe" (
    echo   [НАЙДЕНО] C:\Program Files\ChromiumGost\Application\chromium.exe
    set FOUND=1
)

if exist "C:\Program Files (x86)\ChromiumGost\Application\chromium.exe" (
    echo   [НАЙДЕНО] C:\Program Files (x86)\ChromiumGost\Application\chromium.exe
    set FOUND=1
)

echo.
echo ========================================
if %FOUND% == 1 (
    echo РЕЗУЛЬТАТ: Найдены записи Chromium Gost Browser
) else (
    echo РЕЗУЛЬТАТ: Записи Chromium Gost Browser не найдены
    echo Возможно, браузер не установлен или использует другие пути в реестре
)
echo ========================================
echo.
echo Примечание: Для более детального поиска рекомендуется использовать PowerShell скрипт:
echo   find-chromium-gost-registry.ps1
echo.

pause



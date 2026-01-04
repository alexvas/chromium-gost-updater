# Скрипт для поиска записей Chromium Gost Browser в реестре Windows
# Использование: .\find-chromium-gost-registry.ps1

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Поиск Chromium Gost Browser в реестре Windows" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$found = $false

# Приоритет 1: BLBeacon (как у Chrome)
Write-Host "[1] Проверка BLBeacon разделов..." -ForegroundColor Yellow
$beaconPaths = @(
    "HKCU:\Software\ChromiumGost\BLBeacon",
    "HKCU:\Software\Chromium Gost\BLBeacon",
    "HKLM:\SOFTWARE\ChromiumGost\BLBeacon",
    "HKLM:\SOFTWARE\Chromium Gost\BLBeacon"
)

foreach ($path in $beaconPaths) {
    try {
        if (Test-Path $path) {
            $version = (Get-ItemProperty -Path $path -Name "version" -ErrorAction SilentlyContinue).version
            if ($version) {
                Write-Host "  [НАЙДЕНО] $path" -ForegroundColor Green
                Write-Host "    Версия: $version" -ForegroundColor Green
                $found = $true
            } else {
                Write-Host "  [СУЩЕСТВУЕТ, но нет version] $path" -ForegroundColor Yellow
                $props = Get-ItemProperty -Path $path -ErrorAction SilentlyContinue
                if ($props) {
                    Write-Host "    Доступные параметры:" -ForegroundColor Gray
                    $props.PSObject.Properties | Where-Object { $_.Name -ne 'PSPath' -and $_.Name -ne 'PSParentPath' -and $_.Name -ne 'PSChildName' -and $_.Name -ne 'PSDrive' -and $_.Name -ne 'PSProvider' } | ForEach-Object {
                        Write-Host "      $($_.Name) = $($_.Value)" -ForegroundColor Gray
                    }
                }
            }
        }
    } catch {
        # Игнорируем ошибки
    }
}

# Приоритет 2: App Paths
Write-Host ""
Write-Host "[2] Проверка App Paths..." -ForegroundColor Yellow
$appPaths = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chromium.exe",
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chromium-gost.exe",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chromium.exe",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chromium-gost.exe"
)

foreach ($path in $appPaths) {
    try {
        if (Test-Path $path) {
            $exePath = (Get-ItemProperty -Path $path -Name "(default)" -ErrorAction SilentlyContinue).'(default)'
            if ($exePath -and (Test-Path $exePath)) {
                Write-Host "  [НАЙДЕНО] $path" -ForegroundColor Green
                Write-Host "    Путь к файлу: $exePath" -ForegroundColor Green
                
                # Попытка получить версию из файла
                try {
                    $fileVersion = (Get-Item $exePath).VersionInfo.FileVersion
                    if ($fileVersion) {
                        Write-Host "    Версия файла: $fileVersion" -ForegroundColor Green
                        $found = $true
                    }
                } catch {
                    Write-Host "    Не удалось получить версию из файла" -ForegroundColor Yellow
                }
            }
        }
    } catch {
        # Игнорируем ошибки
    }
}

# Приоритет 3: Uninstall раздел
Write-Host ""
Write-Host "[3] Проверка Uninstall раздела..." -ForegroundColor Yellow
$uninstallPaths = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
)

foreach ($basePath in $uninstallPaths) {
    try {
        if (Test-Path $basePath) {
            $subkeys = Get-ChildItem -Path $basePath -ErrorAction SilentlyContinue
            foreach ($subkey in $subkeys) {
                try {
                    $displayName = (Get-ItemProperty -Path $subkey.PSPath -Name "DisplayName" -ErrorAction SilentlyContinue).DisplayName
                    if ($displayName -and ($displayName -match "chromium" -or $displayName -match "Chromium") -and ($displayName -match "gost" -or $displayName -match "Gost")) {
                        Write-Host "  [НАЙДЕНО] $($subkey.PSPath)" -ForegroundColor Green
                        Write-Host "    DisplayName: $displayName" -ForegroundColor Green
                        
                        $displayVersion = (Get-ItemProperty -Path $subkey.PSPath -Name "DisplayVersion" -ErrorAction SilentlyContinue).DisplayVersion
                        if ($displayVersion) {
                            Write-Host "    DisplayVersion: $displayVersion" -ForegroundColor Green
                            $found = $true
                        }
                        
                        $version = (Get-ItemProperty -Path $subkey.PSPath -Name "Version" -ErrorAction SilentlyContinue).Version
                        if ($version) {
                            Write-Host "    Version: $version" -ForegroundColor Green
                        }
                        
                        $installLocation = (Get-ItemProperty -Path $subkey.PSPath -Name "InstallLocation" -ErrorAction SilentlyContinue).InstallLocation
                        if ($installLocation) {
                            Write-Host "    InstallLocation: $installLocation" -ForegroundColor Gray
                        }
                        
                        Write-Host "    Все параметры:" -ForegroundColor Gray
                        $props = Get-ItemProperty -Path $subkey.PSPath -ErrorAction SilentlyContinue
                        $props.PSObject.Properties | Where-Object { $_.Name -notmatch "^PS" } | ForEach-Object {
                            Write-Host "      $($_.Name) = $($_.Value)" -ForegroundColor Gray
                        }
                    }
                } catch {
                    # Игнорируем ошибки для отдельных ключей
                }
            }
        }
    } catch {
        Write-Host "  Ошибка доступа к $basePath" -ForegroundColor Red
    }
}

# Приоритет 4: Стандартные пути установки
Write-Host ""
Write-Host "[4] Проверка стандартных путей установки..." -ForegroundColor Yellow
$standardPaths = @(
    "C:\Program Files\Chromium Gost\Application\chromium.exe",
    "C:\Program Files (x86)\Chromium Gost\Application\chromium.exe",
    "C:\Program Files\ChromiumGost\Application\chromium.exe",
    "C:\Program Files (x86)\ChromiumGost\Application\chromium.exe"
)

foreach ($exePath in $standardPaths) {
    if (Test-Path $exePath) {
        Write-Host "  [НАЙДЕНО] $exePath" -ForegroundColor Green
        try {
            $fileVersion = (Get-Item $exePath).VersionInfo.FileVersion
            if ($fileVersion) {
                Write-Host "    Версия файла: $fileVersion" -ForegroundColor Green
                $found = $true
            }
            
            # Попытка получить версию через --version
            try {
                $versionOutput = & $exePath --version 2>&1
                if ($versionOutput) {
                    Write-Host "    Версия (--version): $versionOutput" -ForegroundColor Green
                }
            } catch {
                # Игнорируем ошибки
            }
        } catch {
            Write-Host "    Не удалось получить версию" -ForegroundColor Yellow
        }
    }
}

# Дополнительный поиск: все ключи с "chromium" или "gost" в названии
Write-Host ""
Write-Host "[5] Дополнительный поиск ключей с 'chromium' или 'gost'..." -ForegroundColor Yellow
$searchPaths = @(
    "HKLM:\SOFTWARE",
    "HKCU:\Software"
)

foreach ($searchPath in $searchPaths) {
    try {
        if (Test-Path $searchPath) {
            $foundKeys = Get-ChildItem -Path $searchPath -Recurse -ErrorAction SilentlyContinue | Where-Object {
                $_.PSChildName -match "chromium" -or $_.PSChildName -match "Chromium" -or 
                $_.PSChildName -match "gost" -or $_.PSChildName -match "Gost"
            } | Select-Object -First 20  # Ограничиваем количество результатов
            
            foreach ($key in $foundKeys) {
                Write-Host "  [НАЙДЕН КЛЮЧ] $($key.PSPath)" -ForegroundColor Cyan
                try {
                    $props = Get-ItemProperty -Path $key.PSPath -ErrorAction SilentlyContinue
                    $props.PSObject.Properties | Where-Object { 
                        $_.Name -notmatch "^PS" -and $_.Value -ne $null 
                    } | Select-Object -First 5 | ForEach-Object {
                        Write-Host "    $($_.Name) = $($_.Value)" -ForegroundColor Gray
                    }
                } catch {
                    # Игнорируем ошибки
                }
            }
        }
    } catch {
        # Игнорируем ошибки доступа
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
if ($found) {
    Write-Host "РЕЗУЛЬТАТ: Найдены записи Chromium Gost Browser" -ForegroundColor Green
} else {
    Write-Host "РЕЗУЛЬТАТ: Записи Chromium Gost Browser не найдены" -ForegroundColor Yellow
    Write-Host "Возможно, браузер не установлен или использует другие пути в реестре" -ForegroundColor Yellow
}
Write-Host "========================================" -ForegroundColor Cyan



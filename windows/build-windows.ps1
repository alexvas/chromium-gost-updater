# Скрипт сборки Windows-дистрибутива chromium-gost-updater
# Использование: .\build-windows.ps1 [-Version "1.0.0"] [-BuildType "portable"|"installer"]

param(
    [string]$Version = "",
    [string]$BuildType = "portable"  # portable или installer
)

$ErrorActionPreference = "Stop"

# Определяем версию
if ([string]::IsNullOrEmpty($Version)) {
    # Пытаемся получить версию из git тега
    try {
        $gitTag = git describe --tags --exact-match HEAD 2>$null
        if ($gitTag -match "^v(.+)$") {
            $Version = $matches[1]
        } else {
            $Version = (Get-Date -Format "yyyyMMddHHmmss") + "-" + (git rev-parse --short HEAD)
        }
    } catch {
        $Version = (Get-Date -Format "yyyyMMddHHmmss")
    }
}

Write-Host "Сборка Windows-дистрибутива версии: $Version" -ForegroundColor Cyan
Write-Host "Тип сборки: $BuildType" -ForegroundColor Cyan

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$BuildDir = Join-Path $RootDir "build-windows"
$DistDir = Join-Path $BuildDir "dist"
$OutputDir = Join-Path $RootDir "dist-windows"

# Очистка и создание директорий
if (Test-Path $BuildDir) {
    Remove-Item -Recurse -Force $BuildDir
}
if (Test-Path $OutputDir) {
    Remove-Item -Recurse -Force $OutputDir
}

New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null
New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

# Проверка Python
Write-Host "`nПроверка Python..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "  Найден: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "  Ошибка: Python не найден!" -ForegroundColor Red
    Write-Host "  Установите Python 3.11 или новее с https://www.python.org/" -ForegroundColor Red
    exit 1
}

# Проверка/установка pip
Write-Host "`nПроверка pip..." -ForegroundColor Yellow
try {
    python -m pip --version | Out-Null
    Write-Host "  pip найден" -ForegroundColor Green
} catch {
    Write-Host "  Ошибка: pip не найден!" -ForegroundColor Red
    exit 1
}

# Создание виртуального окружения
Write-Host "`nСоздание виртуального окружения..." -ForegroundColor Yellow
$VenvDir = Join-Path $BuildDir "venv"
python -m venv $VenvDir
& "$VenvDir\Scripts\Activate.ps1"
python -m pip install --upgrade pip | Out-Null

# Установка зависимостей
Write-Host "`nУстановка зависимостей..." -ForegroundColor Yellow
$RequirementsFile = Join-Path $ScriptDir "requirements-windows.txt"
python -m pip install -r $RequirementsFile
Write-Host "  Зависимости установлены" -ForegroundColor Green

# Копирование файлов приложения
Write-Host "`nКопирование файлов приложения..." -ForegroundColor Yellow
$AppFiles = @(
    "chromium-gost-updater.py",
    "chromium-gost-updater.toml"
)

foreach ($file in $AppFiles) {
    $src = Join-Path $RootDir $file
    if (Test-Path $src) {
        Copy-Item $src $DistDir
        Write-Host "  Скопирован: $file" -ForegroundColor Green
    } else {
        Write-Host "  Предупреждение: файл $file не найден" -ForegroundColor Yellow
    }
}

# Копирование иконки, если есть
$IconFile = Join-Path $RootDir "chromium-gost-logo.png"
if (Test-Path $IconFile) {
    Copy-Item $IconFile $DistDir
    Write-Host "  Скопирована иконка" -ForegroundColor Green
}

# Копирование README и NOTICE
$DocFiles = @("README.md", "NOTICE.txt")
foreach ($file in $DocFiles) {
    $src = Join-Path $RootDir $file
    if (Test-Path $src) {
        Copy-Item $src $DistDir
    }
}

# Создание структуры для portable версии с Python embeddable
Write-Host "`nСоздание portable-версии с Python embeddable..." -ForegroundColor Yellow

$PythonDir = Join-Path $DistDir "python"
$EmbeddableSuccess = $false

# Определяем версию Python для загрузки embeddable (минимум 3.11 для tomllib)
$PythonVersionOutput = python --version 2>&1
if ($PythonVersionOutput -match "Python (\d+)\.(\d+)\.(\d+)") {
    $PythonMajor = [int]$matches[1]
    $PythonMinor = [int]$matches[2]
    $PythonPatch = [int]$matches[3]
    
    # Гарантируем минимум Python 3.11 для встроенного tomllib
    if ($PythonMajor -lt 3 -or ($PythonMajor -eq 3 -and $PythonMinor -lt 11)) {
        Write-Host "  Системный Python $PythonMajor.$PythonMinor.$PythonPatch < 3.11, используем Python 3.11.0 embeddable" -ForegroundColor Yellow
        $PythonMajor = 3
        $PythonMinor = 11
        $PythonPatch = 0
        $PythonVersion = "3.11.0"
    } else {
        $PythonVersion = "${PythonMajor}.${PythonMinor}.${PythonPatch}"
        Write-Host "  Версия Python: $PythonVersion" -ForegroundColor Gray
    }
} else {
    Write-Host "  Не удалось определить версию Python, используем Python 3.11.0 embeddable" -ForegroundColor Yellow
    $PythonMajor = 3
    $PythonMinor = 11
    $PythonPatch = 0
    $PythonVersion = "3.11.0"
}

# Определяем архитектуру
$Arch = if ([Environment]::Is64BitOperatingSystem) { "amd64" } else { "win32" }
$EmbeddableZip = "python-${PythonVersion}-embed-${Arch}.zip"
$EmbeddableUrl = "https://www.python.org/ftp/python/${PythonVersion}/${EmbeddableZip}"

$EmbeddableCacheDir = Join-Path $BuildDir "python-embeddable-cache"
New-Item -ItemType Directory -Path $EmbeddableCacheDir -Force | Out-Null
$EmbeddableZipPath = Join-Path $EmbeddableCacheDir $EmbeddableZip

# Загрузка Python embeddable
if (-not (Test-Path $EmbeddableZipPath)) {
    Write-Host "  Загрузка Python embeddable..." -ForegroundColor Gray
    try {
        Invoke-WebRequest -Uri $EmbeddableUrl -OutFile $EmbeddableZipPath -UseBasicParsing
        Write-Host "  Python embeddable загружен" -ForegroundColor Green
    } catch {
        Write-Host "  Ошибка загрузки Python embeddable: $_" -ForegroundColor Yellow
        Write-Host "  Пропускаем создание portable-версии с Python embeddable" -ForegroundColor Yellow
    }
}

if (Test-Path $EmbeddableZipPath) {
    # Распаковка Python embeddable
    Write-Host "  Распаковка Python embeddable..." -ForegroundColor Gray
    Expand-Archive -Path $EmbeddableZipPath -DestinationPath $PythonDir -Force
    
    # Включаем pyvenv.cfg для правильной работы pip
    $PyvenvCfg = Join-Path $PythonDir "pyvenv.cfg"
    if (-not (Test-Path $PyvenvCfg)) {
        @"
include-system-site-packages = false
version = $PythonVersion
"@ | Out-File -FilePath $PyvenvCfg -Encoding ASCII
    }
    
    # Разблокируем python311._pth для работы с пакетами
    $PthFile = Join-Path $PythonDir "python${PythonMajor}${PythonMinor}._pth"
    if (Test-Path $PthFile) {
        $PthContent = Get-Content $PthFile -Raw
        if ($PthContent -match "#\s*(import site)") {
            $PthContent = $PthContent -replace "#\s*(import site)", "import site"
            Set-Content -Path $PthFile -Value $PthContent -NoNewline
        } elseif ($PthContent -notmatch "import site") {
            # Добавляем import site в конец
            $PthContent = $PthContent.TrimEnd() + "`r`nimport site`r`n"
            Set-Content -Path $PthFile -Value $PthContent -NoNewline
        }
    }
    
    # Загружаем get-pip.py и устанавливаем pip
    Write-Host "  Установка pip в Python embeddable..." -ForegroundColor Gray
    $GetPipPath = Join-Path $EmbeddableCacheDir "get-pip.py"
    if (-not (Test-Path $GetPipPath)) {
        Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $GetPipPath -UseBasicParsing
    }
    
    $EmbeddablePython = Join-Path $PythonDir "python.exe"
    & $EmbeddablePython $GetPipPath --quiet
    
    # Устанавливаем зависимости в embeddable Python
    Write-Host "  Установка зависимостей в Python embeddable..." -ForegroundColor Gray
    & $EmbeddablePython -m pip install --quiet --upgrade pip
    & $EmbeddablePython -m pip install --quiet -r $RequirementsFile
    
    $EmbeddableSuccess = $true
    Write-Host "  Python embeddable подготовлен" -ForegroundColor Green
} else {
    # Удаляем пустую директорию, если загрузка не удалась
    if (Test-Path $PythonDir) {
        Remove-Item -Path $PythonDir -Force -ErrorAction SilentlyContinue
    }
}

# Создание bat-файла для запуска (только если Python embeddable установлен)
if ($EmbeddableSuccess -and (Test-Path (Join-Path $PythonDir "python.exe"))) {
    $LauncherBat = Join-Path $DistDir "chromium-gost-updater.bat"
    $BatContent = @"
@echo off
setlocal

set SCRIPT_DIR=%~dp0
set PYTHON_DIR=%SCRIPT_DIR%python

if exist "%PYTHON_DIR%\python.exe" (
    "%PYTHON_DIR%\python.exe" "%SCRIPT_DIR%chromium-gost-updater.py" %*
) else (
    echo Ошибка: Python embeddable не найден в %PYTHON_DIR%
    pause
    exit /b 1
)
"@
    Set-Content -Path $LauncherBat -Value $BatContent -Encoding ASCII
    Write-Host "  Создан bat-файл для portable-версии" -ForegroundColor Green
}

# Копирование скрипта установки Task Scheduler
Write-Host "`nКопирование скрипта установки Task Scheduler..." -ForegroundColor Yellow
$TaskSchedulerScript = Join-Path $ScriptDir "install-task-scheduler.ps1"
if (Test-Path $TaskSchedulerScript) {
    Copy-Item $TaskSchedulerScript $DistDir
    Write-Host "  Скопирован install-task-scheduler.ps1" -ForegroundColor Green
} else {
    Write-Host "  Предупреждение: install-task-scheduler.ps1 не найден" -ForegroundColor Yellow
}

# Создание ZIP-архива
Write-Host "`nСоздание ZIP-архива..." -ForegroundColor Yellow
$ZipName = "chromium-gost-updater-${Version}-windows.zip"
$ZipPath = Join-Path $OutputDir $ZipName

Compress-Archive -Path "$DistDir\*" -DestinationPath $ZipPath -Force
Write-Host "  Создан архив: $ZipName" -ForegroundColor Green

# Если нужен установщик, создаем его
if ($BuildType -eq "installer") {
    Write-Host "`nСоздание установщика..." -ForegroundColor Yellow
    
    # Проверка NSIS
    $NSISPath = "${env:ProgramFiles(x86)}\NSIS\makensis.exe"
    if (-not (Test-Path $NSISPath)) {
        $NSISPath = "${env:ProgramFiles}\NSIS\makensis.exe"
    }
    
    if (-not (Test-Path $NSISPath)) {
        Write-Host "  Предупреждение: NSIS не найден!" -ForegroundColor Yellow
        Write-Host "  Установите NSIS с https://nsis.sourceforge.io/" -ForegroundColor Yellow
        Write-Host "  ZIP-архив создан: $ZipPath" -ForegroundColor Green
    } else {
        $NSISScript = Join-Path $ScriptDir "chromium-gost-updater.nsi"
        if (Test-Path $NSISScript) {
            $NSISOutput = Join-Path $OutputDir "chromium-gost-updater-${Version}-setup.exe"
            & $NSISPath "/DVERSION=$Version" "/DOUTPUT=$NSISOutput" "/DDISTDIR=$DistDir" $NSISScript
            Write-Host "  Установщик создан: $(Split-Path -Leaf $NSISOutput)" -ForegroundColor Green
        } else {
            Write-Host "  Предупреждение: NSIS скрипт не найден: $NSISScript" -ForegroundColor Yellow
        }
    }
}

Write-Host "`nСборка завершена!" -ForegroundColor Green
Write-Host "Результаты находятся в: $OutputDir" -ForegroundColor Cyan

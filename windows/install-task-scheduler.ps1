# Скрипт установки задачи Task Scheduler для chromium-gost-updater
# Использование: .\install-task-scheduler.ps1 [-InstallDir "путь"]

param(
    [string]$InstallDir = ""
)

$ErrorActionPreference = "Stop"

# Определяем директорию установки
if ([string]::IsNullOrEmpty($InstallDir)) {
    $InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}

$BatPath = Join-Path $InstallDir "chromium-gost-updater.bat"
if (-not (Test-Path $BatPath)) {
    Write-Host "Ошибка: chromium-gost-updater.bat не найден в $InstallDir" -ForegroundColor Red
    exit 1
}

$TaskName = "ChromiumGostUpdater"
$TaskDescription = "Автоматическая проверка обновлений Chromium Gost (каждый час)"

Write-Host "Установка задачи Task Scheduler: $TaskName" -ForegroundColor Cyan

# Удаляем существующую задачу, если есть
$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($ExistingTask) {
    Write-Host "Удаление существующей задачи..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
}

# Создаем действие - запуск bat-файла с флагом --show-tray-lazily
$Action = New-ScheduledTaskAction -Execute $BatPath -Argument "--show-tray-lazily" -WorkingDirectory $InstallDir

# Создаем триггеры
# 1. При входе пользователя (с задержкой 5 минут)
$OnLogonTrigger = New-ScheduledTaskTrigger -AtLogOn
$OnLogonTrigger.Delay = "PT5M"  # 5 минут задержки

# 2. Периодически каждый час (начиная с момента регистрации)
$RepetitionInterval = (New-TimeSpan -Hours 1)
$RepetitionDuration = [TimeSpan]::MaxValue  # Бесконечно
$Repetition = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval $RepetitionInterval -RepetitionDuration $RepetitionDuration

# Настройки задачи
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable
$Settings.ExecutionTimeLimit = "PT0S"  # Без ограничения времени
$Settings.RestartCount = 0

# Принцип запуска - только когда пользователь вошёл в систему
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

# Регистрируем задачу
try {
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger @($OnLogonTrigger, $Repetition) -Settings $Settings -Principal $Principal -Description $TaskDescription -Force | Out-Null
    Write-Host "Задача успешно установлена: $TaskName" -ForegroundColor Green
    Write-Host "  Запуск: каждый час, первый запуск через 5 минут после входа" -ForegroundColor Gray
    Write-Host "  Команда: $BatPath --show-tray-lazily" -ForegroundColor Gray
} catch {
    Write-Host "Ошибка при установке задачи: $_" -ForegroundColor Red
    exit 1
}

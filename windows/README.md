# Windows-упаковка chromium-gost-updater

Этот каталог содержит файлы для сборки Windows-дистрибутива chromium-gost-updater.

## Файлы

- `requirements-windows.txt` - зависимости Python для Windows (PySide6)
- `build-windows.ps1` - PowerShell скрипт для автоматической сборки
- `install-task-scheduler.ps1` - скрипт установки задачи Task Scheduler
- `chromium-gost-updater.nsi` - NSIS скрипт для создания установщика (опционально)
- `README.md` - этот файл

## Быстрый старт

### Автоматическая сборка

```powershell
# Убедитесь, что установлены Python 3.11+ и pip
python --version

# Запустите скрипт сборки
.\build-windows.ps1 -Version "1.0.0"
```

Скрипт создаст ZIP-архив с portable-версией в директории `dist-windows/`.

### Зависимости

Все зависимости (Python 3.11+ embeddable, PySide6) включаются в portable-версию. Пользователю не нужно устанавливать Python или другие зависимости отдельно. Python 3.11+ используется для встроенного `tomllib` (не требуется внешняя библиотека `toml`).

### Структура дистрибутива

После сборки в ZIP-архиве:
- `chromium-gost-updater.bat` - главный launcher
- `chromium-gost-updater.py` - основной скрипт
- `chromium-gost-updater.toml` - конфиг
- `chromium-gost-logo.png` - иконка (если есть)
- `python/` - Python embeddable 3.11+ с зависимостями
- `install-task-scheduler.ps1` - скрипт установки задачи
- `README.md`, `NOTICE.txt` - документация

### Создание установщика (опционально)

Для создания установщика требуется NSIS:

1. Установите NSIS с https://nsis.sourceforge.io/
2. Запустите скрипт с параметром `-BuildType "installer"`:

```powershell
.\build-windows.ps1 -Version "1.0.0" -BuildType "installer"
```

Установщик автоматически:
- Установит файлы в `%LOCALAPPDATA%\chromium-gost-updater`
- Создаст ярлык в меню Пуск
- Зарегистрирует задачу Task Scheduler для автоматической проверки обновлений (каждый час)

### Автоматическая проверка обновлений

После установки через установщик автоматически создаётся задача Task Scheduler:
- Запуск: каждый час
- Первый запуск: через 5 минут после входа пользователя
- Команда: `chromium-gost-updater.bat --show-tray-lazily`

Для ручной установки задачи запустите:
```powershell
.\install-task-scheduler.ps1
```

## Автоматическая сборка через GitHub Actions

Windows-дистрибутив автоматически собирается при создании тега релиза через GitHub Actions workflow.

См. также: [BUILD.md](../BUILD.md)

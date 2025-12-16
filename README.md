# Обновлятор chromium-gost

Это обновлятор браузера Chromium Gost сборки КриптоПро. Скрипт на Питоне, который следит за выходом новой версии браузера, уведомляет об этом пользователя в системном трее, запускает команду установки пакета новой версии в ОС. Скрипт рассчитан на rpm и deb дистрибутивы Линукса.


## 1. Лицензирование

Можно пользоваться на свой страх и риск. Апач 2.0. Подробнее и зануднее в [NOTICE.txt](NOTICE.txt)

## 2. Установка из Deb или Rpm пакета (рекомендуется)
Забираете пакет с [Github Releases](https://github.com/alexvas/chromium-gost-updater/releases) и устанавливаете.


Идея в том, что скрипт-обновлятор меняется редко, а браузер — часто. Установите скрипт один раз, и тот будет следить за обновлениями браузера Chromium Gost [на сайте КриптоПро](https://update.cryptopro.ru/chromium-gost/).

## 3. Полуавтоматическая установка из исходников.

### На уровне системы

Установите зависимости из-под администратора:

**Минимальные требования:**
```bash
sudo apt update
sudo apt install python3 python3-venv python3-full
```

### На уровне пользователя

[Скрипт установки `install.sh`](install.sh) автоматически проверит наличие GUI-библиотек и библиотеки toml в системе:
- **KDE/Plasma**: PySide6 или PyQt5 (устанавливаются через pip)
- **GNOME**: PyGObject (устанавливается через pip в venv, если системные библиотеки доступны)
  - Требуются системные библиотеки: `libgtk-3-dev libappindicator3-dev gir1.2-appindicator3-0.1`
  - Или используйте системный `python3-gi` (без venv)
  - **Важно**: Для отображения иконки в системном трее GNOME требуется расширение AppIndicator Support или TopIcons Plus (см. раздел ниже)
- **Ubuntu Unity**: PyGObject (устанавливается через pip в venv, если системные библиотеки доступны)
  - Требуются системные библиотеки: `libgtk-3-dev libappindicator3-dev gir1.2-appindicator3-0.1`
  - Или используйте системный `python3-gi` (без venv)
- **Другие DE**: PySide6 или PyQt5

Если зависимости отсутствуют, будет создано виртуальное окружение и зависимости будут установлены в него автоматически (для Qt-библиотек и PyGObject, если системные библиотеки установлены).

Запускать скрипт надо от имени пользователя, которому надо показывать уведомления:

```bash
bash ./install.sh
```

### Что и как устанавливается?

Скрипт `install.sh` устанавливает всё в пользовательское пространство без необходимости root-прав:
- `~/.local/bin` - исполняемые файлы (основной скрипт и обёртка)
- `~/.local/share/chromium-gost-updater` - данные приложения (конфиг, иконка, виртуальное окружение при необходимости)
- `~/.config/systemd/user` - systemd user сервисы и таймеры

Скрипт-инсталлятор автоматически:
- Определяет Desktop Environment (KDE/GNOME/Unity/другие)
- Проверяет наличие зависимостей (PySide6/PyQt5 для KDE или python3-gi для GNOME/Unity, и toml) в системе
- Если зависимости отсутствуют, создаёт виртуальное окружение и устанавливает их туда (для Qt-библиотек)
- Копирует файлы в соответствующие директории
- Настраивает systemd user сервисы и таймеры
- Включает и запускает таймеры

**Для GNOME**: Есть два варианта:

1. **Установка через pip в venv** (рекомендуется, если используете venv):
   ```bash
   # Установите системные библиотеки (требуются для PyGObject)
   sudo apt install libgtk-3-dev libappindicator3-dev gir1.2-appindicator3-0.1
   # Скрипт установки автоматически установит PyGObject через pip в venv
   ```

2. **Использование системного python3-gi** (без venv):
   ```bash
   sudo apt install python3-gi gir1.2-appindicator3-0.1
   ```

**Важно для пользователей GNOME**: Начиная с версии GNOME 3.26, системный трей был удалён из стандартной оболочки. Для отображения иконки обновлятора в системном трее необходимо установить одно из расширений:

- **AppIndicator Support** (рекомендуется):

  Устанавливаем вручную с правами администратора:
  ```bash
  # Ubuntu/Debian
  sudo apt install gnome-shell-extension-appindicator
  
  # Fedora
  sudo dnf install gnome-shell-extension-appindicator
  
  # Или через GNOME Extensions (браузер)
  # https://extensions.gnome.org/extension/615/appindicator-support/
  ```

- **TopIcons Plus** (альтернатива):
  ```bash
  # Через GNOME Extensions
  # https://extensions.gnome.org/extension/1031/topicons/
  ```

После установки расширения активируйте его через приложение "Расширения" (GNOME Extensions) или через веб-интерфейс extensions.gnome.org.


**Для Ubuntu Unity**: Есть два варианта:

1. **Установка через pip в venv** (рекомендуется, если используете venv):
   ```bash
   # Установите системные библиотеки (требуются для PyGObject)
   sudo apt install libgtk-3-dev libappindicator3-dev gir1.2-appindicator3-0.1
   # Скрипт установки автоматически установит PyGObject через pip в venv
   ```

2. **Использование системного python3-gi** (без venv):
   ```bash
   sudo apt install python3-gi gir1.2-appindicator3-0.1
   ```

**Обёртка-скрипт** (`chromium-gost-updater-wrapper.sh`) автоматически определяет, использовать ли системный Python или виртуальное окружение.

## 4. Установка вручную (альтернативная)

Если предпочитаете установку вручную, произведите операции из [`install.sh`](install.sh), скорректовав установочную директорию, файл иконки или конфиг.

## 5. Как работает обновлятор

При обнаружении обновления systemd скрипт автоматически попытается запустить
GUI-версию с tray-иконкой (если доступен DISPLAY). Если GUI запустится успешно,
вы получите уведомление и сможете установить обновление через диалог.

**Поддержка Desktop Environments:**
- **KDE/Plasma**: Использует QSystemTrayIcon (PySide6/PyQt5)
- **GNOME**: Использует AppIndicator3 (python3-gi) при наличии расширения AppIndicator Support или TopIcons Plus, иначе QSystemTrayIcon как fallback
- **Ubuntu Unity**: Использует AppIndicator3 (python3-gi)
- **Другие DE**: Использует QSystemTrayIcon (PySide6/PyQt5), если доступен

## 6. Запуск обновлятора вручную:

```bash
~/.local/bin/chromium-gost-updater-wrapper.sh
```

Или напрямую через Python (если зависимости установлены в системе):

```bash
~/.local/bin/chromium-gost-updater.py
```

## 7. Иконка

Поместите иконку под именем chromium-gost-logo.png в директорию со скриптом, тогда скрипт подхватит её для tray. Иначе используется тема-иконка "chromium".

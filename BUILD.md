# Инструкция по сборке пакетов

## Автоматическая сборка через GitHub Actions

### Создание релиза

**Важно:** Создавайте только тег, без релиза. GitHub Actions автоматически создаст или обновит релиз с пакетами.

#### Способ 1: Через командную строку (рекомендуется)

1. Создайте тег для релиза:
   ```bash
   git tag -a v1.0.0 -m "Release version 1.0.0"
   git push origin v1.0.0
   ```

2. GitHub Actions автоматически:
   - Соберёт DEB, RPM и Windows пакеты
   - Создаст или обновит Release на GitHub (если релиз уже существует, он будет обновлён)
   - Загрузит пакеты в GitHub Releases

#### Способ 2: Через веб-интерфейс GitHub

1. Перейдите на страницу репозитория на GitHub
2. Нажмите на "Releases" → "Draft a new release"
3. **НЕ заполняйте форму!** Вместо этого:
   - В поле "Choose a tag" введите новый тег (например, `v1.0.0`)
   - GitHub создаст тег автоматически
   - Нажмите "Create tag: v1.0.0 on publish" (внизу формы)
   - **НЕ нажимайте "Publish release"** - просто закройте страницу
4. GitHub Actions автоматически создаст релиз с пакетами

#### Способ 3: Создание тега через GitHub CLI

```bash
gh release create v1.0.0 --draft --title "Release v1.0.0" --notes "Temporary draft"
gh release delete v1.0.0 --yes
git tag v1.0.0
git push origin v1.0.0
```

**Примечание:** Если релиз уже существует вручную, GitHub Actions обновит его, добавив пакеты.

### Ручной запуск сборки

В GitHub можно запустить workflow вручную через Actions → Build and Release Packages → Run workflow

## Локальная сборка

### Сборка DEB пакета

```bash
# Установите зависимости
sudo apt-get install build-essential devscripts debhelper dh-python python3-all fakeroot

# Обновите changelog (если нужно)
dch --newversion 1.0.0

# Соберите пакет
dpkg-buildpackage -us -uc -b

# Пакет будет в родительской директории: ../chromium-gost-updater_1.0.0_all.deb
```

### Сборка RPM пакета

```bash
# Установите зависимости
sudo apt-get install rpm  # или на RPM-системе: sudo dnf install rpm-build

# Подготовьте окружение
mkdir -p ~/rpmbuild/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

# Создайте архив исходников
VERSION=1.0.0
tar czf ~/rpmbuild/SOURCES/chromium-gost-updater-${VERSION}.tar.gz \
  --transform "s,^,chromium-gost-updater-${VERSION}/," \
  chromium-gost-updater.py \
  chromium-gost-updater-wrapper.sh \
  chromium-gost-updater.service \
  chromium-gost-updater.service.system \
  chromium-gost-remote.timer \
  chromium-gost-updater.toml \
  chromium-gost-logo.png \
  README.md \
  NOTICE.txt

# Скопируйте spec файл
cp rpm/chromium-gost-updater.spec ~/rpmbuild/SPECS/

# Соберите пакет
rpmbuild -ba --define "_version ${VERSION}" ~/rpmbuild/SPECS/chromium-gost-updater.spec

# Пакет будет в: ~/rpmbuild/RPMS/noarch/chromium-gost-updater-${VERSION}-1.noarch.rpm
```

### Сборка Windows-дистрибутива

#### Автоматическая сборка через PowerShell скрипт

```powershell
# Убедитесь, что установлены Python 3.11+ и pip
python --version

# Запустите скрипт сборки
cd windows
.\build-windows.ps1 -Version "1.0.0" -BuildType "portable"
```

Скрипт создаст:
- `dist-windows/chromium-gost-updater-1.0.0-windows.zip` - ZIP-архив с portable-версией

Для создания установщика (требуется NSIS):
```powershell
.\build-windows.ps1 -Version "1.0.0" -BuildType "installer"
```

**Зависимости для Windows:**
- Python 3.11 или новее (используется Python embeddable, встроен в дистрибутив)
- PySide6 (GUI библиотека, автоматически устанавливается через requirements-windows.txt)
- tomllib (встроен в Python 3.11+, внешняя библиотека toml не требуется)

Все зависимости включаются в portable-версию с Python embeddable.

#### Структура portable-версии

После сборки в ZIP-архиве:
- `chromium-gost-updater.bat` - главный launcher
- `chromium-gost-updater.py` - основной скрипт
- `chromium-gost-updater.toml` - конфиг
- `python/` - Python embeddable 3.11+ с зависимостями
- `install-task-scheduler.ps1` - скрипт установки задачи Task Scheduler
- Документация и иконка

#### Создание установщика с NSIS (опционально)

1. Установите NSIS с https://nsis.sourceforge.io/
2. Используйте скрипт `build-windows.ps1` с параметром `-BuildType "installer"`
3. Или скомпилируйте вручную:
```powershell
makensis /DVERSION=1.0.0 /DOUTPUT=installer.exe /DDISTDIR=dist windows/chromium-gost-updater.nsi
```

Установщик автоматически:
- Установит файлы в `%LOCALAPPDATA%\chromium-gost-updater`
- Создаст ярлык в меню Пуск
- Зарегистрирует задачу Task Scheduler для автоматической проверки обновлений

## Структура файлов

- `.github/workflows/build-packages.yml` - GitHub Actions workflow для автоматической сборки
- `debian/` - файлы для сборки DEB пакета
  - `control` - метаданные пакета и зависимости
  - `changelog` - история изменений
  - `rules` - правила сборки
  - `postinst` - скрипт после установки
  - `postrm` - скрипт после удаления
- `rpm/chromium-gost-updater.spec` - спецификация для сборки RPM пакета
- `chromium-gost-updater.service.system` - systemd service файл для системной установки
- `windows/` - файлы для сборки Windows-дистрибутива
  - `build-windows.ps1` - PowerShell скрипт для сборки portable-версии
  - `install-task-scheduler.ps1` - скрипт установки задачи Task Scheduler
  - `chromium-gost-updater.nsi` - NSIS скрипт для установщика
  - `requirements-windows.txt` - зависимости Python для Windows (PySide6)

## Установка собранных пакетов

### DEB (Debian/Ubuntu)
```bash
sudo dpkg -i chromium-gost-updater_1.0.0_all.deb
sudo apt-get install -f  # Установить зависимости если нужно
```

### RPM (Fedora/RHEL/CentOS)
```bash
sudo rpm -Uvh chromium-gost-updater-1.0.0-1.noarch.rpm
```

### Windows

**Portable-версия:**
1. Скачайте ZIP-архив из релиза: `chromium-gost-updater-1.0.0-windows.zip`
2. Распакуйте архив в любую директорию
3. Запустите `chromium-gost-updater.bat`

**Установщик (рекомендуется):**
1. Скачайте и запустите `chromium-gost-updater-1.0.0-setup.exe`
2. Следуйте инструкциям установщика
3. Приложение будет установлено в `%LOCALAPPDATA%\chromium-gost-updater`
4. Автоматически создастся задача Task Scheduler для проверки обновлений каждый час

**Важно:** Все зависимости (Python 3.11+ embeddable, Qt/PySide6) включены в portable-версию, дополнительная установка не требуется.

После установки пакета:
- Скрипты будут в `/usr/bin/`
- Данные в `/usr/share/chromium-gost-updater/`
- Systemd unit файлы в `/usr/lib/systemd/user/`
- Конфиг можно создать в `/etc/chromium-gost-updater.toml` (системный) или `~/.chromium-gost-updater.toml` (пользовательский)

## Примечания

- Версия пакета берётся из тега (v1.0.0 → 1.0.0) или генерируется автоматически
- Для системной установки используется `/usr/bin/`, для пользовательской - `~/.local/bin/`
- Systemd service файл автоматически адаптируется под тип установки


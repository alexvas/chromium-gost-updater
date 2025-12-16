# Инструкция по сборке пакетов

## Автоматическая сборка через GitHub Actions

### Создание релиза

1. Создайте тег для релиза:
   ```bash
   git tag -a v1.0.0 -m "Release version 1.0.0"
   git push origin v1.0.0
   ```

2. GitHub Actions автоматически:
   - Соберёт DEB и RPM пакеты
   - Создаст Release на GitHub
   - Загрузит пакеты в GitHub Releases

3. Пользователи смогут скачать пакеты со страницы Releases

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

После установки пакета:
- Скрипты будут в `/usr/bin/`
- Данные в `/usr/share/chromium-gost-updater/`
- Systemd unit файлы в `/usr/lib/systemd/user/`
- Конфиг можно создать в `/etc/chromium-gost-updater.toml` (системный) или `~/.chromium-gost-updater.toml` (пользовательский)

## Примечания

- Версия пакета берётся из тега (v1.0.0 → 1.0.0) или генерируется автоматически
- Для системной установки используется `/usr/bin/`, для пользовательской - `~/.local/bin/`
- Systemd service файл автоматически адаптируется под тип установки


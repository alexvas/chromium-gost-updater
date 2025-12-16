%define name chromium-gost-updater
%define version %{_version}
%define release 1

Summary: Автоматический обновлятор браузера Chromium Gost сборки КриптоПро
Name: %{name}
Version: %{version}
Release: %{release}%{?dist}
License: Apache-2.0
Group: Applications/System
Source0: %{name}-%{version}.tar.gz
URL: https://github.com/alexvas/chromium-gost-updater
BuildArch: noarch

Requires: python3
Requires: python3-gi >= 3.0 or python3-pyside6 or python3-pyqt5
Requires: policykit
Requires: systemd

%description
Питоновский скрипт приложения системного трея, который проверяет наличие обновлений 
и позволяет установить их через GUI-диалог.

Возможности:
 - Автоматическая проверка обновлений через systemd timer
 - Иконка в трее (поддержка KDE/GNOME/Unity)
 - Установка пакета по клику

%prep
%setup -q

%build
# Нет компиляции, только скрипты

%install
mkdir -p %{buildroot}/usr/bin
mkdir -p %{buildroot}/usr/lib/systemd/user
mkdir -p %{buildroot}/usr/share/chromium-gost-updater
mkdir -p %{buildroot}/usr/share/doc/%{name}

install -m 755 chromium-gost-updater.py %{buildroot}/usr/bin/
install -m 755 chromium-gost-updater-wrapper.sh %{buildroot}/usr/bin/

# Используем системный вариант service файла с путем /usr/bin/
# Если файл существует, используем его, иначе используем обычный и заменяем путь
if [ -f chromium-gost-updater.service.system ]; then
    install -m 644 chromium-gost-updater.service.system %{buildroot}/usr/lib/systemd/user/chromium-gost-updater.service
else
    sed 's|%h/.local/bin/chromium-gost-updater-wrapper.sh|/usr/bin/chromium-gost-updater-wrapper.sh|' chromium-gost-updater.service > %{buildroot}/usr/lib/systemd/user/chromium-gost-updater.service
    chmod 644 %{buildroot}/usr/lib/systemd/user/chromium-gost-updater.service
fi
install -m 644 chromium-gost-remote.timer %{buildroot}/usr/lib/systemd/user/

install -m 644 chromium-gost-logo.png %{buildroot}/usr/share/chromium-gost-updater/
install -m 644 chromium-gost-updater.toml %{buildroot}/usr/share/chromium-gost-updater/

install -m 644 README.md %{buildroot}/usr/share/doc/%{name}/
install -m 644 NOTICE.txt %{buildroot}/usr/share/doc/%{name}/

%post
# Выполняем команды systemctl для всех активных пользователей
if command -v systemctl >/dev/null 2>&1 && command -v su >/dev/null 2>&1; then
    # Метод 1: Если установка через sudo, используем SUDO_USER (самый надёжный)
    if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
        su - "$SUDO_USER" -c "systemctl --user daemon-reload" 2>/dev/null || true
        su - "$SUDO_USER" -c "systemctl --user enable chromium-gost-remote.timer" 2>/dev/null || true
        su - "$SUDO_USER" -c "systemctl --user start chromium-gost-remote.timer" 2>/dev/null || true
    fi
    
    # Метод 2: Используем loginctl для определения активных пользовательских сессий
    if command -v loginctl >/dev/null 2>&1 && command -v getent >/dev/null 2>&1; then
        loginctl list-sessions --no-legend 2>/dev/null | while read -r session uid user rest; do
            username=$(getent passwd "$uid" 2>/dev/null | cut -d: -f1)
            if [ -n "$username" ] && [ "$username" != "root" ] && [ "$uid" -ge 1000 ]; then
                su - "$username" -c "systemctl --user daemon-reload" 2>/dev/null || true
                su - "$username" -c "systemctl --user enable chromium-gost-remote.timer" 2>/dev/null || true
                su - "$username" -c "systemctl --user start chromium-gost-remote.timer" 2>/dev/null || true
            fi
        done
    fi
    
    # Метод 3: Перебираем всех пользователей с активными systemd user сессиями
    if command -v getent >/dev/null 2>&1; then
        getent passwd | while IFS=: read -r username _ uid _ _ home _; do
            if [ "$uid" -ge 1000 ] && [ "$username" != "root" ] && [ -n "$home" ]; then
                if [ -S "/run/user/$uid/systemd/private" ] 2>/dev/null; then
                    su - "$username" -c "systemctl --user daemon-reload" 2>/dev/null || true
                    su - "$username" -c "systemctl --user enable chromium-gost-remote.timer" 2>/dev/null || true
                    su - "$username" -c "systemctl --user start chromium-gost-remote.timer" 2>/dev/null || true
                fi
            fi
        done
    fi
fi

%postun
# Выполняем daemon-reload для всех активных пользователей
if command -v systemctl >/dev/null 2>&1 && command -v su >/dev/null 2>&1; then
    # Метод 1: Если удаление через sudo, используем SUDO_USER
    if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
        su - "$SUDO_USER" -c "systemctl --user daemon-reload" 2>/dev/null || true
    fi
    
    # Метод 2: Используем loginctl для определения активных пользовательских сессий
    if command -v loginctl >/dev/null 2>&1 && command -v getent >/dev/null 2>&1; then
        loginctl list-sessions --no-legend 2>/dev/null | while read -r session uid user rest; do
            username=$(getent passwd "$uid" 2>/dev/null | cut -d: -f1)
            if [ -n "$username" ] && [ "$username" != "root" ] && [ "$uid" -ge 1000 ]; then
                su - "$username" -c "systemctl --user daemon-reload" 2>/dev/null || true
            fi
        done
    fi
    
    # Метод 3: Перебираем всех пользователей с активными systemd user сессиями
    if command -v getent >/dev/null 2>&1; then
        getent passwd | while IFS=: read -r username _ uid _ _ home _; do
            if [ "$uid" -ge 1000 ] && [ "$username" != "root" ] && [ -n "$home" ]; then
                if [ -S "/run/user/$uid/systemd/private" ] 2>/dev/null; then
                    su - "$username" -c "systemctl --user daemon-reload" 2>/dev/null || true
                fi
            fi
        done
    fi
fi

%files
%defattr(-,root,root,-)
/usr/bin/chromium-gost-updater.py
/usr/bin/chromium-gost-updater-wrapper.sh
/usr/lib/systemd/user/chromium-gost-updater.service
/usr/lib/systemd/user/chromium-gost-remote.timer
/usr/share/chromium-gost-updater/chromium-gost-logo.png
/usr/share/chromium-gost-updater/chromium-gost-updater.toml
/usr/share/doc/%{name}/README.md
/usr/share/doc/%{name}/NOTICE.txt

%changelog
* Tue Dec 16 2026 Обновлятор Chromium Gost <a.a.vasiljev+cgu@yandex.ru> - %{version}-%{release}
- Начальный релиз


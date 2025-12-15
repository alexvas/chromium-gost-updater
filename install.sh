#!/bin/bash
# User-space installation script (no sudo required)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_HOME="$HOME"

echo "Устанавливаем chromium-gost-updater в пользовательское пространство..."

# Paths
LOCAL_BIN="$USER_HOME/.local/bin"
LOCAL_SHARE="$USER_HOME/.local/share/chromium-gost-updater"
SYSTEMD_USER="$USER_HOME/.config/systemd/user"

# Ensure directories exist
mkdir -p "$LOCAL_BIN"
mkdir -p "$LOCAL_SHARE"
mkdir -p "$SYSTEMD_USER"

# Function to check if a Python module is available
check_python_module() {
    python3 -c "import $1" 2>/dev/null
}

# Function to check if GUI backend is available (Qt or AppIndicator)
check_gui_backend() {
    # Check for Qt backends
    python3 -c "from PySide6.QtWidgets import QApplication" 2>/dev/null || \
    python3 -c "from PyQt5.QtWidgets import QApplication" 2>/dev/null || \
    # Check for AppIndicator (Unity)
    python3 -c "import gi; gi.require_version('AppIndicator3', '0.1'); from gi.repository import AppIndicator3" 2>/dev/null
}

# Function to check if toml is available (either built-in tomllib or pip toml)
check_toml() {
    python3 -c "import tomllib" 2>/dev/null || \
    python3 -c "import toml" 2>/dev/null
}

# Check if dependencies are available in system Python
echo "Проверяем зависимости..."
HAS_GUI=$(check_gui_backend && echo "yes" || echo "no")
HAS_TOML=$(check_toml && echo "yes" || echo "no")

if [ "$HAS_GUI" = "yes" ] && [ "$HAS_TOML" = "yes" ]; then
    echo "  ✓ Все зависимости найдены в системном Python"
    USE_VENV=false
else
    echo "  ⚠ Некоторые зависимости отсутствуют, будет использоваться виртуальное окружение"
    USE_VENV=true
fi

# Copy files
echo "Копируем исполняемые файлы..."
WRAPPER=chromium-gost-updater-wrapper.sh
MAIN_SCRIPT=chromium-gost-updater.py
for file in "$WRAPPER" "$MAIN_SCRIPT"; do
    FROM="$SCRIPT_DIR/$file"
    TO="$LOCAL_BIN/$file"
    if [ -f "$FROM" ]; then
        cp "$FROM" "$TO"
        chmod +x "$TO"
    fi
done

CONFIG=chromium-gost-updater.toml
ICON=chromium-gost-logo.png
for file in "$CONFIG" "$ICON"; do
    FROM="$SCRIPT_DIR/$file"
    TO="$LOCAL_SHARE/$file"
    if [ -f "$FROM" ]; then
        cp "$FROM" "$TO"
    fi
done

# Setup virtual environment if needed
if [ "$USE_VENV" = "true" ]; then
    VENV_DIR="$LOCAL_SHARE/venv"
    echo "Настраиваем виртуальное окружение..."
    
    # Check if python3-venv is available
    if ! python3 -m venv --help >/dev/null 2>&1; then
        echo "Ошибка: python3-venv не установлен" >&2
        echo "Установите его с помощью: sudo apt install python3-venv python3-full" >&2
        exit 1
    fi
    
    # Create venv if it doesn't exist
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
        echo "  ✓ Виртуальное окружение создано"
    else
        echo "  ✓ Виртуальное окружение уже существует"
    fi
    
    # Install dependencies
    echo "Устанавливаем зависимости в виртуальном окружении..."
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    
    # Try to install GUI backend dependencies
    INSTALLED_GUI=false
    
    # Check if system libraries for AppIndicator are available
    # (needed for PyGObject to work even if installed via pip)
    HAS_SYSTEM_GTK=false
    if command -v pkg-config >/dev/null 2>&1; then
        if pkg-config --exists gtk+-3.0 2>/dev/null && pkg-config --exists appindicator3-0.1 2>/dev/null; then
            HAS_SYSTEM_GTK=true
        fi
    fi
    
    # Try AppIndicator first (for Unity) - install PyGObject via pip if system libs available
    if [ "$HAS_SYSTEM_GTK" = "true" ]; then
        if "$VENV_DIR/bin/pip" install --quiet PyGObject 2>/dev/null; then
            # Test if AppIndicator works
            if "$VENV_DIR/bin/python3" -c "import gi; gi.require_version('AppIndicator3', '0.1'); from gi.repository import AppIndicator3" 2>/dev/null; then
                INSTALLED_GUI=true
                echo "  ✓ PyGObject установлен через pip (AppIndicator доступен)"
            fi
        fi
    fi
    
    # Try Qt backends (for KDE or as fallback)
    if [ "$INSTALLED_GUI" = "false" ]; then
        if "$VENV_DIR/bin/pip" install --quiet PySide6 toml 2>/dev/null; then
            INSTALLED_GUI=true
            echo "  ✓ PySide6 установлен"
        elif "$VENV_DIR/bin/pip" install --quiet PyQt5 toml 2>/dev/null; then
            INSTALLED_GUI=true
            echo "  ✓ PyQt5 установлен"
        fi
    fi
    
    # Check if AppIndicator is available via system python3-gi (fallback)
    if [ "$INSTALLED_GUI" = "false" ] && python3 -c "import gi; gi.require_version('AppIndicator3', '0.1')" 2>/dev/null; then
        echo "  ℹ AppIndicator доступен через системный python3-gi (но не в venv)"
        echo "     Для использования в venv установите системные библиотеки:" >&2
        echo "     sudo apt install libgtk-3-dev libappindicator3-dev gir1.2-appindicator3-0.1" >&2
    fi
    
    if [ "$INSTALLED_GUI" = "false" ]; then
        echo "Предупреждение: GUI backend не установлен в venv." >&2
        echo "  Для Unity: установите системные библиотеки, затем PyGObject будет установлен автоматически:" >&2
        echo "    sudo apt install libgtk-3-dev libappindicator3-dev gir1.2-appindicator3-0.1" >&2
        echo "  Или используйте системный python3-gi (без venv)" >&2
        echo "  Для KDE: pip install PySide6 или PyQt5" >&2
    fi
    
    # Install toml if not already installed
    if ! "$VENV_DIR/bin/python3" -c "import toml" 2>/dev/null && ! "$VENV_DIR/bin/python3" -c "import tomllib" 2>/dev/null; then
        "$VENV_DIR/bin/pip" install --quiet toml || {
            echo "Ошибка: не удалось установить toml" >&2
            exit 1
        }
    fi
    echo "  ✓ Зависимости установлены"
fi

# Copy systemd files
for file in chromium-gost-updater.service chromium-gost-local.timer chromium-gost-remote.timer; do
    FROM="$SCRIPT_DIR/$file"
    TO="$SYSTEMD_USER/$file"
    if [ -f "$FROM" ]; then
        cp "$FROM" "$TO"
    fi
done

# Setup systemd
echo "Настраиваем сервисы systemd на уровне пользователя..."
systemctl --user daemon-reload

# Enable and start timers
if [ -f "$SYSTEMD_USER/chromium-gost-remote.timer" ]; then
    systemctl --user enable --now chromium-gost-remote.timer
    echo "  ✓ chromium-gost-remote.timer включен и запущен"
fi

if [ -f "$SYSTEMD_USER/chromium-gost-local.timer" ]; then
    systemctl --user enable --now chromium-gost-local.timer
    echo "  ✓ chromium-gost-local.timer включен и запущен"
fi

echo ""
echo "Установка завершена!"
echo ""
echo "Установлено в:"
echo "  Скрипт: $LOCAL_BIN/chromium-gost-updater.py"
echo "  Данные:   $LOCAL_SHARE/"
echo "  Systemd: $SYSTEMD_USER/"
echo ""
echo "Для запуска вручную:"
echo "  $LOCAL_BIN/chromium-gost-updater-wrapper.sh"


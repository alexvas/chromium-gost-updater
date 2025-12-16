#!/bin/bash
# Wrapper script for chromium-gost-updater.py
# Checks for dependencies and uses venv if needed

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_HOME="$HOME"

# Определяем путь к скрипту: сначала системные пути (для deb/rpm), затем пользовательские
if [ -f "/usr/bin/chromium-gost-updater.py" ]; then
    PYTHON_SCRIPT="/usr/bin/chromium-gost-updater.py"
elif [ -f "$USER_HOME/.local/bin/chromium-gost-updater.py" ]; then
    PYTHON_SCRIPT="$USER_HOME/.local/bin/chromium-gost-updater.py"
else
    PYTHON_SCRIPT="$SCRIPT_DIR/chromium-gost-updater.py"
fi

LOCAL_SHARE="$USER_HOME/.local/share/chromium-gost-updater"
VENV_DIR="$LOCAL_SHARE/venv"

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
HAS_GUI=$(check_gui_backend && echo "yes" || echo "no")
HAS_TOML=$(check_toml && echo "yes" || echo "no")

# If both dependencies are available, use system Python
if [ "$HAS_GUI" = "yes" ] && [ "$HAS_TOML" = "yes" ]; then
    exec python3 "$PYTHON_SCRIPT" "$@"
fi

# Otherwise, use venv
if [ ! -d "$VENV_DIR" ]; then
    echo "Error: Virtual environment not found at $VENV_DIR" >&2
    echo "Please run install.sh to set up the environment" >&2
    exit 1
fi

# Activate venv and run script
exec "$VENV_DIR/bin/python3" "$PYTHON_SCRIPT" "$@"


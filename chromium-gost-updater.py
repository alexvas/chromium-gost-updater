#!/usr/bin/env python3
"""
chromium-gost-updater.py

Python tray updater for Chromium Gost (single-file).
Поддерживает KDE (через PySide6/PyQt5), GNOME и Ubuntu Unity (через AppIndicator).

Функции:
 - проверка локальной версии (apt-cache show / rpm -q chromium-gost-stable)
 - проверка удалённой версии (https://update.cryptopro.ru/get/chromium-gost/version)
 - сравнение (учёт "-<debrev>" у локальной версии)
 - показывать tray-иконку и диалог Обновить / Игнорировать / Напомнить позже
   - KDE: через PySide6 / PyQt5 (QSystemTrayIcon)
   - GNOME/Unity: через AppIndicator3 (python3-gi)
     Примечание: для GNOME требуется расширение AppIndicator Support или TopIcons Plus
 - скачивание .deb/.rpm с повторными попытками
 - установка через pkexec (dpkg -i для deb, rpm -Uvh для rpm)
 - хранение состояния (ignored versions, remind timestamps) в ~/.cache/chromium_gost_updater/state.json
 - конфиг в ~/.chromium-gost-updater.toml

Ограничения/заметки внутри кода.
"""

import sys
import os
import subprocess
import shlex
import time
import threading
import json
import atexit
import random
import webbrowser
from datetime import datetime
from urllib.request import urlopen, Request
from pathlib import Path
from itertools import chain


# Detect Desktop Environment
def detect_desktop_environment() -> str:
    """Detect current desktop environment."""
    # Check XDG_CURRENT_DESKTOP first
    de = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "unity" in de:
        return "unity"
    if "gnome" in de:
        return "gnome"
    if "kde" in de or "plasma" in de:
        return "kde"

    # Check XDG_SESSION_DESKTOP as fallback
    session_de = os.environ.get("XDG_SESSION_DESKTOP", "").lower()
    if "unity" in session_de:
        return "unity"
    if "gnome" in session_de:
        return "gnome"
    if "kde" in session_de or "plasma" in session_de:
        return "kde"

    # Check for GNOME-specific processes
    try:
        result = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "gnome-shell"],
            capture_output=True,
            timeout=1,
        )
        if result.returncode == 0:
            return "gnome"
    except Exception:
        pass

    # Check for Unity-specific processes
    try:
        result = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "unity-panel-service"],
            capture_output=True,
            timeout=1,
        )
        if result.returncode == 0:
            return "unity"
    except Exception:
        pass

    # Check for Unity indicator processes
    try:
        result = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-l", "unity"],
            capture_output=True,
            timeout=1,
        )
        if result.returncode == 0:
            return "unity"
    except Exception:
        pass

    return "unknown"


DESKTOP_ENV = detect_desktop_environment()

APPNAME = "Chromium Gost Updater"
PACKAGE_NAME = "chromium-gost-stable"
HOME = Path.home()
CACHE_DIR = HOME / ".cache" / "chromium_gost_updater"
STATE_FILE = CACHE_DIR / "state.json"
LOCK_FILE = CACHE_DIR / "gui_instance.lock"
LOG_FILE = Path("/tmp/chromium-gost-updater.log")


# Генерируем случайный положительный long идентификатор сессии
SESSION_ID = random.randint(1, 2**63 - 1)


def log_debug(message: str) -> None:
    """Записать отладочное сообщение в лог-файл с идентификатором сессии и временной отметкой."""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        log_entry = f"[{timestamp}] [{SESSION_ID}] {message}\n"
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception:
        pass  # Игнорируем ошибки записи в лог


# Дата-класс версий: локальная и удалённая версии
class PackageVersions:
    def __init__(self, local: str | None = None, remote: str | None = None):
        self.__local = local
        self.__remote = remote.strip() if remote else None

    def local(self) -> str | None:
        return self.__local

    def remote(self) -> str | None:
        return self.__remote

    def set_local(self, local: str | None) -> None:
        self.__local = local

    def set_remote(self, remote: str | None) -> None:
        self.__remote = remote.strip() if remote else None

    def differ(self) -> bool:
        if not self.__remote:
            log_debug(f"differ: remote is empty, returning False")
            return False
        if not self.__local:
            log_debug(f"differ: local is empty, returning True")
            return True
        result = self.__local != self.__remote
        log_debug(
            f"differ: local={self.__local}, remote={self.__remote}, differ={result}"
        )
        return result

    def __str__(self) -> str:
        return f"PackageVersions(local={self.__local!r}, remote={self.__remote!r}, differ={self.differ()!r})"

    def __repr__(self) -> str:
        return self.__str__()


# -------------------------
# Config начало
# -------------------------


class Config:
    """
    Настройки скрипта, завязанные на файл конфигурации, управляемый пользователем.
    """

    __delegate: dict[str, dict[str, str | int]] = {}
    # Проверяем системный конфиг, затем пользовательский
    __CONFIG_PATH = (
        Path("/etc/chromium-gost-updater.toml")
        if Path("/etc/chromium-gost-updater.toml").exists()
        else HOME / ".chromium-gost-updater.toml"
    )
    __TMP_DIR = "/tmp/chromium-gost-updater"

    def __init__(self):
        self.__load_config()
        tmp_dir = self.tmp_dir()
        tmp_dir.mkdir(parents=True, exist_ok=True)

    def __load_config(self) -> None:
        """
        Загружаем пользовательский конфиг, если он имеется
        """
        # Try tomllib (py3.11), else toml (pip), else None
        try:
            import tomllib as toml_loader  # Python 3.11+
        except Exception:
            try:
                import toml as toml_loader  # type: ignore[import] -- pip install toml
            except Exception:
                return

        if not self.__CONFIG_PATH.exists():
            return

        try:
            # tomllib (Python 3.11+) требует бинарный режим
            # toml (pip) может работать с обоими режимами, но предпочтительно бинарный
            try:
                with self.__CONFIG_PATH.open("rb") as f:
                    data = toml_loader.load(f)
            except (TypeError, AttributeError):
                # Fallback для старых версий toml (pip), которые требуют текстовый режим
                with self.__CONFIG_PATH.open("r", encoding="utf-8") as f:
                    data = toml_loader.load(f)
        except Exception as e:
            print(
                "Не удалось распарсить настройки, используем настройки по умолчанию:", e
            )
            return

        self.__delegate: dict[str, dict[str, str | int]] = {}

        for k, v in data.items():
            if isinstance(v, dict):
                self.__delegate.setdefault(k, {}).update(v)

    def __int_or_default(self, section: str, key: str, default: int) -> int:
        value = self.__delegate.get(section, {}).get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def __str_or_default(self, section: str, key: str, default: str) -> str:
        value = self.__delegate.get(section, {}).get(key)
        if value is None:
            return default
        try:
            return str(value)
        except ValueError:
            return default

    def tmp_dir(self) -> Path:
        """
        Возвращаем путь к временной директории.
        """
        return Path(self.__str_or_default("paths", "tmp_dir", self.__TMP_DIR))

    def download_retries(self) -> int:
        """
        Возвращаем количество попыток загрузки пакета.
        """
        return self.__int_or_default("download", "retries", 5)

    def auth_password_attempts(self) -> int:
        """
        Возвращаем количество попыток ввода пароля при установке пакета.
        """
        return self.__int_or_default("auth", "password_attempts", 3)

    def timing_check_remote_interval(self) -> int:
        """
        Возвращаем период проверки обновлений в секундах.
        """
        return self.__int_or_default("timing", "check_remote_interval", 3600)


CONFIG: Config = Config()

# -------------------------
# Config конец
# -------------------------


# -------------------------
# state functions
# -------------------------


def load_state() -> dict:
    if not STATE_FILE.parent.exists():
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# -------------------------
# Менеджер пакетов: начало
# -------------------------


class PackageManager:
    """
    Базовый класс для установки пакета дистрибутива браузера (дебки или рпмки)
    и проверки версии установленного пакета.
    """

    def _create_install_command(self, package_path: str) -> list[str]:
        """Сформировать команду установки пакета."""
        raise NotImplementedError

    @classmethod
    def get_local_version(cls) -> str | None:
        """
        Найти версию установленного пакета.
        Возвращаем текстовую строку с версией (например, "142.0.7444.176-1") или None.
        """
        raise NotImplementedError

    @classmethod
    def _normalize_local_version(cls, input: str | None) -> str | None:
        """
        Удаляем части package revision/release после первого дефиса, если такие имеются:
        "142.0.7444.176-1" -> "142.0.7444.176" (deb)
        "142.0.7444.176-1.el8" -> "142.0.7444.176" (rpm)
        """
        if not input:
            return input
        if "-" in input:
            parts = input.split("-", 1)
            return parts[0]
        return input

    def install(self, package_path: str) -> tuple[bool, str]:
        """
        Используем pkexec, чтобы запустить сформированную команду установки.
        pkexec работает в KDE, GNOME и Unity, но для показа GUI диалога требуется
        запущенный агент аутентификации Polkit (polkit-kde-authentication-agent-1
        для KDE, polkit-gnome-authentication-agent-1 для GNOME/Unity).
        Returns (success: bool, combined_output: str)
        """
        # Передаем DISPLAY и XAUTHORITY через окружение процесса, а не через команду
        # Это позволяет pkexec показать GUI диалог, но команда в диалоге будет чистой
        env = os.environ.copy()

        # Объединяем обе команды в один вызов pkexec через sh -c
        # Команда в диалоге будет: sh -c "dpkg -i ... && apt -f install -y"
        # Используем shlex.quote для безопасного экранирования пути
        cmd = ["pkexec"] + self._create_install_command(package_path)
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            output = proc.stdout + "\n" + proc.stderr
            success = proc.returncode == 0
            return success, output
        except Exception as e:
            return False, str(e)

    @classmethod
    def _check_output_for_package(cls, cmd_prefix: list[str]) -> str | None:
        """
        Запускаем команду, добавляя туда суффиксом имя пакета.
        Возвращает либо строку STDOUT в случае удачного запуска, либо None.
        """
        cmd = cmd_prefix + [PACKAGE_NAME]
        try:
            return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
        except subprocess.CalledProcessError:
            return None

    def get_extension(self) -> str:
        """
        Возвращает deb или rpm
        """
        raise NotImplementedError

    @classmethod
    def create(cls) -> "PackageManager":
        """
        Метод-фабрика для создания реализации менеджера пакетов.
        """

        def __check_quietly(cmd: list[str]) -> bool:
            try:
                subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1,
                    check=True,
                )
                return True
            except (subprocess.CalledProcessError, FileNotFoundError, OSError):
                return False

        # Проверяем наличие команды Rpm.
        if __check_quietly(["rpm", "--version"]):
            # Проверяем, установлен ли браузер через Rpm:
            rpm = RpmPackageManager()
            if rpm.get_local_version():
                return rpm

            # Команда rpm имеется, однако rpm-пакет с браузером не установлен.
            # Проверяем подсистему пакетов Apt, чисто на всякий.
            deb = DebPackageManager()
            if deb.get_local_version():
                return deb

            return rpm

        if __check_quietly(["dpkg", "--version"]):
            return DebPackageManager()

        raise Exception("Не найдено ни Apt, ни Rpm.")


class DebPackageManager(PackageManager):
    """
    Работает с дистрибутивами, основанными на Deb-пакетах
    """

    def _create_install_command(self, package_path: str) -> list[str]:
        quoted_path = shlex.quote(package_path)
        cmd = f"dpkg -i {quoted_path} && apt -f install -y"
        return ["sh", "-c", cmd]

    @classmethod
    def get_local_version(cls) -> str | None:
        """
        Находим версию установленного пакета с помощью apt-cache
        Возвращаем текстовую строку с версией (например, "142.0.7444.176-1") или None.
        """

        out = cls._check_output_for_package(["apt-cache", "show"])
        if not out:
            return None

        for line in out.splitlines():
            if line.startswith("Version:"):
                found = line.split(":", 1)[1].strip()
                normalized = cls._normalize_local_version(found)
                return normalized

        return None

    def get_extension(self) -> str:
        return "deb"


class RpmPackageManager(PackageManager):
    """
    Работает с дистрибутивами, основанными на пакетах RPM
    """

    def _create_install_command(self, package_path: str) -> list[str]:
        return ["rpm", "-Uvh", package_path]

    @classmethod
    def get_local_version(cls) -> str | None:
        """
        Пробуем rpm -q <ИМЯ-ПАКЕТА> и парсим версию
        Возвращаем текстовую строку с версией (например, "142.0.7444.176-1.el8") или None.
        """

        out = cls._check_output_for_package(["rpm", "-q"])
        if not out:
            return None

        # Получили результат в формате: chromium-gost-stable-142.0.7444.176-1.el8.x86_64
        # Формат: <package-name>-<version>-<release>.<arch>
        # Выделяем часть version-release (всё, что между именем пакета и архитектурой)
        line = out.strip()
        # Убедились, что префикс совпадает
        if line.startswith(PACKAGE_NAME + "-"):
            # Отрезаем префикс и дефис
            version_part = line[len(PACKAGE_NAME) + 1 :]
            # Убираем суффикс с архитектурой (последняя точка и всё, что за ней)
            if "." in version_part:
                # Находим последнюю точку (перед архитектурой)
                last_dot_idx = version_part.rfind(".")
                if last_dot_idx > 0:
                    version_part = version_part[:last_dot_idx]
            normalized = cls._normalize_local_version(version_part)
            return normalized

        return None

    def get_extension(self) -> str:
        return "rpm"


PACKAGE_MANAGER: PackageManager = PackageManager.create()

# -------------------------
# Менеджер пакетов: конец
# -------------------------

# -------------------------
# Загрузчик пакетов: начало
# -------------------------


class Downloader:

    VERSION_CHECK_URL = "https://update.cryptopro.ru/get/chromium-gost/version"
    PACKAGE_DOWNLOAD_URL_TEMPLATE = "https://update.cryptopro.ru/chromium-gost/chromium-gost-{{version}}-linux-amd64.{{extension}}"
    HEADERS = {"User-Agent": "chromium-gost-updater/1.0"}

    def get_remote_version(self) -> str | None:
        """
        Запрашиваем удалённую версию (строка вида "142.0.7444.176")
        Возвращаем строку с версией или None, если не удалось.
        """
        version_check_timeout = 15
        try:
            req = Request(self.VERSION_CHECK_URL, headers=self.HEADERS)
            with urlopen(req, timeout=version_check_timeout) as r:
                text = r.read().decode("utf-8").strip()
                return text
        except Exception:
            return None

    def get_retries_count(self) -> int:
        return CONFIG.download_retries()

    def download_package(self, version: str) -> Path | None:
        """
        Загружаем пакет (.deb or .rpm) с повторами.
        Возвращаем путь к загруженному файлу или None, если не удалось.
        """
        out_dir = CONFIG.tmp_dir()
        ext = PACKAGE_MANAGER.get_extension()
        url = self.PACKAGE_DOWNLOAD_URL_TEMPLATE.replace(
            "{{version}}", version
        ).replace("{{extension}}", ext)
        filename = url.split("/")[-1]
        dest = out_dir / filename
        attempt = 0
        retries = self.get_retries_count()
        while attempt < retries:
            attempt += 1
            try:
                return self.__do_download_package(url, dest)
            except Exception:
                time.sleep(2 ** min(attempt, 5))
        return None

    def __do_download_package(self, url: str, dest: Path) -> Path | None:
        req = Request(url, headers=self.HEADERS)
        with urlopen(req, timeout=30) as r, open(dest, "wb") as f:
            f.write(r.read())
        # basic sanity check
        if dest.exists() and dest.stat().st_size > 1024:
            return dest
        else:
            self.__unlink(dest)
            return None

    def __unlink(self, dest: Path) -> None:
        try:
            dest.unlink()
        except Exception:
            pass


DOWNLOADER: Downloader = Downloader()

# -------------------------
# Загрузчик пакетов: конец
# -------------------------


def cleanup_old_package_files(keep_current: str | None = None) -> None:
    """
    Remove old package files (.deb or .rpm) from tmp_dir, optionally keeping the current one.
    """
    tmp_dir = CONFIG.tmp_dir()
    if not tmp_dir.exists():
        return
    try:
        old_packages = chain(
            tmp_dir.glob("chromium-gost-*.deb"), tmp_dir.glob("chromium-gost-*.rpm")
        )
    except Exception:
        return

    current_file = str(Path(keep_current).resolve()) if keep_current else None
    for pkg_file in old_packages:
        if keep_current and str(pkg_file.resolve()) == current_file:
            continue
        try:
            pkg_file.unlink()
        except Exception:
            pass


def is_gui_running() -> bool:
    """
    Check if GUI instance is already running by checking lock file and process.
    """
    if not LOCK_FILE.exists():
        return False

    try:
        # Check if process from lock file is still running
        pid = int(LOCK_FILE.read_text().strip())
        os.kill(pid, 0)  # Check if process exists (doesn't kill, just checks)
        return True
    except (OSError, ValueError):
        # Process doesn't exist, remove stale lock file
        try:
            LOCK_FILE.unlink()
        except Exception:
            pass
        return False


def launch_gui_version(script_path: str | None = None) -> bool:
    """
    Launch GUI version of the script with proper DISPLAY/XAUTHORITY.
    Tries multiple methods:
    1. Check if GUI is already running (avoid duplicates)
    2. systemd-run with environment from active session
    3. Direct execution with environment variables
    """
    if script_path is None:
        script_path = sys.argv[0]

    # Check if GUI instance is already running
    if is_gui_running():
        return True

    # Try to get DISPLAY and XAUTHORITY from active user session
    display = None
    xauthority = None

    # Method 1: Check environment of systemd user session
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("DISPLAY="):
                    display = line.split("=", 1)[1]
                elif line.startswith("XAUTHORITY="):
                    xauthority = line.split("=", 1)[1]
    except Exception:
        pass

    # Method 2: Check common X11 locations
    if not display:
        try:
            x11_dir = Path("/tmp/.X11-unix")
            if x11_dir.exists():
                sockets = list(x11_dir.glob("X*"))
                if sockets:
                    display = f":{sockets[0].name[1:]}"
                    xauth_path = Path.home() / ".Xauthority"
                    if xauth_path.exists():
                        xauthority = str(xauth_path)
        except Exception:
            pass

    # Method 3: Try systemd-run with user environment
    if display:
        try:
            env_vars = {}
            if display:
                env_vars["DISPLAY"] = display
            if xauthority:
                env_vars["XAUTHORITY"] = xauthority
            if "DBUS_SESSION_BUS_ADDRESS" in os.environ:
                env_vars["DBUS_SESSION_BUS_ADDRESS"] = os.environ[
                    "DBUS_SESSION_BUS_ADDRESS"
                ]

            env_args = []
            for k, v in env_vars.items():
                env_args.extend([f"--setenv={k}={v}"])

            cmd = (
                ["systemd-run", "--user", "--collect", "--no-block"]
                + env_args
                + [script_path]
            )
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            # Note: We can't reliably get PID from systemd-run, so skip lock file
            return True
        except Exception:
            pass

    # Fallback: Try direct execution with environment
    try:
        env = os.environ.copy()
        if display:
            env["DISPLAY"] = display
        if xauthority:
            env["XAUTHORITY"] = xauthority

        proc = subprocess.Popen(
            [sys.executable, script_path],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Create lock file with PID
        try:
            LOCK_FILE.write_text(str(proc.pid))
        except Exception:
            pass
        return True
    except Exception:
        pass

    return False


# -------------------------
# Notifier: начало
# -------------------------
class Notifier:

    def notify(self, message: str, timeout: int = 5000) -> None:
        """
        Send notification via DBus (notify-send) if available, else print.
        In GUI mode, tray.showMessage should be used instead.
        """
        # Try DBus notification for headless mode
        try:
            # Use notify-send for DBus notifications (works without GUI)
            subprocess.run(
                ["notify-send", "-t", str(timeout), APPNAME, message],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        except Exception:
            # Fallback to print if notify-send fails
            print(f"[NOTIFY] {APPNAME}: {message}")


NOTIFIER = Notifier()

# -------------------------
# Notifier: конец
# -------------------------


# -------------------------
# API UpdaterApp: начало
# -------------------------

CHROMIUM_GOST_UPDATER_FORUM_URL = (
    "https://www.cryptopro.ru/forum2/default.aspx?g=posts&t=9991&find=lastpost"
)


class UpdaterApp:
    def handle_left_or_double_click(self) -> None:
        """Обработчик левого или двойного клика на tray иконке."""
        pass

    def manual_check_and_notify(self) -> None:
        """Ручная проверка обновлений и уведомление."""
        pass

    def show_forum(self) -> None:
        """Показать страничку форума в браузере ОС по умолчанию."""
        webbrowser.open(CHROMIUM_GOST_UPDATER_FORUM_URL)

    def mark_ignored(self) -> None:
        """Отметить версию как игнорируемую."""
        pass

    def set_remind_later(self) -> None:
        """Установить напоминание позже для версии."""
        pass

    def do_update(self) -> None:
        """Выполнить обновление."""
        pass


# -------------------------
# API UpdaterApp: конец
# -------------------------


# -------------------------
# GUI Backend: начало
# -------------------------


class GuiBackend:
    """Базовый класс для GUI бэкендов."""

    # Константы для диалога обновления
    DIALOG_TITLE = "Обновить Chromium Gost"
    UPDATE_BTN_TEXT = "Давай, обновляй!"
    CHANGELOG_BTN_TEXT = "А чё там поменялось?"
    IGNORE_BTN_TEXT = "Игнорировать версию"
    REMIND_BTN_TEXT = "Напомнить позже"

    def __init__(self):
        self.app = None
        self.tray = None

    def _find_icon_path(self) -> Path | None:
        """
        Найти путь к иконке приложения.
        Проверяет системные пути (для deb/rpm пакетов), затем пользовательские.
        Возвращает Path к иконке или None, если не найдена.
        """
        # Системные пути (для deb/rpm пакетов)
        system_paths = [
            Path("/usr/share/chromium-gost-updater/chromium-gost-logo.png"),
            Path("/usr/local/share/chromium-gost-updater/chromium-gost-logo.png"),
        ]
        for path in system_paths:
            if path.exists():
                return path

        # Пользовательские пути (для ручной установки)
        # Prefer local icon file chromium-gost-logo.png in current dir
        icon_path = Path.cwd() / "chromium-gost-logo.png"
        if icon_path.exists():
            return icon_path

        # Try in share directory
        share_icon = (
            Path.home()
            / ".local"
            / "share"
            / "chromium-gost-updater"
            / "chromium-gost-logo.png"
        )
        if share_icon.exists():
            return share_icon
        return None

    def _build_dialog_message(self, package_versions: PackageVersions) -> str:
        """Построить сообщение для диалога обновления."""
        return f"Обновить Chromium Gost\nс версии\n{package_versions.local() or 'не установлено'}\nна версию\n{package_versions.remote()}\n?"

    def _build_ignore_notification(self, remote: str) -> str:
        """Построить сообщение об игнорировании версии."""
        return f"Версия {remote} будет игнорироваться"

    def _build_remind_message(self, remote: str) -> str:
        """Построить сообщение о напоминании позже."""
        return f"Напомню позже о версии {remote}"

    def create_tray(self) -> None:
        """Создать tray иконку."""
        raise NotImplementedError

    def show_update_dialog(self, updater_app: UpdaterApp) -> None:
        """Показать диалог обновления."""
        raise NotImplementedError

    def show_tray_message(self, message: str, timeout: int = 3000) -> None:
        """Показать сообщение в трее."""
        raise NotImplementedError

    def show_tray_if_hidden(self) -> None:
        """Показать tray, если он скрыт."""
        raise NotImplementedError

    def quit(self) -> None:
        """Выход из приложения."""
        raise NotImplementedError

    def run_main_loop(self) -> int:
        """Запустить главный цикл приложения."""
        raise NotImplementedError

    @classmethod
    def create(cls) -> "GuiBackend":
        """Создать подходящий GUI бэкенд на основе доступных библиотек."""
        # Try AppIndicator for Unity and GNOME (preferred for these DEs)
        if DESKTOP_ENV in ("unity", "gnome"):
            try:
                import gi

                gi.require_version("AppIndicator3", "0.1")
                gi.require_version("Gtk", "3.0")
                from gi.repository import AppIndicator3, Gtk, GLib

                backend = AppIndicatorGuiBackend()
                return backend
            except Exception:
                pass

        # Try Qt backends: PySide6 then PyQt5
        try:
            # Тестируем успешность импортов. Импорты ниже нужны
            from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QMessageBox  # type: ignore[import]
            from PySide6.QtGui import QIcon  # type: ignore[import]

            backend = Pyside6GuiBackend()
            return backend
        except Exception:
            try:
                # Тестируем успешность импортов. Импорты ниже нужны
                from PyQt5.QtWidgets import (
                    QApplication,
                    QSystemTrayIcon,
                    QMenu,
                    QAction,
                    QMessageBox,
                )  # pyright: ignore[reportUnusedImport]
                from PyQt5.QtGui import QIcon  # pyright: ignore[reportUnusedImport]

                backend = Pyqt5GuiBackend()
                return backend
            except Exception:
                pass

        # Fallback to None backend
        backend = NoneGuiBackend()
        return backend


def cached_getter(cache_attr):
    """Декоратор для автоматического кэширования геттеров."""

    def decorator(func):
        def wrapper(self):
            if not hasattr(self, cache_attr):
                setattr(self, cache_attr, func(self))
            return getattr(self, cache_attr)

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator


class QtBackend(GuiBackend):
    """Базовый класс для Qt бэкендов (PySide6/PyQt5)."""

    def _get_qapplication(self):
        """Получить класс QApplication. Должен быть переопределен в дочерних классах."""
        raise NotImplementedError

    def _get_qsystemtrayicon(self):
        """Получить класс QSystemTrayIcon. Должен быть переопределен в дочерних классах."""
        raise NotImplementedError

    def _get_qmenu(self):
        """Получить класс QMenu. Должен быть переопределен в дочерних классах."""
        raise NotImplementedError

    def _get_qaction(self):
        """Получить класс QAction. Должен быть переопределен в дочерних классах."""
        raise NotImplementedError

    def _get_qicon(self):
        """Получить класс QIcon. Должен быть переопределен в дочерних классах."""
        raise NotImplementedError

    def _get_qthread(self):
        """Получить класс QThread. Должен быть переопределен в дочерних классах."""
        raise NotImplementedError

    def _get_qmessagebox(self):
        """Получить класс QMessageBox. Должен быть переопределен в дочерних классах."""
        raise NotImplementedError

    def _get_qt(self):
        """Получить модуль Qt. Должен быть переопределен в дочерних классах."""
        raise NotImplementedError

    def _get_qobject(self):
        """Получить класс QObject. Должен быть переопределен в дочерних классах."""
        raise NotImplementedError

    def _get_signal(self):
        """Получить класс Signal (или pyqtSignal). Должен быть переопределен в дочерних классах."""
        raise NotImplementedError

    def __init__(self):
        """Инициализация Qt бэкенда (создание DialogSignaler)."""
        super().__init__()
        QObject = self._get_qobject()
        Signal = self._get_signal()

        class DialogSignaler(QObject):
            """Вспомогательный QObject для сигналов Qt, позволяющий вызывать методы из других потоков."""

            show_dialog_signal = Signal()

        self.__dialog_signaler_class = DialogSignaler

    def __consider_on_tray_activated(self, updater_app: UpdaterApp, reason) -> None:
        """Обработчик активации tray иконки (только для Qt бэкендов)."""
        # left-click -> show dialog if update available (Qt only)

        QSystemTrayIcon = self._get_qsystemtrayicon()
        QtTrigger = QSystemTrayIcon.Trigger
        QtDoubleClick = QSystemTrayIcon.DoubleClick
        if reason in (QtTrigger, QtDoubleClick):
            updater_app.handle_left_or_double_click()

    def create_tray(self, updater_app: UpdaterApp) -> None:
        """Create tray icon using Qt."""
        QApplication = self._get_qapplication()
        QSystemTrayIcon = self._get_qsystemtrayicon()
        QMenu = self._get_qmenu()
        QAction = self._get_qaction()
        QIcon = self._get_qicon()

        self.app = QApplication(sys.argv)
        tray = QSystemTrayIcon(QIcon.fromTheme("applications-internet"))

        icon_path = self._find_icon_path()
        if icon_path and icon_path.exists():
            icon = QIcon(str(icon_path))
        else:
            icon = QIcon.fromTheme("chromium")
        tray.setIcon(icon)
        tray.setToolTip(APPNAME)
        menu = QMenu()
        check_action = QAction("Проверить сейчас", menu)
        forum_action = QAction("Чё там на форуме?", menu)
        quit_action = QAction("Выйти", menu)
        menu.addAction(check_action)
        menu.addAction(forum_action)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        check_action.triggered.connect(
            lambda checked=False: threading.Thread(
                target=updater_app.manual_check_and_notify, daemon=True
            ).start()
        )
        forum_action.triggered.connect(
            lambda checked=False: threading.Thread(
                target=updater_app.show_forum, daemon=True
            ).start()
        )
        quit_action.triggered.connect(lambda checked=False: self.quit())
        tray.activated.connect(
            lambda reason: self.__consider_on_tray_activated(updater_app, reason)
        )
        self.tray = tray
        tray.show()

        # Создаем QObject для сигналов, позволяющий вызывать диалог из других потоков
        self.__dialog_signaler = self.__dialog_signaler_class()
        # Подключаем сигнал к методу показа диалога
        self.__dialog_signaler.show_dialog_signal.connect(
            lambda: self.__show_update_dialog_impl(updater_app)
        )
        log_debug(f"create_tray: created dialog signaler in main thread")

    def show_update_dialog(self, updater_app: UpdaterApp) -> None:
        """Показать диалог обновления через Qt."""
        log_debug(f"show_update_dialog: called")
        # Проверяем, вызывается ли из главного потока Qt
        QThread = self._get_qthread()
        is_main_thread = QThread.currentThread() == self.app.thread()

        log_debug(f"show_update_dialog: is_main_thread={is_main_thread}")
        if is_main_thread:
            # Вызываем напрямую, так как мы в главном потоке (клик по иконке tray)
            log_debug(
                f"show_update_dialog: calling _show_update_dialog_impl directly from main thread"
            )
            self.__show_update_dialog_impl(updater_app)
        else:
            # Вызываем из главного потока через сигнал Qt
            # (попали сюда из меню "Проверить сейчас" или через автопоказ диалога для AppIndicator)
            log_debug(
                f"show_update_dialog: scheduling dialog in main thread via Qt signal"
            )
            # Используем сигнал для вызова в главном потоке
            self.__dialog_signaler.show_dialog_signal.emit()
            log_debug(f"show_update_dialog: emitted show_dialog_signal")

    def __show_update_dialog_impl(self, updater_app: UpdaterApp) -> None:
        """Внутренняя реализация показа диалога обновления через Qt."""
        log_debug(f"_show_update_dialog_impl: called")
        message = self._build_dialog_message(updater_app.current_package_versions)
        remote = updater_app.current_package_versions.remote()
        ignore_notification = self._build_ignore_notification(remote)
        remind_message = self._build_remind_message(remote)

        # Получаем классы через геттеры бэкенда
        QMessageBox = self._get_qmessagebox()
        Qt = self._get_qt()

        msg = QMessageBox()
        msg.setWindowTitle(self.DIALOG_TITLE)
        msg.setText(message)
        msg.setModal(True)  # Устанавливаем модальность
        # Устанавливаем флаги окна для правильного отображения
        msg.setWindowFlags(msg.windowFlags() | Qt.WindowStaysOnTopHint)
        update_btn = msg.addButton(self.UPDATE_BTN_TEXT, QMessageBox.AcceptRole)
        # добавляем кнопку, которая открывает страничку с changelog
        changelog_btn = msg.addButton(self.CHANGELOG_BTN_TEXT, QMessageBox.AcceptRole)
        ignore_btn = msg.addButton(self.IGNORE_BTN_TEXT, QMessageBox.DestructiveRole)
        remind_btn = msg.addButton(self.REMIND_BTN_TEXT, QMessageBox.RejectRole)
        msg.setIcon(QMessageBox.Question)
        # Активируем окно и поднимаем его поверх всех
        msg.activateWindow()
        msg.raise_()
        # Блокирующий вызов - ждет ответа пользователя
        # exec_() должен быть вызван из главного потока Qt
        log_debug(f"_show_update_dialog_impl: showing Qt dialog")
        result = msg.exec_()
        clicked = msg.clickedButton()
        log_debug(
            f"_show_update_dialog_impl: Qt dialog result={result}, clicked={clicked}"
        )
        if clicked == update_btn:
            log_debug(f"_show_update_dialog_impl: user clicked Update")
            threading.Thread(target=updater_app.do_update, daemon=True).start()
        elif clicked == ignore_btn:
            log_debug(f"_show_update_dialog_impl: user clicked Ignore")
            updater_app.mark_ignored()
            self.show_tray_message(ignore_notification)
        elif clicked == changelog_btn:
            log_debug(f"_show_update_dialog_impl: user clicked Changelog")
            updater_app.show_forum()
        else:
            log_debug(
                f"_show_update_dialog_impl: user clicked Remind Later or closed dialog"
            )
            # Напомнить позже или закрытие диалога
            updater_app.set_remind_later()
            self.show_tray_message(remind_message)

    def show_tray_message(self, message: str, timeout: int = 3000) -> None:
        """Show tray message via Qt."""
        if self.tray:
            self.tray.showMessage(APPNAME, message, timeout)

    def show_tray_if_hidden(self) -> None:
        """Показать tray, если он скрыт."""
        if self.tray and not self.tray.isVisible():
            self.tray.show()

    def quit(self) -> None:
        """Выход из приложения Qt."""
        if self.tray:
            self.tray.hide()
        if self.app:
            self.app.quit()

    def run_main_loop(self) -> int:
        """Запустить главный цикл Qt."""
        if self.app:
            return self.app.exec_()
        return 0


class Pyside6GuiBackend(QtBackend):
    """GUI бэкенд для PySide6."""

    @cached_getter("_qobject")
    def _get_qobject(self):
        """Получить класс QObject из PySide6."""
        from PySide6.QtCore import QObject  # type: ignore[import]

        return QObject

    @cached_getter("_signal")
    def _get_signal(self):
        """Получить класс Signal из PySide6."""
        from PySide6.QtCore import Signal  # type: ignore[import]

        return Signal

    @cached_getter("_qapplication")
    def _get_qapplication(self):
        """Получить класс QApplication из PySide6."""
        from PySide6.QtWidgets import QApplication  # type: ignore[import]

        return QApplication

    @cached_getter("_qsystemtrayicon")
    def _get_qsystemtrayicon(self):
        """Получить класс QSystemTrayIcon из PySide6."""
        from PySide6.QtWidgets import QSystemTrayIcon  # type: ignore[import]

        return QSystemTrayIcon

    @cached_getter("_qmenu")
    def _get_qmenu(self):
        """Получить класс QMenu из PySide6."""
        from PySide6.QtWidgets import QMenu  # type: ignore[import]

        return QMenu

    @cached_getter("_qaction")
    def _get_qaction(self):
        """Получить класс QAction из PySide6."""
        from PySide6.QtWidgets import QAction  # type: ignore[import]

        return QAction

    @cached_getter("_qicon")
    def _get_qicon(self):
        """Получить класс QIcon из PySide6."""
        from PySide6.QtGui import QIcon  # type: ignore[import]

        return QIcon

    @cached_getter("_qthread")
    def _get_qthread(self):
        """Получить класс QThread из PySide6."""
        from PySide6.QtCore import QThread  # type: ignore[import]

        return QThread

    @cached_getter("_qmessagebox")
    def _get_qmessagebox(self):
        """Получить класс QMessageBox из PySide6."""
        from PySide6.QtWidgets import QMessageBox  # type: ignore[import]

        return QMessageBox

    @cached_getter("_qt")
    def _get_qt(self):
        """Получить модуль Qt из PySide6."""
        from PySide6.QtCore import Qt  # type: ignore[import]

        return Qt


class Pyqt5GuiBackend(QtBackend):
    """GUI бэкенд для PyQt5."""

    @cached_getter("_qobject")
    def _get_qobject(self):
        """Получить класс QObject из PyQt5."""
        from PyQt5.QtCore import QObject

        return QObject

    @cached_getter("_signal")
    def _get_signal(self):
        """Получить класс pyqtSignal из PyQt5."""
        from PyQt5.QtCore import pyqtSignal as Signal

        return Signal

    @cached_getter("_qapplication")
    def _get_qapplication(self):
        """Получить класс QApplication из PyQt5."""
        from PyQt5.QtWidgets import QApplication

        return QApplication

    @cached_getter("_qsystemtrayicon")
    def _get_qsystemtrayicon(self):
        """Получить класс QSystemTrayIcon из PyQt5."""
        from PyQt5.QtWidgets import QSystemTrayIcon

        return QSystemTrayIcon

    @cached_getter("_qmenu")
    def _get_qmenu(self):
        """Получить класс QMenu из PyQt5."""
        from PyQt5.QtWidgets import QMenu

        return QMenu

    @cached_getter("_qaction")
    def _get_qaction(self):
        """Получить класс QAction из PyQt5."""
        from PyQt5.QtWidgets import QAction

        return QAction

    @cached_getter("_qicon")
    def _get_qicon(self):
        """Получить класс QIcon из PyQt5."""
        from PyQt5.QtGui import QIcon

        return QIcon

    @cached_getter("_qthread")
    def _get_qthread(self):
        """Получить класс QThread из PyQt5."""
        from PyQt5.QtCore import QThread

        return QThread

    @cached_getter("_qmessagebox")
    def _get_qmessagebox(self):
        """Получить класс QMessageBox из PyQt5."""
        from PyQt5.QtWidgets import QMessageBox

        return QMessageBox

    @cached_getter("_qt")
    def _get_qt(self):
        """Получить модуль Qt из PyQt5."""
        from PyQt5.QtCore import Qt

        return Qt


class AppIndicatorGuiBackend(GuiBackend):
    """GUI бэкенд для AppIndicator (GNOME/Unity)."""

    def __init__(self):
        super().__init__()
        self.__notify_initted = False
        # Инициализируем libnotify для показа уведомлений
        try:
            import gi

            gi.require_version("Notify", "0.7")
            from gi.repository import Notify

            if not Notify.is_initted():
                Notify.init(APPNAME)
                self.__notify_initted = True
        except Exception:
            pass

    def create_tray(self, updater_app: UpdaterApp) -> None:
        """Create tray icon using AppIndicator for Unity/GNOME."""
        import gi

        gi.require_version("AppIndicator3", "0.1")
        gi.require_version("Gtk", "3.0")
        from gi.repository import AppIndicator3, Gtk, GLib

        # Initialize GTK
        Gtk.init(sys.argv)
        self.app = Gtk.Application.new("com.chromium.gost.updater", 0)

        # Find icon path
        icon_path = self._find_icon_path()

        # Create AppIndicator
        if icon_path:
            icon = str(icon_path)
        else:
            # Use theme icon as fallback
            icon = "applications-internet"

        indicator_id = "chromium-gost-updater"
        self.tray = AppIndicator3.Indicator.new(
            indicator_id, icon, AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )

        target_status = AppIndicator3.IndicatorStatus.ACTIVE
        self.tray.set_status(target_status)

        # Create menu
        menu = Gtk.Menu()

        check_item = Gtk.MenuItem(label="Проверить сейчас")
        check_item.connect(
            "activate",
            lambda ignored_widget: threading.Thread(
                target=updater_app.manual_check_and_notify, daemon=True
            ).start(),
        )
        menu.append(check_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Выйти")
        quit_item.connect("activate", lambda ignored_widget: self.quit())
        menu.append(quit_item)

        menu.show_all()
        self.tray.set_menu(menu)

    def show_update_dialog(self, updater_app: UpdaterApp) -> None:
        """Внутренняя реализация показа диалога обновления через AppIndicator (GTK)."""
        log_debug(f"show_update_dialog: called")
        message = self._build_dialog_message(updater_app.current_package_versions)
        remote = updater_app.current_package_versions.remote()
        if not remote:
            return
        ignore_notification = self._build_ignore_notification(remote)
        remind_message = self._build_remind_message(remote)

        # For AppIndicator, use GTK dialog
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        dialog = Gtk.MessageDialog(
            parent=None,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            message_format=message,
        )
        dialog.set_title(self.DIALOG_TITLE)

        dialog.add_button(self.UPDATE_BTN_TEXT, Gtk.ResponseType.ACCEPT)
        dialog.add_button(self.CHANGELOG_BTN_TEXT, Gtk.ResponseType.HELP)
        dialog.add_button(self.IGNORE_BTN_TEXT, Gtk.ResponseType.REJECT)

        log_debug(f"show_update_dialog: showing GTK dialog for AppIndicator")
        response = dialog.run()
        dialog.destroy()
        log_debug(f"show_update_dialog: GTK dialog response={response}")

        if response == Gtk.ResponseType.ACCEPT:
            log_debug(f"show_update_dialog: user clicked Update")
            threading.Thread(target=updater_app.do_update, daemon=True).start()
        elif response == Gtk.ResponseType.HELP:
            log_debug(f"show_update_dialog: user clicked Changelog")
            updater_app.show_forum()
        elif response == Gtk.ResponseType.REJECT:
            log_debug(f"show_update_dialog: user clicked Ignore")
            updater_app.mark_ignored()
            self.show_tray_message(ignore_notification)
        else:
            log_debug(f"show_update_dialog: user clicked Remind Later or closed dialog")
            updater_app.set_remind_later()
            self.show_tray_message(remind_message)

    def show_tray_message(self, message: str, timeout: int = 3000) -> None:
        """Show tray message via libnotify for AppIndicator."""

        if self.__notify_initted:
            try:
                self.__do_show_tray_message(message, timeout)
                return
            except Exception:
                # Fallback to notify-send if libnotify fails
                pass

        # Fallback to notify-send if libnotify not available or failed
        NOTIFIER.notify(message, timeout)

    def __do_show_tray_message(self, message: str, timeout: int = 5000) -> None:
        import gi

        gi.require_version("Notify", "0.7")
        from gi.repository import Notify

        # Получаем путь к иконке для уведомления
        icon_path = self._find_icon_path()
        icon_uri = None
        if icon_path and icon_path.exists():
            icon_uri = f"file://{icon_path.resolve()}"

        notification = Notify.Notification.new(
            APPNAME, message, icon_uri if icon_uri else None
        )

        # libnotify set_timeout принимает время в миллисекундах
        # Используем timeout как есть, или специальные константы
        # Используем значение по умолчанию (обычно 5 секунд)
        timeout = 5000 if timeout < 0 else timeout
        notification.set_timeout(timeout)
        notification.show()

    def show_tray_if_hidden(self) -> None:
        """Показать tray, если он скрыт."""
        if self.tray:
            import gi

            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3

            if self.tray.get_status() == AppIndicator3.IndicatorStatus.PASSIVE:
                self.tray.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

    def quit(self) -> None:
        """Выход из приложения AppIndicator."""
        if self.tray:
            import gi

            gi.require_version("AppIndicator3", "0.1")
            gi.require_version("Gtk", "3.0")
            from gi.repository import AppIndicator3, Gtk

            self.tray.set_status(AppIndicator3.IndicatorStatus.PASSIVE)
        if self.app:
            import gi

            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk

            Gtk.main_quit()

    def run_main_loop(self) -> int:
        """Запустить главный цикл GTK."""
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        Gtk.main()
        return 0


class NoneGuiBackend(GuiBackend):
    """GUI бэкенд для headless режима (без GUI)."""

    def create_tray(self, updater_app) -> None:
        """Создать tray не поддерживается в headless режиме."""
        print(
            "GUI недоступен; запущен в headless режиме. Установите PySide6/PyQt5 или python3-gi для работы с треем."
        )

    def show_update_dialog(self, updater_app: UpdaterApp) -> None:
        """Показать диалог не поддерживается в headless режиме."""
        pass

    def show_tray_message(self, message: str, timeout: int = 3000) -> None:
        """Show message via notify-send in headless mode."""
        NOTIFIER.notify(message, timeout)

    def show_tray_if_hidden(self) -> None:
        """Показать tray не поддерживается в headless режиме."""
        pass

    def quit(self) -> None:
        """Выход из приложения в headless режиме."""
        pass

    def run_main_loop(self) -> int:
        """Главный цикл не нужен в headless режиме."""
        return 0


GUI_BACKEND: GuiBackend = GuiBackend.create()

# -------------------------
# GUI Backend: конец
# -------------------------


class UpdaterAppImpl(UpdaterApp):
    def __init__(self):
        self.state = load_state()
        self.state.setdefault("ignored_versions", [])
        self.state.setdefault("remind_at", {})
        self.current_package_versions = PackageVersions()

    def check_package_versions(self) -> PackageVersions:
        local = PACKAGE_MANAGER.get_local_version()
        self.current_package_versions.set_local(local)
        log_debug(f"check_package_versions: local={local}")

        remote = DOWNLOADER.get_remote_version()
        self.current_package_versions.set_remote(remote)
        log_debug(f"check_package_versions: remote={remote}")

        return self.current_package_versions

    def has_updates(self) -> bool:
        """
        Проверяем, есть ли обновления.
        """
        remote_version = self.current_package_versions.remote()
        log_debug(f"has_updates: remote={remote_version}")
        if not remote_version:
            log_debug("has_updates: remote_version is empty, returning False")
            return False
        if remote_version in self.state.get("ignored_versions", []):
            log_debug(
                f"has_updates: {remote_version} is in ignored_versions, returning False"
            )
            return False
        remind_at = self.state.get("remind_at", {}).get(remote_version)
        current_time = time.time()
        log_debug(f"has_updates: remind_at={remind_at}, current_time={current_time}")
        if remind_at and current_time < float(remind_at):
            log_debug(f"has_updates: remind_at not expired, returning False")
            return False
        differ = self.current_package_versions.differ()
        log_debug(f"has_updates: versions={self.current_package_versions}")
        return differ

    def mark_ignored(self) -> None:
        lst = self.state.setdefault("ignored_versions", [])
        version = self.current_package_versions.remote()
        if not version:
            return
        if version not in lst:
            lst.append(version)
        save_state(self.state)

    def set_remind_later(self) -> None:
        version = self.current_package_versions.remote()
        if not version:
            return

        after_seconds = CONFIG.timing_check_remote_interval()
        self.state.setdefault("remind_at", {})[version] = time.time() + after_seconds
        save_state(self.state)

    def cleanup_installed_version(self) -> None:
        """
        Удалить текущую установленную версию из ignored_versions и remind_at,
        так как она уже установлена и не должна быть в списках игнорирования.
        """
        local_version = self.current_package_versions.local()
        if not local_version:
            return

        log_debug(f"cleanup_installed_version: local_version={local_version}")

        # Удаляем из ignored_versions
        ignored_versions = self.state.get("ignored_versions", [])
        removed_from_ignored = False
        for version in list(ignored_versions):
            normalized_version = version
            if normalized_version == local_version:
                ignored_versions.remove(version)
                removed_from_ignored = True
                log_debug(
                    f"cleanup_installed_version: removed {version} from ignored_versions"
                )

        # Удаляем из remind_at
        remind_at = self.state.get("remind_at", {})
        removed_from_remind = False
        for version in list(remind_at.keys()):
            normalized_version = version
            if normalized_version == local_version:
                del remind_at[version]
                removed_from_remind = True
                log_debug(
                    f"cleanup_installed_version: removed {version} from remind_at"
                )

        # Сохраняем состояние только если что-то изменилось
        if removed_from_ignored or removed_from_remind:
            self.state["ignored_versions"] = ignored_versions
            self.state["remind_at"] = remind_at
            save_state(self.state)
            log_debug(f"cleanup_installed_version: state saved after cleanup")

    def create_tray(self) -> None:
        """Создать tray иконку через GUI бэкенд."""
        GUI_BACKEND.create_tray(self)

    def handle_left_or_double_click(self) -> None:
        """Обработчик левого или двойного клика на tray иконке."""
        if self.has_updates():
            self.show_update_dialog()
        else:
            GUI_BACKEND.show_tray_message("Обновлений не найдено")

    def show_update_dialog(self) -> None:
        """Показать диалог обновления через GUI бэкенд."""
        log_debug(f"show_update_dialog: called")
        GUI_BACKEND.show_update_dialog(self)

    def do_update(self) -> None:
        remote_version = self.current_package_versions.remote()
        if not remote_version:
            GUI_BACKEND.show_tray_message("Не удалось определить версию для обновления")
            return
        GUI_BACKEND.show_tray_message(f"Скачивание {remote_version}...")
        package_path = DOWNLOADER.download_package(remote_version)
        if not package_path:
            retries = DOWNLOADER.get_retries_count()
            GUI_BACKEND.show_tray_message(
                f"Не удалось скачать {remote_version} после {retries} попыток", 5000
            )
            return
        GUI_BACKEND.show_tray_message("Файл скачан, запрашиваю права администратора...")
        attempts = CONFIG.auth_password_attempts()
        for attempt in range(1, attempts + 1):
            # Determine package type by extension or package manager
            ok, out = PACKAGE_MANAGER.install(package_path)
            if ok:
                GUI_BACKEND.show_tray_message(f"Установлено {remote_version}", 5000)
                # Cleanup old package files after successful installation
                cleanup_old_package_files(keep_current=package_path)
                time.sleep(1.0)
                self.check_package_versions()
                # Очищаем только что установленную версию из ignored_versions и remind_at
                self.cleanup_installed_version()
                save_state(self.state)
                return
            else:
                if attempt < attempts:
                    GUI_BACKEND.show_tray_message(
                        f"Ошибка установки (попытка {attempt}/{attempts}). Попробуйте снова.",
                        4000,
                    )
                    time.sleep(1.5)
                else:
                    GUI_BACKEND.show_tray_message(
                        f"Установка не удалась: {out[:200]}", 8000
                    )
                    return

    def manual_check_and_notify(self) -> None:
        log_debug("manual_check_and_notify: starting manual check")
        # Показываем tray при ручной проверке, если он скрыт
        GUI_BACKEND.show_tray_if_hidden()
        package_versions = self.check_package_versions()
        log_debug(f"manual_check_and_notify: package_versions={package_versions}")
        log_debug(
            f"manual_check_and_notify: state={json.dumps(self.state, ensure_ascii=False)}"
        )
        remote = package_versions.remote()
        if not remote:
            log_debug("manual_check_and_notify: remote check failed")
            GUI_BACKEND.show_tray_message("Не удалось получить удалённую версию")
            return
        # При ручной проверке игнорируем remind_at и ignored_versions не проверяем отдельно,
        # так как пользователь явно запросил проверку
        ignored_versions = self.state.get("ignored_versions", [])
        log_debug(f"manual_check_and_notify: ignored_versions={ignored_versions}")
        differ = self.current_package_versions.differ()
        if remote in ignored_versions:
            # Версия в игнорируемых, но при ручной проверке все равно показываем диалог
            # Пользователь может передумать
            log_debug(f"manual_check_and_notify: {remote} is in ignored_versions")
            if differ:
                log_debug(
                    f"manual_check_and_notify: versions differ, showing dialog (ignored version)"
                )
                self.show_update_dialog()
            else:
                log_debug(
                    "manual_check_and_notify: versions same, showing no updates message"
                )
                GUI_BACKEND.show_tray_message("Обновлений не найдено")
        elif differ:
            # Версии различаются - показываем диалог независимо от remind_at
            log_debug(f"manual_check_and_notify: versions differ, showing dialog")
            self.show_update_dialog()
        else:
            log_debug(
                "manual_check_and_notify: versions same, showing no updates message"
            )
            GUI_BACKEND.show_tray_message("Обновлений не найдено")


def main() -> None:
    log_debug(f"=== Starting {APPNAME} ===")
    log_debug(f"Session ID: {SESSION_ID}, PID: {os.getpid()}, Args: {sys.argv}")
    # Cleanup old package files on startup
    cleanup_old_package_files()
    updater = UpdaterAppImpl()
    updater.check_package_versions()
    # Очищаем уже установленную версию из ignored_versions и remind_at
    updater.cleanup_installed_version()

    # Check if running under systemd (no DISPLAY) or check-only mode requested
    # For systemd timers, run in headless mode even if GUI is available
    has_display = os.environ.get("DISPLAY") and os.environ.get("DISPLAY") != ""
    check_only = "--check-only" in sys.argv or not has_display
    show_tray_lazily = "--show-tray-lazily" in sys.argv

    # Проверяем, доступен ли GUI бэкенд (не NoneGuiBackend)
    gui_available = not isinstance(GUI_BACKEND, NoneGuiBackend)

    if gui_available and not check_only:
        # Проверяем, есть ли обновления
        has_updates = updater.has_updates()
        log_debug(
            f"main: has_updates={has_updates}, show_tray_lazily={show_tray_lazily}"
        )

        # С флагом --show-tray-lazily показываем tray только при наличии обновлений
        # Без флага показываем tray всегда (если GUI доступен)
        if not has_updates and show_tray_lazily:
            log_debug(
                "main: No updates available and lazy mode enabled, exiting without showing tray"
            )
            return

        # Create lock file when GUI starts
        try:
            LOCK_FILE.write_text(str(os.getpid()))
        except Exception:
            pass

        # Создаём и показываем tray
        updater.create_tray()

        # Cleanup lock file on exit
        def cleanup_lock() -> None:
            try:
                if LOCK_FILE.exists():
                    LOCK_FILE.unlink()
            except Exception:
                pass

        atexit.register(cleanup_lock)

        # For AppIndicator (Unity), show dialog automatically if update is available
        if isinstance(GUI_BACKEND, AppIndicatorGuiBackend) and has_updates:
            # Show dialog after a short delay to ensure tray is ready
            def show_dialog_delayed() -> None:
                time.sleep(0.5)
                updater.show_update_dialog()

            threading.Thread(target=show_dialog_delayed, daemon=True).start()

        # Run appropriate main loop
        sys.exit(GUI_BACKEND.run_main_loop())
    else:
        # Headless mode (systemd or --check-only)
        print("Package versions:", updater.current_package_versions)
        if updater.has_updates():
            remote = updater.current_package_versions.remote()
            print("UPDATE AVAILABLE:", remote)
            # Try to launch GUI version, then send notification
            gui_launched = launch_gui_version()
            if gui_launched:
                msg = f"Доступно обновление {remote}\nGUI запущен в системном трее."
            else:
                msg = f"Доступно обновление {remote}\nЗапустите скрипт вручную для установки."
            NOTIFIER.notify(msg, 10000)
        else:
            print("No update available.")


if __name__ == "__main__":
    main()

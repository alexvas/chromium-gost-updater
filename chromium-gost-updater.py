#!/usr/bin/env python3
"""
chromium-gost-updater.py

Python tray updater for Chromium Gost (single-file).
Поддерживает KDE (через PySide6/PyQt5), GNOME и Ubuntu Unity (через AppIndicator), Windows (PySide6).

Функции:
 - проверка локальной и удалённой версии
 - автоматическое скачивание дистрибутива при наличии обновления
 - Linux: проверка .deb/.rpm через утилиту file; диалог с командой sudo apt/dnf install
 - Windows: проверка PE-инсталлера; открытие проводника в папке кэша
 - tray-иконка; при ошибке скачивания — иконка ошибки в трее
 - хранение состояния (ignored versions, remind timestamps)
 - конфиг в /etc/chromium-gost-updater.toml или ~/.chromium-gost-updater.toml

Ограничения/заметки внутри кода.
"""

import sys
import os
import re
import subprocess
import shlex
import time
import threading
import json
import atexit
import random
import webbrowser
from datetime import datetime
from email.message import Message
from urllib.request import urlopen, Request
from pathlib import Path
from itertools import chain

IS_WINDOWS = sys.platform == "win32"
MIN_ARTIFACT_SIZE = 100 * 1024
DOWNLOAD_RETRY_BASE_DELAY_SEC = 2.0


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

if IS_WINDOWS:
    _local_app_data = os.environ.get("LOCALAPPDATA")
    CACHE_DIR = (
        Path(_local_app_data) / "chromium_gost_updater"
        if _local_app_data
        else HOME / "AppData" / "Local" / "chromium_gost_updater"
    )
    LOG_FILE = Path(os.environ.get("TEMP", ".")) / "chromium-gost-updater.log"
else:
    CACHE_DIR = HOME / ".cache" / "chromium_gost_updater"
    LOG_FILE = Path("/tmp/chromium-gost-updater.log")

CACHE_PACKAGES_DIR = CACHE_DIR / "packages"
CACHE_MANIFEST_FILE = CACHE_PACKAGES_DIR / "cache.toml"
STATE_FILE = CACHE_DIR / "state.json"
LOCK_FILE = CACHE_DIR / "gui_instance.lock"

REMOTE_BASE_URL = "https://update.cryptopro.ru/get/chromium-gost"
REMOTE_VERSION_CHECK_URL = f"{REMOTE_BASE_URL}/version"
WINDOWS_INSTALLER_URL = f"{REMOTE_BASE_URL}/windows/386/installer"

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


def _path_for_display(path: Path) -> str:
    """Путь с ~ вместо домашней директории для показа пользователю."""
    try:
        resolved = path.expanduser().resolve()
        home = HOME.resolve()
        text = str(resolved)
        if text.startswith(str(home)):
            return "~" + text[len(str(home)) :]
        return text
    except Exception:
        return str(path)


def _is_html_response(content_type: str | None, data: bytes) -> bool:
    if content_type and "text/html" in content_type.lower():
        return True
    start = data[:128].lstrip().lower()
    return start.startswith(b"<") or start.startswith(b"<!doctype")


def _filename_from_content_disposition(header_value: str | None) -> str | None:
    if not header_value:
        return None
    msg = Message()
    msg["content-disposition"] = header_value
    filename = msg.get_filename()
    if filename:
        return os.path.basename(filename)
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\n]+)"?', header_value, re.I)
    if match:
        return os.path.basename(match.group(1).strip())
    return None


def validate_pe_artifact(path: Path) -> bool:
    """Проверка Windows-инсталлера: размер, MZ и базовый заголовок PE.

    Подлинность (Authenticode, хеш с сервера) не проверяется.
    """
    try:
        size = path.stat().st_size
        if size < MIN_ARTIFACT_SIZE:
            log_debug(f"validate_pe: file too small ({size} bytes)")
            return False
        with path.open("rb") as f:
            header = f.read(0x40)
        if len(header) < 0x40 or header[:2] != b"MZ":
            log_debug("validate_pe: missing MZ signature")
            return False
        e_lfanew = int.from_bytes(header[0x3C:0x40], "little")
        if e_lfanew <= 0 or e_lfanew > size - 4:
            log_debug(f"validate_pe: invalid e_lfanew={e_lfanew}")
            return False
        with path.open("rb") as f:
            f.seek(e_lfanew)
            pe_sig = f.read(4)
        if pe_sig != b"PE\x00\x00":
            log_debug("validate_pe: missing PE signature")
            return False
        return True
    except Exception as e:
        log_debug(f"validate_pe: exception: {e}")
        return False


def validate_linux_package_file(path: Path, extension: str) -> bool:
    """Проверка .deb/.rpm через утилиту file."""
    try:
        proc = subprocess.run(
            ["file", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            log_debug(f"validate_linux: file command failed: {proc.stderr[:200]}")
            return False
        output = proc.stdout.lower()
        if extension == "deb":
            ok = "debian binary package" in output
        elif extension == "rpm":
            ok = "rpm" in output
        else:
            ok = False
        if not ok:
            log_debug(f"validate_linux: unexpected file output: {proc.stdout[:200]}")
        return ok
    except Exception as e:
        log_debug(f"validate_linux: exception: {e}")
        return False


def validate_artifact(path: Path, extension: str) -> bool:
    if IS_WINDOWS:
        return validate_pe_artifact(path)
    return validate_linux_package_file(path, extension)


def open_installer_folder(package_path: Path) -> None:
    """Открыть проводник Windows с выделенным инсталлером."""
    if not IS_WINDOWS:
        return
    try:
        subprocess.run(
            ["explorer", "/select,", os.path.normpath(str(package_path.resolve()))],
            check=False,
        )
        log_debug(f"open_installer_folder: opened {package_path}")
    except Exception as e:
        log_debug(f"open_installer_folder: failed: {e}")


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
            log_debug("differ: remote is empty, returning False")
            return False
        if not self.__local:
            log_debug("differ: local is empty, returning True")
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

    def keep_cached_distributive_in_days(self) -> int:
        """
        Возвращаем количество дней, в течение которых хранить кэшированные дистрибутивы.
        """
        return self.__int_or_default("download", "keep_cached_distributive_in_days", 30)


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
    Базовый класс для проверки версии установленного пакета
    и формирования команды ручной установки (Linux).
    """

    def format_user_install_command(self, package_path: Path) -> str:
        """Команда для ручной установки скачанного дистрибутива."""
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
        if IS_WINDOWS:
            return WindowsPackageManager()

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

    def format_user_install_command(self, package_path: Path) -> str:
        abs_path = str(package_path.expanduser().resolve())
        return f"sudo apt install {shlex.quote(abs_path)}"

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

    def format_user_install_command(self, package_path: Path) -> str:
        abs_path = str(package_path.expanduser().resolve())
        return f"sudo dnf install {shlex.quote(abs_path)}"

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


class WindowsPackageManager(PackageManager):
    """Версия браузера из реестра Windows, скачивание .exe инсталлера."""

    _BEACON_PATHS = (
        r"Software\ChromiumGost\BLBeacon",
        r"Software\Chromium Gost\BLBeacon",
    )

    def format_user_install_command(self, package_path: Path) -> str:
        return _path_for_display(package_path)

    @classmethod
    def get_local_version(cls) -> str | None:
        import winreg

        for hive, subkey in (
            (winreg.HKEY_CURRENT_USER, path) for path in cls._BEACON_PATHS
        ):
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    version, _ = winreg.QueryValueEx(key, "version")
                    if version:
                        normalized = cls._normalize_local_version(str(version))
                        log_debug(
                            f"WindowsPackageManager: version from BLBeacon: {normalized}"
                        )
                        return normalized
            except OSError:
                continue

        uninstall_roots = (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
            ),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        )
        for hive, root in uninstall_roots:
            try:
                with winreg.OpenKey(hive, root) as uninstall_key:
                    for i in range(winreg.QueryInfoKey(uninstall_key)[0]):
                        try:
                            sub_name = winreg.EnumKey(uninstall_key, i)
                            with winreg.OpenKey(uninstall_key, sub_name) as subkey:
                                display_name = cls._read_reg_str(subkey, "DisplayName")
                                if not display_name or "chromium" not in display_name.lower():
                                    continue
                                if "gost" not in display_name.lower():
                                    continue
                                display_version = cls._read_reg_str(subkey, "DisplayVersion")
                                if display_version:
                                    normalized = cls._normalize_local_version(
                                        display_version
                                    )
                                    log_debug(
                                        f"WindowsPackageManager: version from Uninstall: {normalized}"
                                    )
                                    return normalized
                        except OSError:
                            continue
            except OSError:
                continue
        return None

    @staticmethod
    def _read_reg_str(key, name: str) -> str | None:
        import winreg

        try:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value) if value is not None else None
        except OSError:
            return None

    def get_extension(self) -> str:
        return "exe"


PACKAGE_MANAGER: PackageManager = PackageManager.create()

# -------------------------
# Менеджер пакетов: конец
# -------------------------

# -------------------------
# Загрузчик пакетов: начало
# -------------------------


class Downloader:

    HEADERS = {"User-Agent": "chromium-gost-updater/1.0"}

    def _get_cache_dir(self) -> Path:
        """Получить путь к директории кэша пакетов."""
        cache_dir = CACHE_PACKAGES_DIR
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _get_cache_manifest_path(self) -> Path:
        """Получить путь к файлу манифеста кэша."""
        return CACHE_MANIFEST_FILE

    def _load_cache_manifest(self) -> dict:
        """Загрузить манифест кэша из toml файла."""
        manifest_path = self._get_cache_manifest_path()
        if not manifest_path.exists():
            return {"packages": {}}

        # Try tomllib (py3.11), else toml (pip), else empty dict
        try:
            import tomllib as toml_loader  # Python 3.11+
        except Exception:
            try:
                import toml as toml_loader  # type: ignore[import] -- pip install toml
            except Exception:
                log_debug("cache: toml loader not available, returning empty manifest")
                return {"packages": {}}

        try:
            # tomllib (Python 3.11+) требует бинарный режим
            # toml (pip) может работать с обоими режимами, но предпочтительно бинарный
            try:
                with manifest_path.open("rb") as f:
                    data = toml_loader.load(f)
            except (TypeError, AttributeError):
                # Fallback для старых версий toml (pip), которые требуют текстовый режим
                with manifest_path.open("r", encoding="utf-8") as f:
                    data = toml_loader.load(f)
            return data if isinstance(data, dict) else {"packages": {}}
        except Exception as e:
            log_debug(f"cache: failed to load manifest: {e}")
            return {"packages": {}}

    def _save_cache_manifest(self, manifest: dict) -> None:
        """Сохранить манифест кэша в toml файл."""
        manifest_path = self._get_cache_manifest_path()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)

        # Try tomllib (py3.11), else toml (pip)
        try:
            import tomllib as toml_loader  # Python 3.11+  # noqa: F401

            # tomllib не имеет метода dump, используем toml
            import toml as toml_dumper  # type: ignore[import]
        except Exception:
            try:
                import toml as toml_dumper  # type: ignore[import] -- pip install toml
            except Exception:
                log_debug("cache: toml dumper not available, cannot save manifest")
                return

        try:
            with manifest_path.open("w", encoding="utf-8") as f:
                toml_dumper.dump(manifest, f)
            log_debug(f"cache: manifest saved to {manifest_path}")
        except Exception as e:
            log_debug(f"cache: failed to save manifest: {e}")

    def _resolve_cached_file(
        self, version: str, package_info: dict, extension: str
    ) -> Path | None:
        filename = package_info.get("file")
        if not filename:
            log_debug(f"cache: no filename in package info for version {version}")
            return None

        cache_dir = self._get_cache_dir()
        cached_file = cache_dir / filename

        if not cached_file.exists():
            log_debug(f"cache: file {filename} not found in cache directory")
            return None

        expected_size = package_info.get("size")
        if expected_size:
            actual_size = cached_file.stat().st_size
            if actual_size != expected_size:
                log_debug(
                    f"cache: size mismatch for {filename}: expected {expected_size}, got {actual_size}"
                )
                return None

        status = package_info.get("status")
        if status == "error":
            if self.get_failed_attempts(version) >= self.get_retries_count():
                log_debug(
                    f"cache: version {version} marked as error, download attempts exhausted"
                )
            else:
                log_debug(f"cache: version {version} marked as error in manifest")
            return None

        if status != "ok":
            if validate_artifact(cached_file, extension):
                self._register_in_cache(
                    version, filename, cached_file, "ok", failed_attempts=0
                )
                return cached_file
            failed_attempts = min(
                self.get_failed_attempts(version) + 1, self.get_retries_count()
            )
            self._register_in_cache(
                version, filename, cached_file, "error", failed_attempts=failed_attempts
            )
            return None

        if not validate_artifact(cached_file, extension):
            log_debug(f"cache: cached file {filename} failed validation")
            failed_attempts = min(
                self.get_failed_attempts(version) + 1, self.get_retries_count()
            )
            self._register_in_cache(
                version, filename, cached_file, "error", failed_attempts=failed_attempts
            )
            return None

        log_debug(f"cache: found valid cached file {filename} for version {version}")
        return cached_file

    def _check_cache(self, version: str, extension: str) -> Path | None:
        """
        Проверить наличие файла в кэше.
        Возвращает путь к файлу, если он есть и валиден (status=ok), иначе None.
        """
        manifest = self._load_cache_manifest()
        packages = manifest.get("packages", {})

        if version not in packages:
            log_debug(f"cache: version {version} not found in manifest")
            return None

        package_info = packages[version]
        if not isinstance(package_info, dict):
            log_debug(f"cache: invalid package info for version {version}")
            return None

        return self._resolve_cached_file(version, package_info, extension)

    def get_valid_cached_package(self, version: str) -> Path | None:
        """Публичная обёртка для получения валидного файла из кэша."""
        ext = PACKAGE_MANAGER.get_extension()
        return self._check_cache(version, ext)

    def cleanup_old_cache_files(self, max_age_days: int | None = None) -> None:
        """
        Удалить файлы из кэша, которые старше max_age_days дней.
        Если max_age_days не указан, используется значение из конфига.
        """
        if max_age_days is None:
            max_age_days = CONFIG.keep_cached_distributive_in_days()
        manifest = self._load_cache_manifest()
        packages = manifest.get("packages", {})
        if not packages:
            return

        cache_dir = self._get_cache_dir()
        current_time = time.time()
        max_age_seconds = max_age_days * 24 * 60 * 60
        removed_count = 0

        packages_to_remove = []
        for version, package_info in list(packages.items()):
            if not isinstance(package_info, dict):
                continue

            downloaded_at_str = package_info.get("downloaded_at")
            if not downloaded_at_str:
                # Если нет даты, считаем файл старым
                packages_to_remove.append(version)
                continue

            try:
                # Парсим дату в формате ISO (например, "2024-01-01T12:00:00")
                downloaded_at = datetime.fromisoformat(downloaded_at_str).timestamp()
                age_seconds = current_time - downloaded_at

                if age_seconds > max_age_seconds:
                    packages_to_remove.append(version)
                    log_debug(
                        f"cache: marking version {version} for removal (age: {age_seconds / 86400:.1f} days)"
                    )
            except Exception as e:
                log_debug(f"cache: failed to parse date for version {version}: {e}")
                packages_to_remove.append(version)

        # Удаляем файлы и записи из манифеста
        for version in packages_to_remove:
            package_info = packages.get(version)
            if isinstance(package_info, dict):
                filename = package_info.get("file")
                if filename:
                    file_path = cache_dir / filename
                    try:
                        if file_path.exists():
                            file_path.unlink()
                            log_debug(f"cache: removed old file {filename}")
                            removed_count += 1
                    except Exception as e:
                        log_debug(f"cache: failed to remove file {filename}: {e}")

            del packages[version]

        if packages_to_remove:
            manifest["packages"] = packages
            self._save_cache_manifest(manifest)
            log_debug(f"cache: cleanup completed, removed {removed_count} old files")

    def get_remote_version(self) -> str | None:
        """
        Запрашиваем удалённую версию (строка вида "142.0.7444.176")
        Возвращаем строку с версией или None, если не удалось.
        """
        version_check_timeout = 15
        try:
            req = Request(REMOTE_VERSION_CHECK_URL, headers=self.HEADERS)
            with urlopen(req, timeout=version_check_timeout) as r:
                text = r.read().decode("utf-8").strip()
                return text
        except Exception:
            return None

    def get_retries_count(self) -> int:
        return CONFIG.download_retries()

    def _get_manifest_entry(self, version: str) -> dict | None:
        manifest = self._load_cache_manifest()
        package_info = manifest.get("packages", {}).get(version)
        if isinstance(package_info, dict):
            return package_info
        return None

    def get_failed_attempts(self, version: str) -> int:
        entry = self._get_manifest_entry(version)
        if not entry:
            return 0
        try:
            return int(entry.get("failed_attempts", 0))
        except (TypeError, ValueError):
            return 0

    def has_exhausted_download_attempts(self, version: str) -> bool:
        return self.get_failed_attempts(version) >= self.get_retries_count()

    def _reset_failed_attempts(self, version: str) -> None:
        manifest = self._load_cache_manifest()
        packages = manifest.get("packages", {})
        if version in packages and isinstance(packages[version], dict):
            packages[version]["failed_attempts"] = 0
            manifest["packages"] = packages
            self._save_cache_manifest(manifest)

    def _get_download_target(self, version: str, ext: str) -> tuple[str, str]:
        if IS_WINDOWS:
            filename = f"chromium-gost-{version}-installer.exe"
            return WINDOWS_INSTALLER_URL, filename
        filename = f"chromium-gost-{version}-linux-amd64.{ext}"
        url = f"{REMOTE_BASE_URL}/linux/amd64/{filename}"
        return url, filename

    def get_package_filename(self, version: str) -> str:
        ext = PACKAGE_MANAGER.get_extension()
        _, filename = self._get_download_target(version, ext)
        return filename

    def download_package(self, version: str, force: bool = False) -> Path | None:
        """
        Загружаем дистрибутив с повторами.
        Сначала проверяем кэш, если файл есть и валиден (status=ok) — используем его.
        Невалидный артефакт (не deb/rpm/PE): не более get_retries_count() попыток суммарно,
        с удвоением паузы между попытками. После исчерпания лимита сервер не дёргаем.
        """
        ext = PACKAGE_MANAGER.get_extension()
        max_attempts = self.get_retries_count()

        cached_file = self._check_cache(version, ext)
        if cached_file:
            log_debug(f"cache: using cached file for version {version}")
            return cached_file

        prior_failures = self.get_failed_attempts(version)
        if not force and prior_failures >= max_attempts:
            log_debug(
                f"cache: download skipped for {version}, "
                f"failed_attempts={prior_failures} >= {max_attempts}"
            )
            return None

        if force:
            self._reset_failed_attempts(version)
            prior_failures = 0

        cache_dir = self._get_cache_dir()
        url, filename = self._get_download_target(version, ext)
        dest = cache_dir / filename

        log_debug(f"cache: downloading {version} from {url}")
        attempt = prior_failures
        delay_sec = DOWNLOAD_RETRY_BASE_DELAY_SEC

        while attempt < max_attempts:
            attempt += 1
            try:
                downloaded_file = self.__do_download_package(url, dest)
                if not downloaded_file:
                    log_debug(
                        f"cache: download attempt {attempt}/{max_attempts} "
                        f"returned no file for {version}"
                    )
                    self._register_in_cache(
                        version, filename, dest, "error", failed_attempts=attempt
                    )
                else:
                    filename = downloaded_file.name
                    if validate_artifact(downloaded_file, ext):
                        self._register_in_cache(
                            version, filename, downloaded_file, "ok", failed_attempts=0
                        )
                        return downloaded_file
                    log_debug(
                        f"cache: validation failed for {filename} "
                        f"(attempt {attempt}/{max_attempts})"
                    )
                    self._register_in_cache(
                        version, filename, downloaded_file, "error", failed_attempts=attempt
                    )
                    self.__unlink(downloaded_file)
            except Exception as e:
                log_debug(
                    f"cache: download attempt {attempt}/{max_attempts} failed: {e}"
                )
                self._register_in_cache(
                    version, filename, dest, "error", failed_attempts=attempt
                )

            if attempt < max_attempts:
                log_debug(f"cache: waiting {delay_sec:.0f}s before next download attempt")
                time.sleep(delay_sec)
                delay_sec *= 2

        return None

    def _register_in_cache(
        self,
        version: str,
        filename: str,
        file_path: Path,
        status: str,
        failed_attempts: int | None = None,
    ) -> None:
        """Зарегистрировать скачанный файл в манифесте кэша."""
        manifest = self._load_cache_manifest()
        packages = manifest.setdefault("packages", {})

        file_size = file_path.stat().st_size if file_path.exists() else 0
        downloaded_at = datetime.now().isoformat()

        entry: dict = {
            "file": filename,
            "downloaded_at": downloaded_at,
            "size": file_size,
            "status": status,
        }
        if failed_attempts is not None:
            entry["failed_attempts"] = failed_attempts

        packages[version] = entry

        manifest["packages"] = packages
        self._save_cache_manifest(manifest)
        log_debug(
            f"cache: registered version {version} status={status} "
            f"failed_attempts={failed_attempts} (file: {filename}, size: {file_size})"
        )

    def __do_download_package(self, url: str, dest: Path) -> Path | None:
        req = Request(url, headers=self.HEADERS)
        timeout = 120 if IS_WINDOWS else 60
        with urlopen(req, timeout=timeout) as r:
            content_type = r.headers.get("Content-Type")
            content_disposition = r.headers.get("Content-Disposition")
            # Допустимо читать целиком: размер артефактов ограничен и проверяется ниже.
            data = r.read()

        if _is_html_response(content_type, data):
            log_debug("cache: download returned HTML instead of package")
            return None

        if IS_WINDOWS:
            cd_name = _filename_from_content_disposition(content_disposition)
            if cd_name:
                dest = dest.parent / cd_name

        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as f:
            f.write(data)

        if dest.exists() and dest.stat().st_size >= MIN_ARTIFACT_SIZE:
            return dest
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
    Также очищает старые файлы из кэш-директории (старше месяца).
    """
    # Очистка старых файлов из кэша (используется значение из конфига)
    try:
        DOWNLOADER.cleanup_old_cache_files()
    except Exception as e:
        log_debug(f"cleanup_old_package_files: failed to cleanup cache: {e}")

    # Очистка старых файлов из tmp_dir (для обратной совместимости)
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
            log_debug(
                f"cleanup_old_package_files: removed old file from tmp_dir: {pkg_file.name}"
            )
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

    def show_install(self) -> None:
        """Показать команду установки или открыть папку с дистрибутивом."""
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

# -------------------------
# API UpdaterApp: конец
# -------------------------


# -------------------------
# GUI Backend: начало
# -------------------------


class GuiBackend:
    """Базовый класс для GUI бэкендов."""

    # Константы для диалога обновления
    DIALOG_TITLE = "Обновление Chromium Gost"
    INSTALL_DIALOG_TITLE = "Установка Chromium Gost"
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

        if IS_WINDOWS:
            win_share = (
                Path(os.environ.get("LOCALAPPDATA", ""))
                / "chromium-gost-updater"
                / "chromium-gost-logo.png"
            )
            if win_share.exists():
                return win_share
        return None

    def _build_dialog_message(self, updater_app: "UpdaterAppImpl") -> str:
        """Построить сообщение для диалога обновления."""
        remote = updater_app.current_package_versions.remote() or "?"
        package_path = updater_app.get_ready_package()
        if package_path:
            install_command = PACKAGE_MANAGER.format_user_install_command(package_path)
            return f"Имеется новая версия.\n\nУстановить:\n\n{install_command}"
        return f"Имеется новая версия {remote}.\n\nДистрибутив ещё не скачан."

    def _build_install_dialog_message(self, updater_app: "UpdaterAppImpl") -> str:
        """Построить сообщение для диалога установки."""
        package_path = updater_app.get_ready_package()
        if not package_path:
            return "Дистрибутив не скачан.\n\nСначала выполните «Проверить сейчас»."
        install_command = PACKAGE_MANAGER.format_user_install_command(package_path)
        return f"Установить:\n\n{install_command}"

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

    def show_install_dialog(self, updater_app: UpdaterApp) -> None:
        """Показать диалог с командой установки."""
        raise NotImplementedError

    def update_install_menu_visibility(self, updater_app: UpdaterApp) -> None:
        """Показать пункт «Установить», только если в кэше есть валидный дистрибутив."""
        pass

    def show_tray_message(self, message: str, timeout: int = 3000) -> None:
        """Показать сообщение в трее."""
        raise NotImplementedError

    def show_tray_if_hidden(self) -> None:
        """Показать tray, если он скрыт."""
        raise NotImplementedError

    def set_tray_error_state(self, error: bool) -> None:
        """Показать иконку ошибки в трее или восстановить обычную."""
        pass

    def quit(self) -> None:
        """Выход из приложения."""
        raise NotImplementedError

    def run_main_loop(self) -> int:
        """Запустить главный цикл приложения."""
        raise NotImplementedError

    @classmethod
    def create(cls) -> "GuiBackend":
        """Создать подходящий GUI бэкенд на основе доступных библиотек."""
        if IS_WINDOWS:
            try:
                from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QMessageBox  # type: ignore[import]  # noqa: F401
                from PySide6.QtGui import QIcon  # type: ignore[import]  # noqa: F401

                return Pyside6GuiBackend()
            except Exception:
                try:
                    from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QMessageBox  # noqa: F401
                    from PyQt5.QtGui import QIcon  # noqa: F401

                    return Pyqt5GuiBackend()
                except Exception:
                    pass
            return NoneGuiBackend()

        # Try AppIndicator for Unity and GNOME (preferred for these DEs)
        if DESKTOP_ENV in ("unity", "gnome"):
            try:
                import gi

                gi.require_version("AppIndicator3", "0.1")
                gi.require_version("Gtk", "3.0")
                from gi.repository import AppIndicator3, Gtk, GLib  # noqa: F401

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
                    QApplication,  # noqa: F401
                    QSystemTrayIcon,  # noqa: F401
                    QMenu,  # noqa: F401
                    QAction,  # noqa: F401
                    QMessageBox,  # noqa: F401
                )  # pyright: ignore[reportUnusedImport]
                from PyQt5.QtGui import QIcon  # pyright: ignore[reportUnusedImport]  # noqa: F401

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
        self._normal_tray_icon = None
        QObject = self._get_qobject()
        Signal = self._get_signal()

        class DialogSignaler(QObject):
            """Вспомогательный QObject для сигналов Qt, позволяющий вызывать методы из других потоков."""

            show_dialog_signal = Signal()
            show_install_dialog_signal = Signal()
            refresh_install_menu_signal = Signal()

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
        self._normal_tray_icon = icon
        self._install_menu_action = None
        tray.setIcon(icon)
        tray.setToolTip(APPNAME)
        menu = QMenu()
        check_action = QAction("Проверить сейчас", menu)
        forum_action = QAction("Чё там на форуме?", menu)
        install_action = QAction("Установить", menu)
        self._install_menu_action = install_action
        quit_action = QAction("Выйти", menu)
        menu.addAction(check_action)
        menu.addAction(forum_action)
        menu.addAction(install_action)
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
        install_action.triggered.connect(
            lambda checked=False: threading.Thread(
                target=updater_app.show_install, daemon=True
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
        self.__dialog_signaler.show_install_dialog_signal.connect(
            lambda: self.__show_install_dialog_impl(updater_app)
        )
        self.__dialog_signaler.refresh_install_menu_signal.connect(
            lambda: self.__update_install_menu_visibility_impl(updater_app)
        )
        log_debug("create_tray: created dialog signaler in main thread")

    def update_install_menu_visibility(self, updater_app: UpdaterApp) -> None:
        """Показать пункт «Установить», только если в кэше есть валидный дистрибутив."""
        QThread = self._get_qthread()
        is_main_thread = QThread.currentThread() == self.app.thread()
        if is_main_thread:
            self.__update_install_menu_visibility_impl(updater_app)
        else:
            self.__dialog_signaler.refresh_install_menu_signal.emit()

    def __update_install_menu_visibility_impl(self, updater_app: UpdaterApp) -> None:
        if not self._install_menu_action:
            return
        visible = updater_app.has_ready_package()
        self._install_menu_action.setVisible(visible)
        log_debug(f"update_install_menu_visibility: visible={visible}")

    def show_update_dialog(self, updater_app: UpdaterApp) -> None:
        """Показать диалог обновления через Qt."""
        log_debug("show_update_dialog: called")
        # Проверяем, вызывается ли из главного потока Qt
        QThread = self._get_qthread()
        is_main_thread = QThread.currentThread() == self.app.thread()

        log_debug(f"show_update_dialog: is_main_thread={is_main_thread}")
        if is_main_thread:
            # Вызываем напрямую, так как мы в главном потоке (клик по иконке tray)
            log_debug(
                "show_update_dialog: calling _show_update_dialog_impl directly from main thread"
            )
            self.__show_update_dialog_impl(updater_app)
        else:
            # Вызываем из главного потока через сигнал Qt
            # (попали сюда из меню "Проверить сейчас" или через автопоказ диалога для AppIndicator)
            log_debug(
                "show_update_dialog: scheduling dialog in main thread via Qt signal"
            )
            # Используем сигнал для вызова в главном потоке
            self.__dialog_signaler.show_dialog_signal.emit()
            log_debug("show_update_dialog: emitted show_dialog_signal")

    def show_install_dialog(self, updater_app: UpdaterApp) -> None:
        """Показать диалог установки через Qt."""
        log_debug("show_install_dialog: called")
        QThread = self._get_qthread()
        is_main_thread = QThread.currentThread() == self.app.thread()
        log_debug(f"show_install_dialog: is_main_thread={is_main_thread}")
        if is_main_thread:
            self.__show_install_dialog_impl(updater_app)
        else:
            self.__dialog_signaler.show_install_dialog_signal.emit()
            log_debug("show_install_dialog: emitted show_install_dialog_signal")

    def __show_install_dialog_impl(self, updater_app: UpdaterApp) -> None:
        """Внутренняя реализация диалога установки через Qt."""
        log_debug("_show_install_dialog_impl: called")
        message = self._build_install_dialog_message(updater_app)
        QMessageBox = self._get_qmessagebox()
        Qt = self._get_qt()
        msg = QMessageBox()
        msg.setWindowTitle(self.INSTALL_DIALOG_TITLE)
        msg.setText(message)
        msg.setModal(True)
        msg.setWindowFlags(msg.windowFlags() | Qt.WindowStaysOnTopHint)
        msg.addButton(QMessageBox.Ok)
        msg.setIcon(QMessageBox.Information)
        msg.activateWindow()
        msg.raise_()
        log_debug("_show_install_dialog_impl: showing Qt install dialog")
        msg.exec_()

    def __show_update_dialog_impl(self, updater_app: UpdaterApp) -> None:
        """Внутренняя реализация показа диалога обновления через Qt."""
        log_debug("_show_update_dialog_impl: called")
        message = self._build_dialog_message(updater_app)
        remote = updater_app.current_package_versions.remote()
        ignore_notification = self._build_ignore_notification(remote)
        remind_message = self._build_remind_message(remote)

        QMessageBox = self._get_qmessagebox()
        Qt = self._get_qt()

        msg = QMessageBox()
        msg.setWindowTitle(self.DIALOG_TITLE)
        msg.setText(message)
        msg.setModal(True)
        msg.setWindowFlags(msg.windowFlags() | Qt.WindowStaysOnTopHint)
        changelog_btn = msg.addButton(self.CHANGELOG_BTN_TEXT, QMessageBox.AcceptRole)
        ignore_btn = msg.addButton(self.IGNORE_BTN_TEXT, QMessageBox.DestructiveRole)
        msg.addButton(self.REMIND_BTN_TEXT, QMessageBox.RejectRole)
        msg.setIcon(QMessageBox.Information)
        msg.activateWindow()
        msg.raise_()
        log_debug("_show_update_dialog_impl: showing Qt dialog")
        result = msg.exec_()
        clicked = msg.clickedButton()
        log_debug(
            f"_show_update_dialog_impl: Qt dialog result={result}, clicked={clicked}"
        )
        if clicked == ignore_btn:
            log_debug("_show_update_dialog_impl: user clicked Ignore")
            updater_app.mark_ignored()
            self.show_tray_message(ignore_notification)
        elif clicked == changelog_btn:
            log_debug("_show_update_dialog_impl: user clicked Changelog")
            updater_app.show_forum()
        else:
            log_debug(
                "_show_update_dialog_impl: user clicked Remind Later or closed dialog"
            )
            updater_app.set_remind_later()
            self.show_tray_message(remind_message)

    def set_tray_error_state(self, error: bool) -> None:
        if not self.tray:
            return
        QIcon = self._get_qicon()
        if error:
            icon = QIcon.fromTheme("dialog-error")
            if icon.isNull():
                icon = QIcon.fromTheme("emblem-important")
            if not icon.isNull():
                self.tray.setIcon(icon)
        elif self._normal_tray_icon is not None:
            self.tray.setIcon(self._normal_tray_icon)

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
        self._normal_tray_icon_name = "applications-internet"
        self._install_menu_item = None
        self._install_menu_visible: bool | None = None
        self._tray_error_state: bool | None = None
        self._gtk_main_thread_id: int | None = None
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

    def _is_gtk_main_thread(self) -> bool:
        return (
            self._gtk_main_thread_id is not None
            and threading.get_ident() == self._gtk_main_thread_id
        )

    def _get_glib(self):
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib

        return GLib

    def _run_on_gtk_main_async(self, fn, *args, **kwargs) -> None:
        """Выполнить UI-код в GTK main loop (без ожидания результата)."""
        if self._is_gtk_main_thread():
            log_debug(f"_run_on_gtk_main_async: direct call on main thread ({fn.__name__})")
            fn(*args, **kwargs)
            return
        log_debug(f"_run_on_gtk_main_async: scheduling on main thread ({fn.__name__})")
        GLib = self._get_glib()

        def _wrapped() -> bool:
            try:
                fn(*args, **kwargs)
            except Exception as e:
                log_debug(f"_run_on_gtk_main_async: error in {fn.__name__}: {e}")
            return False

        GLib.idle_add(_wrapped, priority=GLib.PRIORITY_DEFAULT)

    def _run_on_gtk_main_sync(self, fn, *args, **kwargs):
        """Выполнить UI-код в GTK main loop и дождаться результата."""
        if self._is_gtk_main_thread():
            log_debug(f"_run_on_gtk_main_sync: direct call on main thread ({fn.__name__})")
            return fn(*args, **kwargs)
        log_debug(f"_run_on_gtk_main_sync: scheduling on main thread ({fn.__name__})")
        GLib = self._get_glib()
        result: dict[str, object] = {"value": None, "error": None}
        done = threading.Event()

        def _wrapped() -> bool:
            try:
                result["value"] = fn(*args, **kwargs)
            except Exception as e:
                result["error"] = e
                log_debug(f"_run_on_gtk_main_sync: error in {fn.__name__}: {e}")
            finally:
                done.set()
            return False

        GLib.idle_add(_wrapped, priority=GLib.PRIORITY_DEFAULT)
        done.wait()
        if result["error"] is not None:
            raise result["error"]
        return result["value"]

    def create_tray(self, updater_app: UpdaterApp) -> None:
        """Create tray icon using AppIndicator for Unity/GNOME."""
        import gi

        gi.require_version("AppIndicator3", "0.1")
        gi.require_version("Gtk", "3.0")
        from gi.repository import AppIndicator3, Gtk

        # Initialize GTK
        Gtk.init(sys.argv)
        self._gtk_main_thread_id = threading.get_ident()
        self.app = Gtk.Application.new("com.chromium.gost.updater", 0)

        # Find icon path
        icon_path = self._find_icon_path()

        # Create AppIndicator
        if icon_path:
            icon = str(icon_path)
        else:
            icon = "applications-internet"
        self._normal_tray_icon_name = icon

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

        forum_item = Gtk.MenuItem(label="Чё там на форуме?")
        forum_item.connect(
            "activate",
            lambda ignored_widget: threading.Thread(
                target=updater_app.show_forum, daemon=True
            ).start(),
        )
        menu.append(forum_item)

        install_item = Gtk.MenuItem(label="Установить")
        self._install_menu_item = install_item
        install_item.connect(
            "activate",
            lambda ignored_widget: threading.Thread(
                target=updater_app.show_install, daemon=True
            ).start(),
        )
        menu.append(install_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Выйти")
        quit_item.connect("activate", lambda ignored_widget: self.quit())
        menu.append(quit_item)

        menu.show_all()
        self.tray.set_menu(menu)

    def __update_install_menu_visibility_impl(self, visible: bool) -> None:
        if not self._install_menu_item:
            return
        if self._install_menu_visible == visible:
            return
        self._install_menu_item.set_visible(visible)
        self._install_menu_visible = visible
        log_debug(f"update_install_menu_visibility: visible={visible}")

    def update_install_menu_visibility(self, updater_app: UpdaterApp) -> None:
        if not self._install_menu_item:
            return
        visible = updater_app.has_ready_package()
        self._run_on_gtk_main_async(self.__update_install_menu_visibility_impl, visible)

    def __show_update_dialog_impl(self, updater_app: UpdaterApp) -> None:
        """Показать диалог обновления (только из GTK main thread)."""
        log_debug("__show_update_dialog_impl: called")
        message = self._build_dialog_message(updater_app)
        remote = updater_app.current_package_versions.remote()
        if not remote:
            return
        ignore_notification = self._build_ignore_notification(remote)
        remind_message = self._build_remind_message(remote)

        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        dialog = Gtk.MessageDialog(
            parent=None,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.NONE,
            message_format=message,
        )
        dialog.set_title(self.DIALOG_TITLE)

        dialog.add_button(self.CHANGELOG_BTN_TEXT, Gtk.ResponseType.HELP)
        dialog.add_button(self.IGNORE_BTN_TEXT, Gtk.ResponseType.REJECT)
        dialog.add_button(self.REMIND_BTN_TEXT, Gtk.ResponseType.CLOSE)

        log_debug("__show_update_dialog_impl: showing GTK dialog for AppIndicator")
        response = dialog.run()
        dialog.destroy()
        log_debug(f"__show_update_dialog_impl: GTK dialog response={response}")

        if response == Gtk.ResponseType.HELP:
            log_debug("__show_update_dialog_impl: user clicked Changelog")
            updater_app.show_forum()
        elif response == Gtk.ResponseType.REJECT:
            log_debug("__show_update_dialog_impl: user clicked Ignore")
            updater_app.mark_ignored()
            self.show_tray_message(ignore_notification)
        else:
            log_debug(
                "__show_update_dialog_impl: user clicked Remind Later or closed dialog"
            )
            updater_app.set_remind_later()
            self.show_tray_message(remind_message)

    def show_update_dialog(self, updater_app: UpdaterApp) -> None:
        """Показать диалог обновления через AppIndicator (GTK)."""
        log_debug("show_update_dialog: called")
        self._run_on_gtk_main_sync(self.__show_update_dialog_impl, updater_app)

    def __show_install_dialog_impl(self, updater_app: UpdaterApp) -> None:
        """Показать диалог установки (только из GTK main thread)."""
        log_debug("__show_install_dialog_impl: called")
        message = self._build_install_dialog_message(updater_app)

        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        dialog = Gtk.MessageDialog(
            parent=None,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            message_format=message,
        )
        dialog.set_title(self.INSTALL_DIALOG_TITLE)
        log_debug("__show_install_dialog_impl: showing GTK install dialog for AppIndicator")
        dialog.run()
        dialog.destroy()

    def show_install_dialog(self, updater_app: UpdaterApp) -> None:
        """Показать диалог установки через AppIndicator (GTK)."""
        log_debug("show_install_dialog: called")
        self._run_on_gtk_main_sync(self.__show_install_dialog_impl, updater_app)

    def __set_tray_error_state_impl(self, error: bool) -> None:
        if not self.tray:
            return
        if self._tray_error_state == error:
            return
        icon = "dialog-error" if error else self._normal_tray_icon_name
        self.tray.set_icon(icon)
        self._tray_error_state = error
        log_debug(f"set_tray_error_state: error={error}")

    def set_tray_error_state(self, error: bool) -> None:
        if not self.tray:
            return
        self._run_on_gtk_main_async(self.__set_tray_error_state_impl, error)

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

    def show_install_dialog(self, updater_app: UpdaterApp) -> None:
        """Показать диалог установки не поддерживается в headless режиме."""
        pass

    def update_install_menu_visibility(self, updater_app: UpdaterApp) -> None:
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
        self._download_lock = threading.Lock()
        self._download_in_progress = False

    def get_ready_package(self, version: str | None = None) -> Path | None:
        version = version or self.current_package_versions.remote()
        if not version:
            return None
        return DOWNLOADER.get_valid_cached_package(version)

    def has_ready_package(self, version: str | None = None) -> bool:
        return self.get_ready_package(version) is not None

    def _set_tray_error(self, error: bool) -> None:
        GUI_BACKEND.set_tray_error_state(error)

    def notify_update_ready(
        self,
        *,
        user_initiated: bool = False,
        package_path: Path | None = None,
        remote_version: str | None = None,
    ) -> None:
        if package_path is None:
            package_path = self.get_ready_package(remote_version)
        if not package_path:
            return
        self._set_tray_error(False)
        self.refresh_install_menu_visibility()
        remote = remote_version or self.current_package_versions.remote() or "?"
        if user_initiated:
            if IS_WINDOWS:
                open_installer_folder(package_path)
            else:
                self.show_update_dialog()
            return
        artifact = "инсталлер" if IS_WINDOWS else "дистрибутив"
        GUI_BACKEND.show_tray_message(
            f"Скачан {artifact} Chromium Gost {remote}", 5000
        )

    def download_update_async(self, force: bool = False) -> None:
        remote = self.current_package_versions.remote()
        if not remote:
            return

        with self._download_lock:
            if self._download_in_progress:
                GUI_BACKEND.show_tray_message("Скачивание уже выполняется...", 3000)
                return
            if not force and self.has_ready_package(remote):
                self.notify_update_ready(remote_version=remote)
                return
            self._download_in_progress = True

        def worker() -> None:
            try:
                filename = DOWNLOADER.get_package_filename(remote)
                GUI_BACKEND.show_tray_message(f"Скачивается {filename}", 3000)
                package_path = DOWNLOADER.download_package(remote, force=force)
                if package_path:
                    self.notify_update_ready(
                        package_path=package_path,
                        remote_version=remote,
                    )
                else:
                    self._set_tray_error(True)
                    self.refresh_install_menu_visibility()
                    retries = DOWNLOADER.get_retries_count()
                    if DOWNLOADER.has_exhausted_download_attempts(remote):
                        GUI_BACKEND.show_tray_message(
                            f"Дистрибутив {remote} не прошёл проверку после "
                            f"{retries} попыток. Повторная загрузка отложена.",
                            8000,
                        )
                    else:
                        GUI_BACKEND.show_tray_message(
                            f"Не удалось скачать {remote}",
                            5000,
                        )
            finally:
                with self._download_lock:
                    self._download_in_progress = False

        threading.Thread(target=worker, daemon=True).start()

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
            log_debug("has_updates: remind_at not expired, returning False")
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
            log_debug("cleanup_installed_version: state saved after cleanup")

    def create_tray(self) -> None:
        """Создать tray иконку через GUI бэкенд."""
        GUI_BACKEND.create_tray(self)
        self.refresh_install_menu_visibility()

    def refresh_install_menu_visibility(self) -> None:
        """Обновить видимость пункта «Установить» в меню трея."""
        GUI_BACKEND.update_install_menu_visibility(self)

    def _get_download_filename(self, version: str | None = None) -> str | None:
        version = version or self.current_package_versions.remote()
        if not version:
            return None
        return DOWNLOADER.get_package_filename(version)

    def _show_downloading_status_message(self, version: str | None = None) -> None:
        filename = self._get_download_filename(version)
        if filename:
            GUI_BACKEND.show_tray_message(f"Скачивается {filename}", 3000)

    def handle_left_or_double_click(self) -> None:
        """Обработчик левого или двойного клика на tray иконке."""
        GUI_BACKEND.show_tray_if_hidden()
        if self.has_ready_package():
            self.show_install()
            return
        if self._download_in_progress:
            self._show_downloading_status_message()
            return
        if self.has_updates():
            self.download_update_async()
            return
        GUI_BACKEND.show_tray_message("Обновлений не найдено")

    def show_install(self) -> None:
        """Показать команду установки или открыть папку с дистрибутивом."""
        GUI_BACKEND.show_tray_if_hidden()
        package_path = self.get_ready_package()
        if not package_path:
            return
        if IS_WINDOWS:
            open_installer_folder(package_path)
            return
        GUI_BACKEND.show_install_dialog(self)

    def show_update_dialog(self) -> None:
        """Показать диалог обновления через GUI бэкенд."""
        if IS_WINDOWS:
            return
        log_debug("show_update_dialog: called")
        GUI_BACKEND.show_update_dialog(self)

    def manual_check_and_notify(self) -> None:
        log_debug("manual_check_and_notify: starting manual check")
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

        differ = self.current_package_versions.differ()
        if not differ:
            log_debug("manual_check_and_notify: no updates")
            GUI_BACKEND.show_tray_message("Обновлений не найдено")
            return

        if self.has_ready_package(remote):
            log_debug("manual_check_and_notify: ready package in cache")
            self.notify_update_ready(user_initiated=True)
            return

        log_debug("manual_check_and_notify: starting download")
        self.download_update_async(force=True)


def main() -> None:
    log_debug(f"=== Starting {APPNAME} ===")
    log_debug(f"Session ID: {SESSION_ID}, PID: {os.getpid()}, Args: {sys.argv}")
    # Cleanup old package files on startup (including cache cleanup)
    cleanup_old_package_files()
    # Очистка старых файлов из кэша при периодических запусках
    try:
        DOWNLOADER.cleanup_old_cache_files()
        log_debug("main: cache cleanup completed")
    except Exception as e:
        log_debug(f"main: cache cleanup failed: {e}")
    updater = UpdaterAppImpl()
    updater.check_package_versions()
    # Очищаем уже установленную версию из ignored_versions и remind_at
    updater.cleanup_installed_version()

    # Check if running under systemd (no DISPLAY) or check-only mode requested
    # For systemd timers, run in headless mode even if GUI is available
    has_display = IS_WINDOWS or (
        os.environ.get("DISPLAY") and os.environ.get("DISPLAY") != ""
    )
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

        if has_updates:
            updater.download_update_async()

        # Run appropriate main loop
        sys.exit(GUI_BACKEND.run_main_loop())
    else:
        # Headless mode (systemd or --check-only)
        print("Package versions:", updater.current_package_versions)
        if updater.has_updates():
            remote = updater.current_package_versions.remote()
            print("UPDATE AVAILABLE:", remote)
            gui_launched = launch_gui_version()
            if gui_launched:
                msg = f"Доступно обновление {remote}\nGUI запущен в системном трее."
            else:
                package_path = DOWNLOADER.download_package(remote) if remote else None
                if package_path:
                    install_hint = PACKAGE_MANAGER.format_user_install_command(
                        package_path
                    )
                    if IS_WINDOWS:
                        install_hint = _path_for_display(package_path)
                    msg = (
                        f"Доступно обновление {remote}\n"
                        f"Скачан дистрибутив:\n{install_hint}"
                    )
                else:
                    msg = (
                        f"Доступно обновление {remote}\n"
                        "Запустите скрипт вручную для скачивания."
                    )
            NOTIFIER.notify(msg, 10000)
        else:
            print("No update available.")


if __name__ == "__main__":
    main()

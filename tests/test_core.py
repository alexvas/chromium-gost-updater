from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1:142.0.7444.176-1", "142.0.7444.176"),
        ("142.0.7444.176-1.el8", "142.0.7444.176"),
        ("142.0.7444.176", "142.0.7444.176"),
        (None, None),
        ("", ""),
    ],
)
def test_normalize_local_version(updater, raw, expected):
    assert updater.PackageManager._normalize_local_version(raw) == expected


def test_filename_from_content_disposition(updater):
    header = 'attachment; filename="chromium-gost-142.deb"'
    assert (
        updater._filename_from_content_disposition(header)
        == "chromium-gost-142.deb"
    )


def test_deb_get_local_version_uses_dpkg_query(monkeypatch, updater):
    def fake_check_output(cmd, stderr=None, text=None):
        assert cmd[:2] == ["dpkg-query", "-W"]
        return "1:142.0.7444.176-1\n"

    monkeypatch.setattr(updater.subprocess, "check_output", fake_check_output)
    assert updater.DebPackageManager.get_local_version() == "142.0.7444.176"


def test_deb_get_local_version_falls_back_to_apt_cache(monkeypatch, updater):
    def raise_called_process_error(*args, **kwargs):
        raise updater.subprocess.CalledProcessError(1, "dpkg-query")

    monkeypatch.setattr(
        updater.subprocess, "check_output", raise_called_process_error
    )
    monkeypatch.setattr(
        updater.DebPackageManager,
        "_check_output_for_package",
        classmethod(lambda cls, cmd: "Package: chromium-gost-stable\nVersion: 142.0.7444.176-1\n"),
    )

    assert updater.DebPackageManager.get_local_version() == "142.0.7444.176"


def test_validate_linux_package_file_for_deb(monkeypatch, updater, tmp_path):
    package_path = tmp_path / "chromium-gost-test.deb"
    package_path.write_bytes(b"fake")

    monkeypatch.setattr(
        updater.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="Debian binary package (format 2.0)",
            stderr="",
        ),
    )

    assert updater.validate_linux_package_file(package_path, "deb") is True


def test_downloader_version_from_cached_filename_linux(monkeypatch, updater):
    monkeypatch.setattr(updater, "IS_WINDOWS", False)
    downloader = updater.Downloader()
    version = downloader._version_from_cached_filename(
        "chromium-gost-142.0.7444.176-linux-amd64.deb"
    )
    assert version == "142.0.7444.176"


def test_cleanup_stale_state_versions_keeps_only_current_remote(monkeypatch, updater):
    saved_states = []
    monkeypatch.setattr(
        updater,
        "load_state",
        lambda: {
            "ignored_versions": [],
            "remind_at": {
                "143.0.7499.169": 1.0,
                "143.0.7499.193": 2.0,
                "146.0.7680.216": 3.0,
            },
        },
    )
    monkeypatch.setattr(
        updater,
        "save_state",
        lambda state: saved_states.append(dict(state)),
    )

    app = updater.UpdaterAppImpl()
    app.current_package_versions.set_remote("146.0.7680.216")
    app.cleanup_stale_state_versions()

    assert app.state["remind_at"] == {"146.0.7680.216": 3.0}
    assert len(saved_states) == 1


def test_cleanup_stale_state_versions_skips_when_remote_unknown(monkeypatch, updater):
    saved_states = []
    monkeypatch.setattr(
        updater,
        "load_state",
        lambda: {
            "ignored_versions": [],
            "remind_at": {"143.0.7499.193": 2.0},
        },
    )
    monkeypatch.setattr(
        updater,
        "save_state",
        lambda state: saved_states.append(dict(state)),
    )

    app = updater.UpdaterAppImpl()
    app.current_package_versions.set_remote(None)
    app.cleanup_stale_state_versions()

    assert app.state["remind_at"] == {"143.0.7499.193": 2.0}
    assert saved_states == []


@pytest.mark.parametrize(
    ("args", "env", "expected"),
    [
        (["-session", "abc"], {}, "qt-session-restore"),
        (["--show-tray-lazily"], {"INVOCATION_ID": "x"}, "systemd-user-service"),
        (["--check-only"], {}, "check-only"),
        (["--show-tray-lazily"], {}, "lazy-cli"),
        ([], {}, "direct-cli"),
    ],
)
def test_detect_launch_source(updater, args, env, expected):
    assert updater.detect_launch_source(args=args, env=env) == expected

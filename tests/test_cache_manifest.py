from datetime import datetime, timedelta


def _setup_cache_paths(monkeypatch, updater, tmp_path):
    cache_dir = tmp_path / "cache" / "packages"
    manifest_path = cache_dir / "cache.toml"
    monkeypatch.setattr(updater, "CACHE_PACKAGES_DIR", cache_dir)
    monkeypatch.setattr(updater, "CACHE_MANIFEST_FILE", manifest_path)
    return cache_dir, manifest_path


def _pkg_ext(updater):
    return updater.PACKAGE_MANAGER.get_extension()


def _pkg_filename(version, ext):
    return f"chromium-gost-{version}-linux-amd64.{ext}"


def test_register_in_cache_writes_manifest_entry(monkeypatch, updater, tmp_path):
    _setup_cache_paths(monkeypatch, updater, tmp_path)
    downloader = updater.Downloader()
    ext = _pkg_ext(updater)

    artifact = tmp_path / _pkg_filename("142.0.7444.176", ext)
    artifact.write_bytes(b"x" * 16)

    downloader._register_in_cache(
        version="142.0.7444.176",
        filename=artifact.name,
        file_path=artifact,
        status="ok",
        failed_attempts=0,
        downloaded_at="2026-01-01T00:00:00",
    )

    manifest = downloader._load_cache_manifest()
    entry = manifest["packages"]["142.0.7444.176"]
    assert entry["file"] == artifact.name
    assert entry["status"] == "ok"
    assert entry["failed_attempts"] == 0
    assert entry["size"] == 16
    assert entry["downloaded_at"] == "2026-01-01T00:00:00"


def test_get_valid_cached_package_marks_error_when_invalid(
    monkeypatch, updater, tmp_path
):
    cache_dir, _ = _setup_cache_paths(monkeypatch, updater, tmp_path)
    downloader = updater.Downloader()
    ext = _pkg_ext(updater)

    version = "142.0.7444.176"
    filename = _pkg_filename(version, ext)
    artifact = cache_dir / filename
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"payload")

    downloader._register_in_cache(
        version=version,
        filename=filename,
        file_path=artifact,
        status="ok",
        failed_attempts=0,
        downloaded_at="2026-01-01T00:00:00",
    )

    monkeypatch.setattr(updater, "validate_artifact", lambda *args, **kwargs: False)
    monkeypatch.setattr(downloader, "get_retries_count", lambda: 3)

    cached = downloader.get_valid_cached_package(version)
    assert cached is None

    entry = downloader._load_cache_manifest()["packages"][version]
    assert entry["status"] == "error"
    assert entry["failed_attempts"] == 1


def test_get_valid_cached_package_recovers_non_ok_status_when_valid(
    monkeypatch, updater, tmp_path
):
    cache_dir, _ = _setup_cache_paths(monkeypatch, updater, tmp_path)
    downloader = updater.Downloader()
    ext = _pkg_ext(updater)

    version = "142.0.7444.200"
    filename = _pkg_filename(version, ext)
    artifact = cache_dir / filename
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"payload")

    downloader._register_in_cache(
        version=version,
        filename=filename,
        file_path=artifact,
        status="pending",
        failed_attempts=2,
        downloaded_at="2026-01-01T00:00:00",
    )

    monkeypatch.setattr(updater, "validate_artifact", lambda *args, **kwargs: True)

    cached = downloader.get_valid_cached_package(version)
    assert cached == artifact

    entry = downloader._load_cache_manifest()["packages"][version]
    assert entry["status"] == "ok"
    assert entry["failed_attempts"] == 0


def test_cleanup_old_cache_files_removes_stale_entries(monkeypatch, updater, tmp_path):
    cache_dir, _ = _setup_cache_paths(monkeypatch, updater, tmp_path)
    downloader = updater.Downloader()
    ext = _pkg_ext(updater)

    stale_version = "142.0.7000.1"
    fresh_version = "142.0.8000.1"
    stale_name = _pkg_filename(stale_version, ext)
    fresh_name = _pkg_filename(fresh_version, ext)

    stale_file = cache_dir / stale_name
    fresh_file = cache_dir / fresh_name
    stale_file.parent.mkdir(parents=True, exist_ok=True)
    stale_file.write_bytes(b"stale")
    fresh_file.write_bytes(b"fresh")

    stale_date = (datetime.now() - timedelta(days=90)).isoformat()
    fresh_date = (datetime.now() - timedelta(days=2)).isoformat()

    downloader._register_in_cache(
        version=stale_version,
        filename=stale_name,
        file_path=stale_file,
        status="ok",
        failed_attempts=0,
        downloaded_at=stale_date,
    )
    downloader._register_in_cache(
        version=fresh_version,
        filename=fresh_name,
        file_path=fresh_file,
        status="ok",
        failed_attempts=0,
        downloaded_at=fresh_date,
    )

    downloader.cleanup_old_cache_files(max_age_days=30)

    manifest = downloader._load_cache_manifest()
    assert stale_version not in manifest.get("packages", {})
    assert fresh_version in manifest.get("packages", {})
    assert not stale_file.exists()
    assert fresh_file.exists()


def test_rebuild_cache_manifest_if_missing_restores_from_files(
    monkeypatch, updater, tmp_path
):
    cache_dir, manifest_path = _setup_cache_paths(monkeypatch, updater, tmp_path)
    downloader = updater.Downloader()
    ext = _pkg_ext(updater)

    version_ok = "142.0.9000.1"
    version_bad = "142.0.9000.2"
    file_ok = cache_dir / _pkg_filename(version_ok, ext)
    file_bad = cache_dir / _pkg_filename(version_bad, ext)
    unrelated = cache_dir / "random-file.txt"

    cache_dir.mkdir(parents=True, exist_ok=True)
    file_ok.write_bytes(b"ok")
    file_bad.write_bytes(b"bad")
    unrelated.write_text("ignore me", encoding="utf-8")

    # Пустой манифест на диске — должен быть перестроен
    manifest_path.write_text("[packages]\n", encoding="utf-8")

    def fake_validate_artifact(path, extension):
        assert extension == ext
        return path.name == file_ok.name

    monkeypatch.setattr(updater, "validate_artifact", fake_validate_artifact)

    downloader.rebuild_cache_manifest_if_missing()

    manifest = downloader._load_cache_manifest()
    packages = manifest.get("packages", {})
    assert version_ok in packages
    assert version_bad not in packages
    assert packages[version_ok]["file"] == file_ok.name
    assert packages[version_ok]["status"] == "ok"


def test_rebuild_cache_manifest_if_missing_keeps_existing_packages(
    monkeypatch, updater, tmp_path
):
    cache_dir, _ = _setup_cache_paths(monkeypatch, updater, tmp_path)
    downloader = updater.Downloader()
    ext = _pkg_ext(updater)

    existing_version = "142.0.9500.1"
    existing_name = _pkg_filename(existing_version, ext)
    existing_file = cache_dir / existing_name
    existing_file.parent.mkdir(parents=True, exist_ok=True)
    existing_file.write_bytes(b"existing")

    downloader._register_in_cache(
        version=existing_version,
        filename=existing_name,
        file_path=existing_file,
        status="ok",
        failed_attempts=0,
        downloaded_at="2026-01-01T00:00:00",
    )

    # Файл, который был бы подобран rebuild, если бы не ранний выход
    another_version = "142.0.9500.2"
    another_name = _pkg_filename(another_version, ext)
    another_file = cache_dir / another_name
    another_file.write_bytes(b"another")

    validate_calls = {"count": 0}

    def fake_validate_artifact(path, extension):
        validate_calls["count"] += 1
        return True

    monkeypatch.setattr(updater, "validate_artifact", fake_validate_artifact)

    downloader.rebuild_cache_manifest_if_missing()

    manifest = downloader._load_cache_manifest()
    packages = manifest.get("packages", {})
    assert existing_version in packages
    assert another_version not in packages
    assert validate_calls["count"] == 0

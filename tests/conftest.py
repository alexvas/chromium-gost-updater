import importlib.util
from pathlib import Path

import pytest


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT_DIR / "chromium-gost-updater.py"


@pytest.fixture
def updater():
    """Load script module from file path (hyphenated filename)."""
    spec = importlib.util.spec_from_file_location(
        "chromium_gost_updater_under_test", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

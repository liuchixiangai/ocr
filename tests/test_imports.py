"""Every app module must import cleanly (headless, under QT_QPA_PLATFORM=offscreen).

This catches dependency upgrades that remove or rename an API used at import
time, missing dependencies, and syntax errors in rarely exercised modules.
"""

import importlib
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"

# Modules that can only be imported on one platform.
PLATFORM_MODULES = {
    "screen_capture_source_mac": "darwin",
    "screen_capture_source_windows": "win32",
}

MODULES = sorted(path.stem for path in SRC_DIR.glob("*.py"))


def test_module_list_is_complete():
    assert {"main", "mainwindow", "storage", "tesseract", "ui_mainwindow"} <= set(
        MODULES
    )


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name):
    platform = PLATFORM_MODULES.get(module_name)
    if platform is not None and sys.platform != platform:
        pytest.skip(f"{module_name} is {platform}-only")
    importlib.import_module(module_name)


def test_importing_logging_uses_test_log_dir():
    import sc_logging
    from conftest import SESSION_LOG_DIR

    assert sc_logging.log_file_path is not None
    assert Path(sc_logging.log_file_path).parent == SESSION_LOG_DIR

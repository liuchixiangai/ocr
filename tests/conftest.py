"""Shared pytest setup for the ScoreSight test suite.

The module-level code below runs before any test module (and therefore before
any app module) is imported. App modules touch the user's data and log
directories at import time (sc_logging opens log files and prunes old ones,
some modules read and subscribe to settings), so the environment is pointed at
throw-away directories first. pyproject.toml puts src/ on sys.path.
"""

import atexit
import importlib.util
import logging
import os
import shutil
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TESSDATA_DIR = REPO_ROOT / "tesseract" / "tessdata"

_SESSION_DIR = Path(tempfile.mkdtemp(prefix="scoresight-tests-"))
SESSION_DATA_DIR = _SESSION_DIR / "data"
SESSION_LOG_DIR = _SESSION_DIR / "logs"

os.environ["SCORESIGHT_DATA_DIR"] = str(SESSION_DATA_DIR)
os.environ["SCORESIGHT_LOG_DIR"] = str(SESSION_LOG_DIR)
os.environ.pop("SCORESIGHT_PROFILE", None)
# never open real windows; also lets the Qt tests run on CI without a display
os.environ["QT_QPA_PLATFORM"] = "offscreen"

HAS_TESSEROCR = importlib.util.find_spec("tesserocr") is not None
HAS_OCR_MODELS = TESSDATA_DIR.is_dir() and any(TESSDATA_DIR.glob("*.traineddata"))

# The PP-OCR models aren't in git (scripts/fetch_models.py downloads them):
# without them (or onnxruntime) the `ppocr` tests are skipped, unless
# SCORESIGHT_REQUIRE_PPOCR is set (CI fetches them), which makes them fail.
PPOCR_MODELS_DIR = REPO_ROOT / "ocr_models" / "ppocr"
PPOCR_FILES = ("inference.onnx", "characters.txt", "config.json")
PPOCR_FOLDERS = ("PP-OCRv6_small_rec", "PP-OCRv6_tiny_rec")
HAS_ONNXRUNTIME = importlib.util.find_spec("onnxruntime") is not None
HAS_PPOCR_MODELS = all(
    (PPOCR_MODELS_DIR / folder / name).is_file()
    for folder in PPOCR_FOLDERS
    for name in PPOCR_FILES
)
REQUIRE_PPOCR = bool(os.environ.get("SCORESIGHT_REQUIRE_PPOCR"))
PPOCR_MISSING = (
    None
    if HAS_ONNXRUNTIME and HAS_PPOCR_MODELS
    else (
        "onnxruntime is not installed"
        if not HAS_ONNXRUNTIME
        else "the PP-OCR models aren't downloaded (python scripts/fetch_models.py)"
    )
)


def _remove_session_dir():
    # close the log files sc_logging opened so they can be deleted on Windows
    logging.shutdown()
    shutil.rmtree(_SESSION_DIR, ignore_errors=True)


atexit.register(_remove_session_dir)


def pytest_report_header(config):
    return f"scoresight test data/log dir: {_SESSION_DIR}"


def pytest_ignore_collect(collection_path, config):
    """Skip the OCR-eval tests when that package is not in this tree.

    This portable fork does not ship ``ocr_eval`` (it is listed in
    ``.gitignore``). Those tests import the package at module level, so
    collecting them aborts the whole run with ModuleNotFoundError. When the
    package is present, the tests are collected as usual.
    """
    if not collection_path.name.startswith("test_ocr_eval"):
        return None
    if importlib.util.find_spec("ocr_eval") is None:
        return True
    return None


def pytest_collection_modifyitems(config, items):
    if PPOCR_MISSING is not None:
        if REQUIRE_PPOCR:
            raise pytest.UsageError(
                f"SCORESIGHT_REQUIRE_PPOCR is set but {PPOCR_MISSING}"
            )
        skip_ppocr = pytest.mark.skip(reason=PPOCR_MISSING)
        for item in items:
            if "ppocr" in item.keywords:
                item.add_marker(skip_ppocr)
    if HAS_TESSEROCR and HAS_OCR_MODELS:
        return
    reason = (
        "tesserocr is not installed"
        if not HAS_TESSEROCR
        else f"no bundled OCR models in {TESSDATA_DIR}"
    )
    skip_ocr = pytest.mark.skip(reason=reason)
    for item in items:
        if "ocr" in item.keywords:
            item.add_marker(skip_ocr)


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Per-test settings directory (SCORESIGHT_DATA_DIR) with an empty storage
    subscriber registry and no active profile. Autouse, so no test can read or
    write another test's (or the user's) settings; request it to get the path."""
    import storage

    directory = tmp_path / "userdata"
    monkeypatch.setenv("SCORESIGHT_DATA_DIR", str(directory))
    monkeypatch.delenv("SCORESIGHT_PROFILE", raising=False)
    # Subscriptions made at import time (e.g. by api_output) live in the
    # original registry, which is restored after the test.
    monkeypatch.setattr(storage, "data_subscribers", {})
    monkeypatch.setattr(storage, "_reported_corrupt_files", {})
    return directory


@pytest.fixture
def memory_storage():
    """A fresh TextDetectionTargetMemoryStorage singleton (the boxes in memory)."""
    from storage import TextDetectionTargetMemoryStorage

    TextDetectionTargetMemoryStorage._instance = None
    memory = TextDetectionTargetMemoryStorage()
    yield memory
    # without data_changed: nothing of the finished test may react to it
    memory._data.clear()
    TextDetectionTargetMemoryStorage._instance = None


@pytest.fixture
def make_target():
    """Factory for TextDetectionTarget boxes with normalized settings.

    make_target("Home Score", rect=(x, y, w, h), **setting_overrides)
    """
    from defaults import default_info_for_box_name, normalize_settings_dict
    from text_detection_target import TextDetectionTarget

    def _make(name, rect=(0, 0, 100, 50), **settings):
        normalized = normalize_settings_dict(
            dict(settings), default_info_for_box_name(name)
        )
        return TextDetectionTarget(*rect, name, normalized, [])

    return _make


@pytest.fixture
def make_result(make_target):
    """Factory for TextDetectionTargetWithResult OCR results.

    make_result("Home Score", "12", state="Success", rect=(x, y, w, h))
    """
    from text_detection_target import TextDetectionTargetWithResult

    def _make(name, text, state="Success", rect=(1, 2, 3, 4), **settings):
        target = make_target(name, rect=rect, **settings)
        return TextDetectionTargetWithResult(
            target, text, TextDetectionTargetWithResult.ResultState[state]
        )

    return _make


@pytest.fixture
def timing(monkeypatch):
    """The startup_timing module without milestones and not finished (its
    state is global), and without SCORESIGHT_STARTUP_REPORT."""
    import startup_timing

    monkeypatch.setattr(startup_timing, "_milestones", {})
    monkeypatch.setattr(startup_timing, "_finished", False)
    monkeypatch.setattr(startup_timing, "_before_main_cache", [])
    monkeypatch.delenv(startup_timing.REPORT_ENV, raising=False)
    return startup_timing


@pytest.fixture(scope="session")
def text_detector():
    """A real TextDetector (default model: scoreboard_general). Tests using it
    must be marked `ocr`; tests that switch models should restore the default."""
    from tesseract import TextDetector

    return TextDetector(TextDetector.OcrModelIndex.SCOREBOARD_GENERAL)

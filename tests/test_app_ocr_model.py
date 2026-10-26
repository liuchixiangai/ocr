"""The OCR model in the main window: the list (PP-OCR first, the stored choice
and its migration, PP-OCR without its models), Image cleanup while PP-OCR
reads, PP-OCR (automatic)'s model in the list and the status bar, and the
one-time hint when PP-OCR (accurate) can't keep up."""

import pytest

pytestmark = pytest.mark.qt
pytest.importorskip("tesserocr")  # MainWindow imports the OCR pipeline

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

import ocr_models  # noqa: E402
import ppocr_engine  # noqa: E402
from box_settings_ui_handler import tesseract_cleanup_widgets  # noqa: E402
from storage import fetch_data, store_data  # noqa: E402
from tesseract import EngineStatus  # noqa: E402
import test_app_mainwindow_smoke as smoke  # noqa: E402
from test_app_mainwindow_smoke import FakeCaptureThread, FakeViewer  # noqa: E402

# the main window tests' fixtures
patched = smoke.patched
make_window = smoke.make_window
windows_do_not_outlive_their_test = smoke.windows_do_not_outlive_their_test

SOURCE = "rtsp://10.0.1.200:554/11"


@pytest.fixture
def ppocr_available(monkeypatch):
    monkeypatch.setattr(ppocr_engine, "missing", lambda model: None)
    monkeypatch.setattr(ppocr_engine, "unavailable_reason", lambda model: None)


@pytest.fixture
def ppocr_missing(monkeypatch):
    monkeypatch.setattr(
        ppocr_engine, "missing", lambda model: ppocr_engine.MISSING_MODELS
    )
    monkeypatch.setattr(
        ppocr_engine,
        "unavailable_reason",
        lambda model: (
            "the model files are missing (run python scripts/fetch_models.py)"
        ),
    )


class FakeTextDetector:
    def __init__(self, status: EngineStatus):
        self.status = status
        self.model = status.model
        self.models = []

    def engine_status(self):
        return self.status

    def setOcrModel(self, model):
        self.models.append(model)
        self.model = model
        self.status = EngineStatus(model)
        return True


@pytest.fixture
def running_window(make_window, monkeypatch):
    """running_window(status, ups=4.8) -> a window whose source runs, read by a
    FakeTextDetector with that engine status."""
    import mainwindow

    def _make(status=None, ups=4.8):
        class RunningViewer(FakeViewer):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.timerThread = FakeCaptureThread()
                self.timerThread.ups = ups
                self.timerThread.textDetector = FakeTextDetector(
                    status or EngineStatus(ocr_models.stored_model())
                )
                self.firstFrameReceived = True

        monkeypatch.setattr(mainwindow, "ImageViewer", RunningViewer)
        monkeypatch.setattr(mainwindow, "primary_lan_address", lambda: None)
        store_data("scoresight.json", "source_selected", SOURCE)
        return make_window()

    return _make


def items(window):
    combo = window.ui.comboBox_ocrModel
    return [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]


def current(window):
    combo = window.ui.comboBox_ocrModel
    return combo.currentText(), combo.currentData()


def item_enabled(window, value):
    combo = window.ui.comboBox_ocrModel
    return combo.model().item(window.ocrModelItem(value)).isEnabled()


def item_tooltip(window, value):
    combo = window.ui.comboBox_ocrModel
    return combo.itemData(window.ocrModelItem(value), Qt.ItemDataRole.ToolTipRole)


# --- the list and the stored choice ---------------------------------------------------


def test_the_list_has_ppocr_first(make_window, ppocr_available):
    window = make_window()
    assert items(window) == [
        ("PP-OCR (automatic)", "ppocr:auto"),
        ("PP-OCR (accurate)", "ppocr:small"),
        ("PP-OCR (fast)", "ppocr:tiny"),
        ("Tesseract: Daktronics", 0),
        ("Tesseract: General Scoreboard", 1),
        ("Tesseract: General Fonts (English)", 2),
        ("Tesseract: General Scoreboard Large", 3),
        ("Load external Tesseract model...", "load-external-model"),
    ]
    for value in (ocr_models.PPOCR_AUTO, ocr_models.PPOCR_ACCURATE, 1):
        assert item_enabled(window, value)
        assert item_tooltip(window, value)


def test_a_new_setup_defaults_to_ppocr_automatic(make_window, ppocr_available):
    window = make_window()
    assert current(window) == ("PP-OCR (automatic)", "ppocr:auto")
    # nothing is stored until a source opens
    assert fetch_data("scoresight.json", "ocr_model") is None


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        (0, "Tesseract: Daktronics"),
        (1, "Tesseract: General Scoreboard"),
        (2, "Tesseract: General Fonts (English)"),
        (3, "Tesseract: General Scoreboard Large"),
        ("ppocr:small", "PP-OCR (accurate)"),
        ("ppocr:tiny", "PP-OCR (fast)"),
    ],
)
def test_a_stored_choice_is_kept(make_window, ppocr_available, stored, shown):
    # the indexes 0.1 and 0.2 stored keep their meaning
    store_data("scoresight.json", "ocr_model", stored)
    window = make_window()
    assert current(window) == (shown, stored)
    assert fetch_data("scoresight.json", "ocr_model") == stored


def test_a_stored_external_model(make_window, ppocr_available, tmp_path):
    model = tmp_path / "custom.traineddata"
    model.write_bytes(b"x")
    store_data("scoresight.json", "ocr_model", str(model))
    window = make_window()
    assert current(window) == (
        "Load external Tesseract model...",
        "load-external-model",
    )
    assert item_tooltip(window, str(model)) == str(model)


def test_without_the_ppocr_models_tesseract_reads(make_window, ppocr_missing):
    window = make_window()
    assert current(window) == ("Tesseract: General Scoreboard", 1)
    for value in (
        ocr_models.PPOCR_AUTO,
        ocr_models.PPOCR_ACCURATE,
        ocr_models.PPOCR_FAST,
    ):
        assert not item_enabled(window, value)
        assert "python scripts/fetch_models.py" in item_tooltip(window, value)
    assert item_enabled(window, 1)
    # Image cleanup applies
    assert not window.ui.label_imageCleanupEngineNote.isVisibleTo(window)
    assert fetch_data("scoresight.json", "ocr_model") is None


def test_without_onnxruntime_the_tooltip_says_so(make_window, monkeypatch):
    monkeypatch.setattr(
        ppocr_engine, "missing", lambda model: ppocr_engine.MISSING_RUNTIME
    )
    monkeypatch.setattr(
        ppocr_engine, "unavailable_reason", lambda model: "onnxruntime is not installed"
    )
    window = make_window()
    assert "onnxruntime" in item_tooltip(window, ocr_models.PPOCR_AUTO)


def test_opening_a_source_stores_the_default(running_window, ppocr_available):
    window = running_window()
    assert fetch_data("scoresight.json", "ocr_model") == "ppocr:auto"
    assert window.currentTextDetector().model == "ppocr:auto"


def test_opening_a_source_without_ppocr_stores_nothing(running_window, ppocr_missing):
    window = running_window(EngineStatus(1))
    assert fetch_data("scoresight.json", "ocr_model") is None
    # the fallback reads
    assert window.currentTextDetector().model == 1


def test_opening_a_source_keeps_a_stored_tesseract_model(
    running_window, ppocr_available
):
    store_data("scoresight.json", "ocr_model", 2)
    window = running_window(EngineStatus(2))
    assert fetch_data("scoresight.json", "ocr_model") == 2
    assert window.currentTextDetector().models == []  # already reading with it


def test_choosing_a_model_stores_it_and_reads_with_it(running_window, ppocr_available):
    window = running_window()
    detector = window.currentTextDetector()
    combo = window.ui.comboBox_ocrModel
    combo.setCurrentIndex(window.ocrModelItem(3))
    assert fetch_data("scoresight.json", "ocr_model") == 3
    assert detector.models[-1] == 3
    # (no box is selected: the box settings are disabled as a whole)
    group = window.ui.groupBox_target_settings
    widgets = tesseract_cleanup_widgets(window.ui)
    assert all(widget.isEnabledTo(group) for widget in widgets)
    combo.setCurrentIndex(window.ocrModelItem(ocr_models.PPOCR_FAST))
    assert fetch_data("scoresight.json", "ocr_model") == "ppocr:tiny"
    assert detector.models[-1] == "ppocr:tiny"
    assert not any(widget.isEnabledTo(group) for widget in widgets)


def test_load_external_model_cancelled_keeps_the_model(
    make_window, ppocr_available, monkeypatch
):
    import mainwindow

    monkeypatch.setattr(
        mainwindow.QFileDialog, "getOpenFileName", lambda *a, **k: ("", "")
    )
    window = make_window()
    combo = window.ui.comboBox_ocrModel
    combo.setCurrentIndex(window.ocrModelItem("load-external-model"))
    assert current(window) == ("PP-OCR (automatic)", "ppocr:auto")
    assert fetch_data("scoresight.json", "ocr_model") is None


# --- Image cleanup while PP-OCR reads -----------------------------------------------


def test_image_cleanup_is_disabled_while_ppocr_reads(
    make_window, ppocr_available, make_target
):
    window = make_window()
    ui = window.ui
    storage = window.detectionTargetsStorage
    storage.add_item(make_target("Home Score", dilate=3, binarization_method=2))
    window.itemSelected("Home Score")
    window.boxSettingsUiHandler.setImageCleanupExpanded(True)
    widgets = tesseract_cleanup_widgets(ui)
    names = {widget.objectName() for widget in widgets}
    assert {
        "comboBox_binarizationMethod",
        "horizontalSlider_cleanup",
        "horizontalSlider_vscale",
        "horizontalSlider_skew",
        "horizontalSlider_dilate",
        "checkBox_invertPatch",
        "checkBox_autocrop",
        "checkBox_rescalePatch",
        "checkBox_normWHRatio",
    } <= names
    # the legacy settings aren't Tesseract's
    assert "checkBox_compositeBox" not in names
    assert "comboBox_clearValue" not in names
    assert not any(widget.isEnabled() for widget in widgets)
    note = ui.label_imageCleanupEngineNote
    assert note.isVisibleTo(window)
    assert note.text() == (
        "Image cleanup is for the Tesseract models; PP-OCR reads the picture as it is."
    )
    assert ui.checkBox_compositeBox.isEnabled()
    # the settings are kept
    settings = storage.find_item_by_name("Home Score").settings
    assert (settings["dilate"], settings["binarization_method"]) == (3, 2)
    assert ui.horizontalSlider_dilate.value() == 3

    # a dot counter uses Image cleanup with any engine
    ui.checkBox_dotDetector.setChecked(True)
    assert all(widget.isEnabled() for widget in widgets)
    assert not note.isVisibleTo(window)
    ui.checkBox_dotDetector.setChecked(False)
    assert not any(widget.isEnabled() for widget in widgets)

    # a Tesseract model: enabled
    ui.comboBox_ocrModel.setCurrentIndex(window.ocrModelItem(1))
    assert all(widget.isEnabled() for widget in widgets)
    assert not note.isVisibleTo(window)
    ui.comboBox_ocrModel.setCurrentIndex(window.ocrModelItem(ocr_models.PPOCR_FAST))
    assert not any(widget.isEnabled() for widget in widgets)


def test_the_image_cleanup_note_is_translated_again(make_window, ppocr_available):
    window = make_window()
    window.ui.label_imageCleanupEngineNote.setText("")
    window.applyRuntimeTexts()
    assert window.ui.label_imageCleanupEngineNote.text() != ""


# --- PP-OCR (automatic) in the list and the status bar -------------------------------


def test_automatic_shows_its_model_and_why(running_window, ppocr_available):
    status = EngineStatus(
        "ppocr:auto",
        ppocr_engine.READY,
        auto_model=ppocr_engine.TINY,
        auto_reason=ppocr_engine.AUTO_TOO_SLOW,
        auto_accurate_ms=110.0,
        auto_interval_ms=100.0,
    )
    window = running_window(status)
    window.refreshLiveStatus()
    why = (
        "PP-OCR automatic: fast (accurate needs 110 ms per reading here, more than "
        "70% of the 100 ms between readings)"
    )
    assert current(window)[0] == "PP-OCR (automatic: fast)"
    assert why in window.ui.comboBox_ocrModel.toolTip()
    assert why in window.statusBarWidgets.label_ocr.toolTip()

    detector = window.currentTextDetector()
    detector.status = EngineStatus(
        "ppocr:auto",
        ppocr_engine.READY,
        auto_model=ppocr_engine.SMALL,
        auto_reason=ppocr_engine.AUTO_KEEPS_UP,
        auto_accurate_ms=42.0,
        auto_interval_ms=200.0,
    )
    window.refreshLiveStatus()
    assert current(window)[0] == "PP-OCR (automatic: accurate)"
    assert (
        "PP-OCR automatic: accurate (it needs 42 ms per reading here, of the 200 ms "
        "between readings)" in window.statusBarWidgets.label_ocr.toolTip()
    )
    # without a source: just automatic
    window.reset_playing_source()
    window.refreshLiveStatus()
    assert current(window)[0] == "PP-OCR (automatic)"


def test_loading_and_fallback_in_the_status_bar(running_window, ppocr_available):
    window = running_window(EngineStatus("ppocr:auto", ppocr_engine.LOADING))
    bar = window.statusBarWidgets
    window.refreshLiveStatus()
    assert bar.label_ocr.text() == "Loading PP-OCR…"
    assert window.boxSettingsUiHandler.ppocr is True
    window.currentTextDetector().status = EngineStatus(
        "ppocr:auto", ppocr_engine.FAILED, "RuntimeError: no onnxruntime"
    )
    window.refreshLiveStatus()
    assert bar.label_ocr.text() == (
        "Reading 4.8/s of 5 · with Tesseract: PP-OCR didn't load"
    )
    assert "RuntimeError: no onnxruntime" in bar.label_ocr.toolTip()
    assert bar.label_ocr.styleSheet() != ""
    # Tesseract reads: its Image cleanup settings apply again
    assert window.boxSettingsUiHandler.ppocr is False
    assert not window.ui.label_imageCleanupEngineNote.isVisibleTo(window)


# --- the hint when PP-OCR (accurate) can't keep up ---------------------------------


def slow(window, updates):
    for _ in range(updates):
        window.refreshLiveStatus()


def test_a_slow_ppocr_accurate_gets_a_hint_once(running_window, ppocr_available):
    store_data("scoresight.json", "ocr_model", "ppocr:small")
    window = running_window(EngineStatus("ppocr:small", ppocr_engine.READY), ups=2.0)
    slow(window, 4)
    assert window.ppocrCpuHint is None
    slow(window, 1)
    hint = window.ppocrCpuHint
    assert isinstance(hint, QMessageBox) and hint.isVisible()
    assert hint.windowModality() == Qt.WindowModality.NonModal
    assert "2.0 times per second instead of 5" in hint.text()
    assert "PP-OCR (fast)" in hint.informativeText()
    assert "PP-OCR (accurate) needs more CPU" in (
        window.statusBarWidgets.label_ocr.toolTip()
    )
    # the model isn't changed without the user
    assert fetch_data("scoresight.json", "ocr_model") == "ppocr:small"
    assert fetch_data("scoresight.json", "ppocr_cpu_hint_shown") is True
    # its button chooses PP-OCR (fast)
    [use_fast] = [b for b in hint.buttons() if b.text() == "Use PP-OCR (fast)"]
    use_fast.click()
    assert fetch_data("scoresight.json", "ocr_model") == "ppocr:tiny"
    assert window.currentTextDetector().models[-1] == "ppocr:tiny"
    # once
    window.ppocrCpuHint = None
    slow(window, 10)
    assert window.ppocrCpuHint is None


def test_the_hint_is_shown_only_once_ever(running_window, ppocr_available):
    store_data("scoresight.json", "ocr_model", "ppocr:small")
    store_data("scoresight.json", "ppocr_cpu_hint_shown", True)
    window = running_window(EngineStatus("ppocr:small", ppocr_engine.READY), ups=2.0)
    slow(window, 10)
    assert window.ppocrCpuHint is None


@pytest.mark.parametrize("model", ["ppocr:auto", "ppocr:tiny", 1])
def test_no_hint_for_the_other_models(running_window, ppocr_available, model):
    store_data("scoresight.json", "ocr_model", model)
    status = EngineStatus(model, ppocr_engine.READY if model != 1 else None)
    window = running_window(status, ups=2.0)
    slow(window, 10)
    assert window.ppocrCpuHint is None


def test_keeping_accurate_closes_the_hint(running_window, ppocr_available, qapp):
    store_data("scoresight.json", "ocr_model", "ppocr:small")
    window = running_window(EngineStatus("ppocr:small", ppocr_engine.READY), ups=2.0)
    slow(window, 5)
    hint = window.ppocrCpuHint
    [keep] = [b for b in hint.buttons() if b.text() == "Keep PP-OCR (accurate)"]
    keep.click()
    qapp.processEvents()
    assert fetch_data("scoresight.json", "ocr_model") == "ppocr:small"

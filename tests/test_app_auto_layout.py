"""✨ Auto crop and ✨ Auto layout in the main window (src/auto_layout_ui.py),
with a stand-in for the layout model."""

import numpy as np
import pytest

pytest.importorskip("tesserocr")  # MainWindow imports the OCR pipeline

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import auto_layout  # noqa: E402
import auto_layout_ui  # noqa: E402
from auto_layout import Detection  # noqa: E402
from storage import fetch_data  # noqa: E402
import test_app_mainwindow_smoke as smoke  # noqa: E402
from test_app_draw_boxes import open_picture  # noqa: E402

patched = smoke.patched
make_window = smoke.make_window
main_window = smoke.main_window
windows_do_not_outlive_their_test = smoke.windows_do_not_outlive_their_test


class FakeDetector:
    def __init__(self):
        self.detections = []
        self.frames = []

    def detect(self, frame):
        self.frames.append(frame)
        return list(self.detections)


@pytest.fixture
def model(monkeypatch):
    """The model, downloaded; its detections are set by the test. Messages
    the window shows are recorded instead."""
    detector = FakeDetector()
    monkeypatch.setattr(auto_layout, "model_available", lambda: True)
    monkeypatch.setattr(auto_layout, "detector", lambda: detector)
    detector.messages = []
    monkeypatch.setattr(
        auto_layout_ui.AutoLayoutUI,
        "tell",
        lambda self, icon, title, text: detector.messages.append(title),
    )
    return detector


@pytest.fixture
def window(main_window, qtbot):
    view = open_picture(main_window, qtbot)
    thread = view.timerThread
    frame = np.zeros((720, 1280, 3), np.uint8)
    thread.latest_source = frame
    thread.latest_picture = frame
    return main_window, view


def click_and_wait(window, qtbot, button):
    button.click()
    qtbot.waitUntil(lambda: window.autoLayoutUi.busy is None)
    QApplication.processEvents()


def test_the_ai_buttons_wear_a_sparkle(window):
    window, view = window
    ui = window.ui
    for button in (ui.toolButton_autoCrop, ui.toolButton_autoLayout):
        assert button.isEnabled() and button.isVisible()
        assert not button.icon().isNull()
        assert button.objectName() in ui.widget_viewTools.styleSheet()
    assert ui.toolButton_autoCrop.text() == "Auto crop"


def test_the_ai_buttons_show_their_sparkle_only_when_narrow(window, qtbot):
    from PySide6.QtCore import Qt

    window, view = window
    ui = window.ui
    row = next(r for r in window.compactRows if r.row is ui.widget_viewTools)
    window.resize(2400, 800)
    qtbot.waitUntil(lambda: row.step == 0)
    beside = Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    assert ui.toolButton_autoLayout.toolButtonStyle() == beside
    sizes = ui.splitter_main.sizes()
    ui.splitter_main.setSizes([sizes[0], 300, sum(sizes) - sizes[0] - 300])
    qtbot.waitUntil(lambda: row.step > 0)
    icon_only = Qt.ToolButtonStyle.ToolButtonIconOnly
    assert ui.toolButton_autoLayout.toolButtonStyle() == icon_only


def test_auto_crop_crops_to_the_scoreboard(window, model, qtbot):
    window, view = window
    ui = window.ui
    window.placeFieldBox("Home Score", QRectF(500, 300, 100, 60))
    box = window.detectionTargetsStorage.find_item_by_name("Home Score")
    model.detections = [Detection("board", (400, 200, 400, 200), 0.9)]

    click_and_wait(window, qtbot, ui.toolButton_autoCrop)
    # the board and a tenth of its size around it
    assert ui.toolButton_topCrop.isChecked()
    assert window.cropValues() == (360, 180, 1280 - 840, 720 - 420)
    assert fetch_data("scoresight.json", "crop_mode") is True
    # the boxes stay on their digits
    assert (box.x(), box.y()) == (500 - 360, 300 - 180)
    assert not ui.toolButton_autoCrop.isChecked()
    assert ui.toolButton_autoCrop.text() == "Auto crop"
    assert model.messages == []


def test_auto_crop_without_a_scoreboard_says_so(window, model, qtbot):
    window, view = window
    model.detections = [Detection("home", (10, 10, 30, 30), 0.9)]
    click_and_wait(window, qtbot, window.ui.toolButton_autoCrop)
    assert not window.ui.toolButton_topCrop.isChecked()
    assert model.messages == ["No scoreboard found"]


def test_auto_layout_places_the_fields_it_found(window, model, qtbot):
    window, view = window
    storage = window.detectionTargetsStorage
    model.detections = [
        Detection("home", (100, 100, 40, 50), 0.8),
        Detection("clock", (200, 20, 100, 40), 0.9),
    ]
    click_and_wait(window, qtbot, window.ui.toolButton_autoLayout)
    home = storage.find_item_by_name("Home Score")
    clock = storage.find_item_by_name("Time")
    assert (home.x(), home.y(), home.width(), home.height()) == (95, 95, 50, 60)
    assert (clock.x(), clock.y()) == (196, 16)
    assert storage.find_item_by_name("Away Score") is None


@pytest.mark.parametrize("answer", [True, False, None])
def test_auto_layout_asks_before_moving_boxes(
    window, model, qtbot, monkeypatch, answer
):
    window, view = window
    storage = window.detectionTargetsStorage
    window.placeFieldBox("Home Score", QRectF(500, 300, 100, 60))
    asked = []
    monkeypatch.setattr(
        window,
        "askAutoLayoutMove",
        lambda found, placed: asked.append((found, placed)) or answer,
    )
    model.detections = [
        Detection("home", (100, 100, 40, 50), 0.8),
        Detection("away", (300, 100, 40, 50), 0.7),
    ]
    click_and_wait(window, qtbot, window.ui.toolButton_autoLayout)
    assert asked == [(["Home Score", "Away Score"], ["Home Score"])]
    home = storage.find_item_by_name("Home Score")
    away = storage.find_item_by_name("Away Score")
    assert (home.x(), home.y()) == ((95, 95) if answer else (500, 300))
    # Cancel places nothing
    assert (away is None) == (answer is None)


def test_nothing_is_downloaded_without_consent(window, model, qtbot, monkeypatch):
    window, view = window
    monkeypatch.setattr(auto_layout, "model_available", lambda: False)
    monkeypatch.setattr(
        auto_layout_ui.AutoLayoutUI, "askToDownload", lambda self: False
    )
    model.detections = [Detection("board", (400, 200, 400, 200), 0.9)]
    window.ui.toolButton_autoCrop.click()
    assert window.autoLayoutUi.busy is None and model.frames == []
    assert not window.ui.toolButton_autoCrop.isChecked()


def test_a_failure_is_reported(window, model, qtbot, monkeypatch):
    window, view = window

    def fail():
        raise OSError("could not download the layout model")

    monkeypatch.setattr(auto_layout, "detector", fail)
    click_and_wait(window, qtbot, window.ui.toolButton_autoLayout)
    assert model.messages == ["The AI model couldn't run"]


def test_a_result_for_a_picture_that_changed_is_dropped(window, model, qtbot):
    window, view = window
    ui = window.ui
    model.detections = [Detection("board", (400, 200, 400, 200), 0.9)]
    auto = window.autoLayoutUi
    # the crop changes while the model runs
    auto.busy = "crop"
    auto.started_for = auto.pictureState()
    ui.toolButton_topCrop.click()
    auto.finished("crop", {"size": (1280, 720), "detections": model.detections})
    assert window.cropValues() == (0, 0, 0, 0)
    assert auto.busy is None and model.messages == []


def test_the_download_question_names_the_size(window, monkeypatch):
    window, view = window
    shown = []

    def exec_(box):
        shown.append(box.text())
        return 0

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    assert not window.autoLayoutUi.askToDownload()
    assert "(42 MB)" in shown[0] and "aren't sent anywhere" in shown[0]

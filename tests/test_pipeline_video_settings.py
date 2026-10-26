"""VideoSettingsDialog (src/video_settings.py)."""

import cv2
import pytest

pytest.importorskip("tesserocr")  # video_settings imports camera_thread

from storage import fetch_data, subscribe_to_data  # noqa: E402

pytestmark = pytest.mark.qt

SETTINGS = ("fps", "width", "height", "fourcc", "backend")


class FakeDeviceCapture:
    """Plays OpenCVVideoCaptureWithSettings for the dialog."""

    def __init__(self):
        self.sets = []
        self.props = {
            cv2.CAP_PROP_FRAME_WIDTH: 1920.0,
            cv2.CAP_PROP_FRAME_HEIGHT: 1080.0,
            cv2.CAP_PROP_FPS: 30.0,
            cv2.CAP_PROP_FOURCC: float(cv2.VideoWriter_fourcc(*"MJPG")),
            cv2.CAP_PROP_BACKEND: float(cv2.CAP_DSHOW),
        }
        self.lock_count = 0

    def get(self, prop):
        return self.props.get(prop, 0.0)

    def set(self, prop, value):
        self.sets.append((prop, value))
        self.props[prop] = float(value)
        return True

    def run_locked(self, function):
        self.lock_count += 1
        return function(self)


@pytest.fixture
def dialog(qapp):
    from video_settings import VideoSettingsDialog

    dialog = VideoSettingsDialog()
    yield dialog
    dialog.deleteLater()


@pytest.fixture
def stored():
    """The Video Settings documents stored, in order."""
    stored = []
    for name in SETTINGS:
        subscribe_to_data(
            "scoresight.json", name, lambda value, name=name: stored.append(name)
        )
    return stored


def test_current_resolution_is_preselected(dialog):
    capture = FakeDeviceCapture()
    dialog.init_ui(capture)
    assert dialog.ui.comboBox_resolution.currentText() == "1920x1080"
    assert dialog.ui.spinBox_fps.value() == 30
    assert dialog.ui.spinBox_fps.minimum() == 5
    assert "MJPG" in dialog.ui.label_formatValue.text()
    # opening the dialog only reads the device; it does not try formats
    assert capture.sets == []
    # all the properties are read while waiting for the capture thread once
    assert capture.lock_count == 1
    assert "30" in dialog.ui.label_captureHint.text()
    # the dialog itself isn't shown; isHidden is the label's own flag
    assert dialog.ui.label_fpsNote.isHidden()


def test_rgb24_guid_is_shown_as_a_name_and_cannot_be_chosen(dialog):
    from video_settings import fourcc_to_str

    rgb24 = 0xE436EB7D
    assert fourcc_to_str(rgb24) == "RGB24"
    assert fourcc_to_str(float(rgb24)) == "RGB24"
    # the same bits as a signed int, which is how some bindings return it
    assert fourcc_to_str(rgb24 - 2**32) == "RGB24"
    assert fourcc_to_str("}ë6ä") is None
    assert "ë" not in fourcc_to_str(rgb24)
    capture = FakeDeviceCapture()
    capture.props[cv2.CAP_PROP_FOURCC] = float(rgb24)
    capture.props[cv2.CAP_PROP_POS_MSEC] = -1.0
    capture.props[cv2.CAP_PROP_BRIGHTNESS] = 0.0
    capture.props[cv2.CAP_PROP_CONTRAST] = 12.0
    dialog.init_ui(capture)
    label = dialog.ui.label_formatValue.text()
    assert "RGB24" in label
    assert "ë" not in label
    assert not hasattr(dialog.ui, "comboBox_fourcc")
    details = dialog.ui.plainTextEdit_videoProps.toPlainText()
    assert "CAP_PROP_FOURCC: RGB24" in details
    assert "CAP_PROP_CONTRAST: 12" in details
    assert "POS_MSEC" not in details
    assert "BRIGHTNESS" not in details
    assert capture.sets == []


def test_unreported_fps_shows_30_and_is_saved_only_on_ok(dialog, stored):
    capture = FakeDeviceCapture()
    capture.props[cv2.CAP_PROP_FPS] = -1.0
    dialog.init_ui(capture)
    assert dialog.ui.spinBox_fps.value() == 30
    assert not dialog.ui.label_fpsNote.isHidden()
    note = dialog.ui.label_fpsNote.text()
    assert "30" in note and "1" in note
    assert fetch_data("scoresight.json", "fps") is None
    assert stored == []
    dialog.save_settings()
    assert fetch_data("scoresight.json", "fps") == 30


def test_fps_of_one_is_corrected_when_the_dialog_opens(dialog):
    from storage import store_data

    capture = FakeDeviceCapture()
    capture.props[cv2.CAP_PROP_FPS] = 1.0
    store_data("scoresight.json", "fps", 1)
    dialog.init_ui(capture)
    assert dialog.ui.spinBox_fps.value() == 30
    assert not dialog.ui.label_fpsNote.isHidden()
    assert "30" in dialog.ui.label_fpsNote.text()
    assert fetch_data("scoresight.json", "fps") == 30
    # the control itself cannot go back to 1
    dialog.ui.spinBox_fps.setValue(1)
    assert dialog.ui.spinBox_fps.value() >= 5


def test_a_saved_one_keeps_the_rate_the_device_reports(dialog):
    from storage import store_data

    capture = FakeDeviceCapture()
    capture.props[cv2.CAP_PROP_FPS] = 59.94
    store_data("scoresight.json", "fps", 1)
    dialog.init_ui(capture)
    assert dialog.ui.spinBox_fps.value() == 60
    assert not dialog.ui.label_fpsNote.isHidden()
    assert "60" in dialog.ui.label_fpsNote.text()
    assert fetch_data("scoresight.json", "fps") == 60


@pytest.mark.parametrize(
    ("reported", "stored", "shown", "reason"),
    [
        (30.0, None, 30, "device"),
        (29.97, None, 30, "device"),
        (59.94, None, 60, "device"),
        (60.0, 30, 60, "device"),
        (-1.0, None, 30, "suggest-default"),
        (float("nan"), None, 30, "suggest-default"),
        (None, None, 30, "suggest-default"),
        (-1.0, 25, 25, "keep-stored"),
        (1.0, 1, 30, "correct-now"),
        (0.0, None, 30, "correct-now"),
        (1.0, None, 30, "correct-now"),
        (-1.0, 1, 30, "correct-now"),
        (60.0, 1, 60, "correct-to-device"),
        (1.0, 60, 60, "reapply"),
        (0, 24, 24, "reapply"),
    ],
)
def test_dialog_frame_rate_choice(reported, stored, shown, reason):
    from video_settings import choose_dialog_fps

    assert choose_dialog_fps(reported, stored) == (shown, reason)


def test_ok_without_changes_stores_nothing(dialog, stored):
    dialog.init_ui(FakeDeviceCapture())
    dialog.save_settings()
    assert stored == []
    assert fetch_data("scoresight.json", "width") is None


def test_ok_stores_only_the_changed_settings(dialog, stored):
    dialog.init_ui(FakeDeviceCapture())
    dialog.ui.comboBox_resolution.setCurrentText("1280x720")
    dialog.ui.spinBox_fps.setValue(60)
    dialog.save_settings()
    assert sorted(stored) == ["fps", "height", "width"]
    assert fetch_data("scoresight.json", "width") == 1280
    assert fetch_data("scoresight.json", "height") == 720
    assert fetch_data("scoresight.json", "backend") is None


def test_dialog_without_a_capture(dialog, stored):
    dialog.init_ui(None)
    dialog.save_settings()
    assert stored == []


def test_resolution_list_offers_full_hd(qapp):
    # the list offered "1980x1080" (a typo); the numeric items aren't
    # translatable, since they are parsed as WxH
    from PySide6.QtWidgets import QDialog

    from ui_video_settings import Ui_Dialog

    dialog = QDialog()
    ui = Ui_Dialog()
    ui.setupUi(dialog)
    items = [
        ui.comboBox_resolution.itemText(i)
        for i in range(ui.comboBox_resolution.count())
    ]
    assert "1920x1080" in items
    assert "1980x1080" not in items
    dialog.deleteLater()


@pytest.mark.parametrize(
    "system, available, offered",
    [
        # the pip wheels on Windows: DirectShow (and FFmpeg) only
        ("Windows", {cv2.CAP_DSHOW}, ["Auto", "DShow"]),
        (
            "Windows",
            {cv2.CAP_DSHOW, cv2.CAP_MSMF},
            ["Auto", "DShow", "Microsoft Media Foundation"],
        ),
        # the headless pip wheels on Linux have no GStreamer
        ("Linux", {cv2.CAP_V4L}, ["Auto", "V4L"]),
        ("Darwin", {cv2.CAP_AVFOUNDATION}, ["Auto", "AVFoundation"]),
    ],
)
def test_only_the_backends_opencv_has_are_offered(
    dialog, monkeypatch, system, available, offered
):
    # choosing a backend the build doesn't have would make the camera fail to open
    import video_settings

    monkeypatch.setattr(video_settings.platform, "system", lambda: system)
    monkeypatch.setattr(
        video_settings.cv2.videoio_registry,
        "hasBackend",
        lambda backend: backend in available,
    )
    dialog.init_ui(FakeDeviceCapture())
    combo = dialog.ui.comboBox_captureBackend
    assert [combo.itemText(i) for i in range(combo.count())] == offered


def test_every_offered_backend_is_in_this_opencv_build(dialog):
    dialog.init_ui(FakeDeviceCapture())
    combo = dialog.ui.comboBox_captureBackend
    for i in range(1, combo.count()):
        assert cv2.videoio_registry.hasBackend(combo.itemData(i))

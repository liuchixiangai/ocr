import math

import cv2
import platform

from PySide6.QtWidgets import QDialog
from camera_thread import (
    DEFAULT_CAPTURE_FPS,
    MAX_CAPTURE_FPS,
    MIN_CAPTURE_FPS,
    OpenCVVideoCaptureWithSettings,
    is_bad_low_fps,
    usable_capture_fps,
)
from ui_video_settings import Ui_Dialog as Ui_VideoSettingsDialog
from storage import fetch_data, store_data


# DirectShow media subtypes whose first four GUID bytes OpenCV returns from
# CAP_PROP_FOURCC. They are not FOURCCs: RGB24 (0xE436EB7D) used to be shown
# as "}ë6ä" / "jë6ä". Printable codes (MJPG, YUY2, NV12, ...) are decoded from
# the bytes instead.
_DSHOW_SUBTYPE_NAMES = {
    0xE436EB78: "RGB1",
    0xE436EB79: "RGB4",
    0xE436EB7A: "RGB8",
    0xE436EB7B: "RGB565",
    0xE436EB7C: "RGB555",
    0xE436EB7D: "RGB24",
    0xE436EB7E: "RGB32",
    0x773C9AC0: "ARGB32",
}


def fourcc_to_str(fourcc) -> str | None:
    """A readable format name, or None when the device didn't report one.

    Never returns the raw GUID bytes as Latin-1. Callers show "driver default"
    instead of letting the user pick a garbled name.
    """
    if isinstance(fourcc, str):
        text = fourcc.strip()
        if len(text) == 4 and text.isascii() and text.isalnum() and text != "NULL":
            return text
        return None
    try:
        code = int(fourcc) & 0xFFFFFFFF
    except (TypeError, ValueError, OverflowError):
        return None
    if code == 0:
        return None
    named = _DSHOW_SUBTYPE_NAMES.get(code)
    if named:
        return named
    raw = bytes((code >> (8 * i)) & 0xFF for i in range(4))
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    if text.isalnum():
        return text
    return None


def resolution_text(width, height) -> str:
    return f"{int(width)}x{int(height)}"


def adopted_resolution(width, height) -> str | None:
    """WxH when both sides look like a real mode, else None (0 and -1 are not)."""
    try:
        w = int(round(float(width)))
        h = int(round(float(height)))
    except (TypeError, ValueError):
        return None
    if w < 16 or h < 16:
        return None
    return resolution_text(w, h)


def platform_backends(system: str) -> list[tuple[str, int]]:
    """The capture backends offered on a platform (platform.system()), besides
    Auto, as (name, cv2 backend id)."""
    if system == "Windows":
        return [
            ("DShow", cv2.CAP_DSHOW),
            ("Microsoft Media Foundation", cv2.CAP_MSMF),
            ("Microsoft Windows Runtime", cv2.CAP_WINRT),
        ]
    if system == "Linux":
        return [("V4L", cv2.CAP_V4L), ("GStreamer", cv2.CAP_GSTREAMER)]
    if system == "Darwin":
        return [("AVFoundation", cv2.CAP_AVFOUNDATION)]
    return []


def backend_name(backend) -> str | None:
    """A short name for an OpenCV backend id, or None when it wasn't reported."""
    try:
        backend = int(backend)
    except (TypeError, ValueError):
        return None
    if backend <= 0:
        return None
    names = {
        value: name
        for name, value in platform_backends("Windows")
        + platform_backends("Linux")
        + platform_backends("Darwin")
    }
    return names.get(backend, str(backend))


def has_backend(backend: int) -> bool:
    """Whether this OpenCV build has the capture backend."""
    try:
        return bool(cv2.videoio_registry.hasBackend(backend))
    except (AttributeError, cv2.error):
        return False


def choose_dialog_fps(reported, stored) -> tuple[int, str]:
    """(frame rate to show, why).

    "device": the device reported a usable rate (above 1).
    "correct-to-device": a saved rate below 5 (the old dialog saved 1) is
    replaced immediately with the usable rate the device just reported.
    "correct-now": that bad rate, and no usable device rate, is replaced
    with 25 immediately.
    "reapply": the device reports that bad rate but a usable one is saved;
    show the saved one and push it to the device again.
    "keep-stored": the device didn't report a rate; show the saved one.
    "suggest-default": nothing usable; show 25 and save it only if the dialog
    is accepted. Cancel leaves the driver's own rate.
    """
    stored_ok = usable_capture_fps(stored)
    reported_ok = usable_capture_fps(reported)
    if is_bad_low_fps(stored) and reported_ok is not None:
        return reported_ok, "correct-to-device"
    if is_bad_low_fps(stored) or (is_bad_low_fps(reported) and stored_ok is None):
        return DEFAULT_CAPTURE_FPS, "correct-now"
    if is_bad_low_fps(reported) and stored_ok is not None:
        return stored_ok, "reapply"
    if reported_ok is not None:
        return reported_ok, "device"
    if stored_ok is not None:
        return stored_ok, "keep-stored"
    return DEFAULT_CAPTURE_FPS, "suggest-default"


def format_reported_fps(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(number):
        return "n/a"
    return f"{number:g}"


def prop_is_reported(value) -> bool:
    """False for the 0 and -1 OpenCV uses when a backend has no such property."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def format_prop_line(name: str, value) -> str | None:
    """One "CAP_PROP_…: …" line, or None when the device didn't report it.

    FOURCC is the format name (RGB24, MJPG), never the raw GUID number.
    """
    if name == "CAP_PROP_FOURCC":
        text = fourcc_to_str(value)
        if not text:
            return None
        return f"{name}: {text}"
    if name == "CAP_PROP_BACKEND":
        text = backend_name(value)
        if not text:
            return None
        return f"{name}: {text}"
    if name == "CAP_PROP_FPS":
        fps = usable_capture_fps(value)
        if fps is None:
            return None
        return f"{name}: {fps}"
    if not prop_is_reported(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        shown = str(int(number))
    else:
        shown = f"{number:g}"
    return f"{name}: {shown}"


# Properties read once for the details box. Unsupported ones (0 or -1) are
# not shown: a wall of them made capture cards look broken.
_VIDEO_PROPS = [
    (cv2.CAP_PROP_POS_MSEC, "CAP_PROP_POS_MSEC"),
    (cv2.CAP_PROP_POS_FRAMES, "CAP_PROP_POS_FRAMES"),
    (cv2.CAP_PROP_POS_AVI_RATIO, "CAP_PROP_POS_AVI_RATIO"),
    (cv2.CAP_PROP_FRAME_WIDTH, "CAP_PROP_FRAME_WIDTH"),
    (cv2.CAP_PROP_FRAME_HEIGHT, "CAP_PROP_FRAME_HEIGHT"),
    (cv2.CAP_PROP_FPS, "CAP_PROP_FPS"),
    (cv2.CAP_PROP_FOURCC, "CAP_PROP_FOURCC"),
    (cv2.CAP_PROP_FRAME_COUNT, "CAP_PROP_FRAME_COUNT"),
    (cv2.CAP_PROP_FORMAT, "CAP_PROP_FORMAT"),
    (cv2.CAP_PROP_MODE, "CAP_PROP_MODE"),
    (cv2.CAP_PROP_BRIGHTNESS, "CAP_PROP_BRIGHTNESS"),
    (cv2.CAP_PROP_CONTRAST, "CAP_PROP_CONTRAST"),
    (cv2.CAP_PROP_SATURATION, "CAP_PROP_SATURATION"),
    (cv2.CAP_PROP_HUE, "CAP_PROP_HUE"),
    (cv2.CAP_PROP_GAIN, "CAP_PROP_GAIN"),
    (cv2.CAP_PROP_EXPOSURE, "CAP_PROP_EXPOSURE"),
    (cv2.CAP_PROP_CONVERT_RGB, "CAP_PROP_CONVERT_RGB"),
    (cv2.CAP_PROP_WHITE_BALANCE_BLUE_U, "CAP_PROP_WHITE_BALANCE_BLUE_U"),
    (cv2.CAP_PROP_RECTIFICATION, "CAP_PROP_RECTIFICATION"),
    (cv2.CAP_PROP_MONOCHROME, "CAP_PROP_MONOCHROME"),
    (cv2.CAP_PROP_SHARPNESS, "CAP_PROP_SHARPNESS"),
    (cv2.CAP_PROP_AUTO_EXPOSURE, "CAP_PROP_AUTO_EXPOSURE"),
    (cv2.CAP_PROP_GAMMA, "CAP_PROP_GAMMA"),
    (cv2.CAP_PROP_TEMPERATURE, "CAP_PROP_TEMPERATURE"),
    (cv2.CAP_PROP_TRIGGER, "CAP_PROP_TRIGGER"),
    (cv2.CAP_PROP_TRIGGER_DELAY, "CAP_PROP_TRIGGER_DELAY"),
    (cv2.CAP_PROP_WHITE_BALANCE_RED_V, "CAP_PROP_WHITE_BALANCE_RED_V"),
    (cv2.CAP_PROP_ZOOM, "CAP_PROP_ZOOM"),
    (cv2.CAP_PROP_FOCUS, "CAP_PROP_FOCUS"),
    (cv2.CAP_PROP_GUID, "CAP_PROP_GUID"),
    (cv2.CAP_PROP_ISO_SPEED, "CAP_PROP_ISO_SPEED"),
    (cv2.CAP_PROP_BACKLIGHT, "CAP_PROP_BACKLIGHT"),
    (cv2.CAP_PROP_PAN, "CAP_PROP_PAN"),
    (cv2.CAP_PROP_TILT, "CAP_PROP_TILT"),
    (cv2.CAP_PROP_ROLL, "CAP_PROP_ROLL"),
    (cv2.CAP_PROP_IRIS, "CAP_PROP_IRIS"),
    (cv2.CAP_PROP_SETTINGS, "CAP_PROP_SETTINGS"),
    (cv2.CAP_PROP_BUFFERSIZE, "CAP_PROP_BUFFERSIZE"),
    (cv2.CAP_PROP_AUTOFOCUS, "CAP_PROP_AUTOFOCUS"),
    (cv2.CAP_PROP_SAR_NUM, "CAP_PROP_SAR_NUM"),
    (cv2.CAP_PROP_SAR_DEN, "CAP_PROP_SAR_DEN"),
    (cv2.CAP_PROP_BACKEND, "CAP_PROP_BACKEND"),
    (cv2.CAP_PROP_CHANNEL, "CAP_PROP_CHANNEL"),
    (cv2.CAP_PROP_AUTO_WB, "CAP_PROP_AUTO_WB"),
    (cv2.CAP_PROP_WB_TEMPERATURE, "CAP_PROP_WB_TEMPERATURE"),
    (cv2.CAP_PROP_CODEC_PIXEL_FORMAT, "CAP_PROP_CODEC_PIXEL_FORMAT"),
    (cv2.CAP_PROP_BITRATE, "CAP_PROP_BITRATE"),
    (cv2.CAP_PROP_ORIENTATION_META, "CAP_PROP_ORIENTATION_META"),
    (cv2.CAP_PROP_ORIENTATION_AUTO, "CAP_PROP_ORIENTATION_AUTO"),
    (cv2.CAP_PROP_HW_ACCELERATION, "CAP_PROP_HW_ACCELERATION"),
    (cv2.CAP_PROP_HW_DEVICE, "CAP_PROP_HW_DEVICE"),
    (
        cv2.CAP_PROP_HW_ACCELERATION_USE_OPENCL,
        "CAP_PROP_HW_ACCELERATION_USE_OPENCL",
    ),
    (cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, "CAP_PROP_OPEN_TIMEOUT_MSEC"),
    (cv2.CAP_PROP_READ_TIMEOUT_MSEC, "CAP_PROP_READ_TIMEOUT_MSEC"),
    (cv2.CAP_PROP_STREAM_OPEN_TIME_USEC, "CAP_PROP_STREAM_OPEN_TIME_USEC"),
    (cv2.CAP_PROP_VIDEO_TOTAL_CHANNELS, "CAP_PROP_VIDEO_TOTAL_CHANNELS"),
    (cv2.CAP_PROP_VIDEO_STREAM, "CAP_PROP_VIDEO_STREAM"),
    (cv2.CAP_PROP_AUDIO_STREAM, "CAP_PROP_AUDIO_STREAM"),
    (cv2.CAP_PROP_AUDIO_POS, "CAP_PROP_AUDIO_POS"),
    (cv2.CAP_PROP_AUDIO_SHIFT_NSEC, "CAP_PROP_AUDIO_SHIFT_NSEC"),
    (cv2.CAP_PROP_AUDIO_DATA_DEPTH, "CAP_PROP_AUDIO_DATA_DEPTH"),
    (
        cv2.CAP_PROP_AUDIO_SAMPLES_PER_SECOND,
        "CAP_PROP_AUDIO_SAMPLES_PER_SECOND",
    ),
    (cv2.CAP_PROP_AUDIO_BASE_INDEX, "CAP_PROP_AUDIO_BASE_INDEX"),
    (cv2.CAP_PROP_AUDIO_TOTAL_CHANNELS, "CAP_PROP_AUDIO_TOTAL_CHANNELS"),
    (cv2.CAP_PROP_AUDIO_TOTAL_STREAMS, "CAP_PROP_AUDIO_TOTAL_STREAMS"),
    (cv2.CAP_PROP_AUDIO_SYNCHRONIZE, "CAP_PROP_AUDIO_SYNCHRONIZE"),
    (cv2.CAP_PROP_LRF_HAS_KEY_FRAME, "CAP_PROP_LRF_HAS_KEY_FRAME"),
    (cv2.CAP_PROP_CODEC_EXTRADATA_INDEX, "CAP_PROP_CODEC_EXTRADATA_INDEX"),
    (cv2.CAP_PROP_FRAME_TYPE, "CAP_PROP_FRAME_TYPE"),
    (cv2.CAP_PROP_N_THREADS, "CAP_PROP_N_THREADS"),
]


def read_device_snapshot(capture) -> dict:
    """fps, size, format, backend and the property dump, from one locked read.

    Nothing is set on the device: trying formats used to break virtual cameras
    (ToDesk, vMix) and left the property list full of zeros.
    """
    props = {name: capture.get(prop) for prop, name in _VIDEO_PROPS}
    return {
        "fps": props.get("CAP_PROP_FPS"),
        "width": props.get("CAP_PROP_FRAME_WIDTH"),
        "height": props.get("CAP_PROP_FRAME_HEIGHT"),
        "fourcc": props.get("CAP_PROP_FOURCC"),
        "backend": props.get("CAP_PROP_BACKEND"),
        "props": props,
    }


class VideoSettingsDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.ui = Ui_VideoSettingsDialog()
        self.ui.setupUi(self)
        self.ui.buttonBox.accepted.connect(self.save_settings)
        self.ui.buttonBox.rejected.connect(self.close)
        self.ui.spinBox_fps.setRange(MIN_CAPTURE_FPS, MAX_CAPTURE_FPS)
        self.cap = None
        self.initial_values = {}
        self._fps_reason = "device"

    def init_ui(self, cap: OpenCVVideoCaptureWithSettings | None):
        self.cap = cap
        self.initial_values = {}
        self._fps_reason = "device"
        self.ui.label_fpsNote.hide()
        if cap is None:
            # the source is (re)connecting
            self.ui.label_formatValue.setText(self._format_label(None))
            self.ui.spinBox_fps.setValue(DEFAULT_CAPTURE_FPS)
            self.ui.plainTextEdit_videoProps.setPlainText(
                self.tr("The video source is not open.")
            )
            return
        # one wait on the capture thread, and no CAP_PROP_FOURCC probing:
        # setting formats on a live DirectShow graph drops virtual cameras
        snapshot = cap.run_locked(read_device_snapshot)
        self._show_format(snapshot["fourcc"])
        self._show_fps(snapshot["fps"])
        self._show_resolution(snapshot["width"], snapshot["height"])
        self._show_props(snapshot["props"])
        self.populate_backend_combo(snapshot["backend"])
        self.initial_values = self.current_values()

    def _format_label(self, fourcc) -> str:
        name = fourcc_to_str(fourcc)
        if name:
            return self.tr("{format} (chosen by the driver, not editable)").format(
                format=name
            )
        return self.tr("Driver default (not editable)")

    def _show_format(self, fourcc):
        self.ui.label_formatValue.setText(self._format_label(fourcc))

    def _show_fps(self, reported):
        stored = fetch_data("scoresight.json", "fps", None)
        shown, reason = choose_dialog_fps(reported, stored)
        self._fps_reason = reason
        self.ui.spinBox_fps.setValue(shown)
        if reason in ("correct-now", "correct-to-device") and stored != shown:
            store_data("scoresight.json", "fps", shown)
        elif reason == "reapply":
            # push the saved rate again; the device is sitting on a bad one
            store_data("scoresight.json", "fps", shown)
        note = self._fps_note(reason, reported, shown)
        self.ui.label_fpsNote.setVisible(note is not None)
        if note is not None:
            self.ui.label_fpsNote.setText(note)

    def _fps_note(self, reason: str, reported, shown: int) -> str | None:
        if reason == "device":
            return None
        reported_text = format_reported_fps(reported)
        if reason == "correct-now":
            return self.tr(
                "The frame rate ({reported}) isn't usable — 1 would lock the "
                "capture at one frame per second. It has been changed to {fps}."
            ).format(reported=reported_text, fps=shown)
        if reason == "correct-to-device":
            return self.tr(
                "The saved frame rate isn't usable — 1 would lock the capture "
                "at one frame per second. Using the device's {fps}."
            ).format(fps=shown)
        if reason in ("keep-stored", "reapply"):
            return self.tr(
                "The device didn't report a usable frame rate ({reported}). "
                "Keeping the saved {fps}."
            ).format(reported=reported_text, fps=shown)
        if reason == "suggest-default":
            return self.tr(
                "The device didn't report a usable frame rate ({reported}). "
                "Showing {fps}. Click OK to save it; Cancel leaves the driver's "
                "own rate. It is never saved as 1."
            ).format(reported=reported_text, fps=shown)
        return None

    def _show_resolution(self, width, height):
        choice = adopted_resolution(width, height)
        combo = self.ui.comboBox_resolution
        if choice is None:
            combo.setCurrentIndex(0)
            return
        if combo.findText(choice) < 0:
            combo.addItem(choice)
        combo.setCurrentText(choice)

    def _show_props(self, props: dict):
        lines = []
        for _prop, name in _VIDEO_PROPS:
            line = format_prop_line(name, props.get(name))
            if line is not None:
                lines.append(line)
        if not lines:
            text = self.tr("This device didn't report any further details.")
        else:
            text = "\n".join(lines)
        self.ui.plainTextEdit_videoProps.setPlainText(text)

    def populate_backend_combo(self, backend):
        self.ui.comboBox_captureBackend.clear()
        self.ui.comboBox_captureBackend.addItem("Auto", cv2.CAP_ANY)
        # only the backends this OpenCV build has (the pip wheels have no
        # Media Foundation/Windows Runtime or GStreamer, for example)
        for name, candidate in platform_backends(platform.system()):
            if has_backend(candidate):
                self.ui.comboBox_captureBackend.addItem(name, candidate)
        try:
            backend = int(backend)
        except (TypeError, ValueError):
            backend = cv2.CAP_ANY
        index = self.ui.comboBox_captureBackend.findData(backend)
        if index >= 0:
            self.ui.comboBox_captureBackend.setCurrentIndex(index)

    def current_values(self) -> dict:
        resolution = self.ui.comboBox_resolution.currentText()
        if "x" in resolution:
            resolution = resolution.split("x")
            width = int(resolution[0])
            height = int(resolution[1])
        else:
            width = -1
            height = -1
        # the format is not a choice: a garbled GUID used to be saved and
        # then applied, which broke the camera
        return {
            "fps": self.ui.spinBox_fps.value(),
            "width": width,
            "height": height,
            "backend": self.ui.comboBox_captureBackend.currentData(),
        }

    def save_settings(self):
        # Store only what the user changed: each stored setting is applied to
        # the running capture, and a backend change reopens the device.
        if self.cap is not None:
            current = self.current_values()
            for name, value in current.items():
                if value != self.initial_values.get(name):
                    store_data("scoresight.json", name, value)
            if (
                self._fps_reason == "suggest-default"
                and current.get("fps") == DEFAULT_CAPTURE_FPS
                and fetch_data("scoresight.json", "fps", None) != DEFAULT_CAPTURE_FPS
            ):
                store_data("scoresight.json", "fps", DEFAULT_CAPTURE_FPS)

        self.close()

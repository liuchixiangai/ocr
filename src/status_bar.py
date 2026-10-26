"""The main window's status bar: the source with its resolution and frame rate,
the OCR rate against the Detections / s setting, a chip while the outputs are
paused, the state of every output (a click shows its settings), and the HTTP
server's address with a Copy button.

MainWindow builds a StatusSnapshot from what it already knows (the capture
thread's counters, the pause state, the HTTP server's port, the outputs'
statuses) about once a second, and MainStatusBar shows it. Nothing here waits
for the network.
"""

import html
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from PySide6.QtCore import QCoreApplication, QObject, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStatusBar,
    QToolButton,
    QToolTip,
    QWidget,
)

import ocr_models
import ppocr_engine
import theme
from product_brand import (
    QQ_GROUP_NUMBER,
    SIMPLIFY_OUTPUT_UI,
)
from camera_info import CameraInfo
from output_status import (
    DESTINATIONS,
    STATE_COLORS,
    STATE_SHAPES,
    DestinationStatus,
    short_name,
    state_word,
    status_tooltip,
)

# Source states
SOURCE_NONE = "none"
SOURCE_OPENING = "opening"
SOURCE_RUNNING = "running"
# frames arrive, at less than SLOW_SOURCE_SHARE of the source's frame rate
SOURCE_SLOW = "slow"
# reads fail: the capture retries and reopens the source
SOURCE_RECONNECTING = "reconnecting"
SOURCE_STALLED = "stalled"
SOURCE_ERROR = "error"

# An OCR rate below this share of the Detections / s setting is highlighted:
# the computer can't keep up.
SLOW_OCR_SHARE = 0.7
# A source delivering less than this share of its own frame rate is slow.
SLOW_SOURCE_SHARE = 0.5
# This many failed reads in a row: the source is reconnecting.
RECONNECTING_FAILED_READS = 3
# A source that delivered no frame for this long is shown as stalled.
SOURCE_STALL_S = 3.0

# (glyph, theme colour) of each source state; the glyph's shape tells the
# states apart without colour
_SOURCE_LOOK = {
    SOURCE_NONE: ("○", None),
    SOURCE_OPENING: ("▲", "rejected"),
    SOURCE_RUNNING: ("●", "new"),
    SOURCE_SLOW: ("▲", "rejected"),
    SOURCE_RECONNECTING: ("▲", "rejected"),
    SOURCE_STALLED: ("▲", "rejected"),
    SOURCE_ERROR: ("■", "empty"),
}
# the shapes of theme.draw_state_shape() for the glyphs
_GLYPH_SHAPES = {"○": "ring", "▲": "triangle", "●": "disc", "■": "square"}


def source_look(state: str) -> tuple[str, str | None]:
    """The (theme.draw_state_shape() shape, theme colour name or None) of a
    source state."""
    glyph, color_name = _SOURCE_LOOK.get(state, _SOURCE_LOOK[SOURCE_NONE])
    return _GLYPH_SHAPES[glyph], color_name


def source_state(
    *,
    has_source: bool,
    error: bool = False,
    first_frame: bool = False,
    now: float = 0.0,
    last_frame_at: float | None = None,
    fps: float | None = None,
    nominal_fps: float | None = None,
    failed_reads: int = 0,
) -> str:
    """The state of the source, from what the capture thread reports: its last
    frame's time, its frame rate (against the source's own, nominal_fps, when
    the source tells it) and the reads that failed in a row."""
    if not has_source:
        return SOURCE_NONE
    if error:
        return SOURCE_ERROR
    if not first_frame:
        return SOURCE_OPENING
    if failed_reads >= RECONNECTING_FAILED_READS:
        return SOURCE_RECONNECTING
    if last_frame_at is None or now - last_frame_at > SOURCE_STALL_S:
        return SOURCE_STALLED
    if nominal_fps and fps is not None and fps < SLOW_SOURCE_SHARE * nominal_fps:
        return SOURCE_SLOW
    return SOURCE_RUNNING


@dataclass(frozen=True)
class StatusSnapshot:
    source_name: str | None = None
    source_state: str = SOURCE_NONE
    resolution: tuple[int, int] | None = None
    fps: float | None = None
    # the source's own frame rate, if it tells it
    nominal_fps: float | None = None
    # detection passes per second, and the Detections / s setting
    ocr_rate: float | None = None
    ocr_target: float | None = None
    # the OCR model chosen (an ocr_models value), the PP-OCR model's state
    # (ppocr_engine.LOADING, ...; None for Tesseract) and why it couldn't load
    # (the boxes are then read with Tesseract)
    ocr_model: int | str | None = None
    ocr_engine_state: str | None = None
    ocr_engine_error: str | None = None
    # PP-OCR (automatic): the model it reads with, why (ppocr_engine.AUTO_*),
    # accurate's time per reading (ms) and the time between readings (ms)
    ocr_auto_model: str | None = None
    ocr_auto_reason: str | None = None
    ocr_auto_accurate_ms: float | None = None
    ocr_auto_interval_ms: float | None = None
    # seconds since the outputs were paused, None while they aren't
    paused_seconds: float | None = None
    # OCR training data is being collected (Tools > Collect OCR training
    # data), into this folder
    collecting_training_data: bool = False
    training_data_folder: str = ""
    # the picture adjustments in effect: the rotation in degrees, whether the
    # picture is mirrored, and whether it is cropped, corrected with four
    # corners and stabilized
    rotation: int = 0
    flip_horizontal: bool = False
    flip_vertical: bool = False
    cropped: bool = False
    four_corner_corrected: bool = False
    stabilized: bool = False
    # the HTTP server's URL, None when it isn't running
    http_url: str | None = None
    http_urls: tuple[str, ...] = ()
    # the port the HTTP server was asked to use
    http_port: int | None = None
    # the outputs' statuses (output_status.DestinationStatus), and the time
    # (time.monotonic) they were taken
    outputs: tuple[DestinationStatus, ...] = ()
    now: float = 0.0
    # why the camera list is empty or the search failed ("" when a device
    # was found, or the search has not finished). Shown instead of "No source"
    # only while nothing is open.
    device_enum_headline: str = ""
    device_enum_detail: str = ""


def primary_lan_address() -> str | None:
    """The IPv4 address of the interface that has the default route, or None
    (no network). Connecting a UDP socket only looks up the route: nothing is
    sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            # TEST-NET-1 (RFC 5737): never reached, only routed
            probe.connect(("192.0.2.1", 9))
            address = probe.getsockname()[0]
    except OSError:
        return None
    if not address or address.startswith(("0.", "127.", "169.254.")):
        return None
    return address


def http_server_urls(port: int, lan_address: str | None) -> tuple[str, ...]:
    """The HTTP server's URLs: on the LAN address first (for other computers),
    then on localhost."""
    urls = [f"http://localhost:{port}"]
    if lan_address:
        urls.insert(0, f"http://{lan_address}:{port}")
    return tuple(urls)


def without_credentials(url: str) -> str:
    """A URL without the user name and password (rtsp://user:pass@host/...)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def source_display_name(camera_info: CameraInfo | None) -> str | None:
    """A short name for a source: a file's name, a stream's URL (without its
    password), "Screen capture", or a camera's or NDI source's name."""
    if camera_info is None:
        return None
    if camera_info.type == CameraInfo.CameraType.FILE:
        path = str(camera_info.id)
        return path.replace("\\", "/").rsplit("/", 1)[-1] or path
    if camera_info.type == CameraInfo.CameraType.URL:
        return without_credentials(str(camera_info.id))
    if camera_info.type == CameraInfo.CameraType.SCREEN_CAPTURE:
        return QCoreApplication.translate("StatusBar", "Screen capture")
    return str(camera_info.description)


def format_duration(seconds: float) -> str:
    """mm:ss, or h:mm:ss from an hour on."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _elide_middle(text: str, limit: int = 48) -> str:
    if len(text) <= limit:
        return text
    keep = (limit - 1) // 2
    return f"{text[:keep]}…{text[-keep:]}"


def source_text(snapshot: StatusSnapshot) -> str:
    """The source part, as plain text."""
    state = snapshot.source_state
    if (state == SOURCE_NONE or not snapshot.source_name) and (
        snapshot.device_enum_headline
    ):
        return snapshot.device_enum_headline
    if state == SOURCE_NONE or not snapshot.source_name:
        return QCoreApplication.translate("StatusBar", "No source")
    name = _elide_middle(snapshot.source_name)
    if state == SOURCE_OPENING:
        return QCoreApplication.translate("StatusBar", "Opening {source}…").format(
            source=name
        )
    if state == SOURCE_ERROR:
        return QCoreApplication.translate(
            "StatusBar", "{source}: can't be read"
        ).format(source=name)
    if state == SOURCE_STALLED:
        return QCoreApplication.translate(
            "StatusBar", "{source}: no new frames"
        ).format(source=name)
    if state == SOURCE_RECONNECTING:
        return QCoreApplication.translate(
            "StatusBar", "{source}: reconnecting…"
        ).format(source=name)
    parts = [name]
    if snapshot.resolution and all(snapshot.resolution):
        width, height = snapshot.resolution
        parts.append(f"{int(width)}×{int(height)}")
    if snapshot.fps is not None:
        if state == SOURCE_SLOW and snapshot.nominal_fps:
            parts.append(
                QCoreApplication.translate(
                    "StatusBar", "slow: {fps} of {nominal} fps"
                ).format(
                    fps=f"{snapshot.fps:.1f}", nominal=f"{snapshot.nominal_fps:.0f}"
                )
            )
        else:
            parts.append(
                QCoreApplication.translate("StatusBar", "{fps} fps").format(
                    fps=f"{snapshot.fps:.1f}"
                )
            )
    return " · ".join(parts)


def adjustments_text(snapshot: StatusSnapshot) -> str:
    """The picture adjustments in effect ("Adjust picture: rotated 90°,
    cropped"), or "" without any."""
    parts = []
    if snapshot.rotation:
        parts.append(
            QCoreApplication.translate("StatusBar", "rotated {angle}°").format(
                angle=snapshot.rotation
            )
        )
    if snapshot.flip_horizontal:
        parts.append(QCoreApplication.translate("StatusBar", "flipped horizontally"))
    if snapshot.flip_vertical:
        parts.append(QCoreApplication.translate("StatusBar", "flipped vertically"))
    if snapshot.cropped:
        parts.append(QCoreApplication.translate("StatusBar", "cropped"))
    if snapshot.four_corner_corrected:
        parts.append(QCoreApplication.translate("StatusBar", "4-corner corrected"))
    if snapshot.stabilized:
        parts.append(QCoreApplication.translate("StatusBar", "stabilized"))
    if not parts:
        return ""
    return QCoreApplication.translate("StatusBar", "Adjust picture: {list}").format(
        list=", ".join(parts)
    )


def source_state_description(state: str) -> str:
    """One sentence about a source state, for the source's health dot."""
    if state == SOURCE_OPENING:
        return QCoreApplication.translate("StatusBar", "Opening the source…")
    if state == SOURCE_RUNNING:
        return QCoreApplication.translate("StatusBar", "Frames are arriving.")
    if state == SOURCE_SLOW:
        return QCoreApplication.translate(
            "StatusBar",
            "Frames arrive at less than half the source's frame rate: the "
            "computer or the network can't keep up.",
        )
    if state == SOURCE_RECONNECTING:
        return QCoreApplication.translate(
            "StatusBar", "No frames: reconnecting to the source…"
        )
    if state == SOURCE_STALLED:
        return QCoreApplication.translate(
            "StatusBar", "No new frames for a few seconds."
        )
    if state == SOURCE_ERROR:
        return QCoreApplication.translate("StatusBar", "The source can't be read.")
    return QCoreApplication.translate("StatusBar", "No source is open.")


def ocr_loading(snapshot: StatusSnapshot) -> bool:
    """A PP-OCR model is loading: the boxes have no reading yet."""
    return snapshot.ocr_engine_state in (ppocr_engine.NOT_LOADED, ppocr_engine.LOADING)


def ocr_fallback(snapshot: StatusSnapshot) -> bool:
    """PP-OCR couldn't load: Tesseract reads the boxes."""
    return snapshot.ocr_engine_state == ppocr_engine.FAILED


def ocr_text(snapshot: StatusSnapshot) -> str:
    """The OCR rate part ("" when no source is running)."""
    if snapshot.source_state != SOURCE_RUNNING or snapshot.ocr_rate is None:
        return ""
    if ocr_loading(snapshot):
        return QCoreApplication.translate("StatusBar", "Loading PP-OCR…")
    if snapshot.ocr_target:
        text = QCoreApplication.translate(
            "StatusBar", "OCR {rate}/s of {target}"
        ).format(rate=f"{snapshot.ocr_rate:.1f}", target=f"{snapshot.ocr_target:g}")
    else:
        text = QCoreApplication.translate("StatusBar", "OCR {rate}/s").format(
            rate=f"{snapshot.ocr_rate:.1f}"
        )
    if ocr_fallback(snapshot):
        text += " · " + QCoreApplication.translate(
            "StatusBar", "with Tesseract: PP-OCR didn't load"
        )
    return text


def ocr_is_slow(snapshot: StatusSnapshot) -> bool:
    return bool(
        snapshot.source_state == SOURCE_RUNNING
        and not ocr_loading(snapshot)
        and snapshot.ocr_rate is not None
        and snapshot.ocr_target
        and snapshot.ocr_rate < SLOW_OCR_SHARE * snapshot.ocr_target
    )


def ppocr_accurate_is_slow(snapshot: StatusSnapshot) -> bool:
    """PP-OCR (accurate) reads the boxes and can't keep up with Detections / s:
    PP-OCR (fast) or fewer detections would."""
    return (
        ocr_is_slow(snapshot)
        and snapshot.ocr_model == ocr_models.PPOCR_ACCURATE
        and snapshot.ocr_engine_state == ppocr_engine.READY
    )


def ppocr_auto_text(
    model: str | None,
    reason: str | None,
    accurate_ms: float | None,
    interval_ms: float | None,
) -> str:
    """Which model PP-OCR (automatic) reads with and why, e.g. "PP-OCR
    automatic: fast (accurate needs 110 ms per reading here, more than 70% of
    the 100 ms between readings)"; "" before it has read."""
    if model is None:
        return ""
    ms = "?" if accurate_ms is None else f"{accurate_ms:.0f}"
    interval = "?" if interval_ms is None else f"{interval_ms:.0f}"
    if model == ppocr_engine.TINY:
        if reason == ppocr_engine.AUTO_UNAVAILABLE:
            return QCoreApplication.translate(
                "StatusBar", "PP-OCR automatic: fast (accurate didn't load)"
            )
        return QCoreApplication.translate(
            "StatusBar",
            "PP-OCR automatic: fast (accurate needs {ms} ms per reading here, more "
            "than {share}% of the {interval} ms between readings)",
        ).format(
            ms=ms,
            share=f"{ppocr_engine.AUTO_SLOW_SHARE * 100:.0f}",
            interval=interval,
        )
    if reason == ppocr_engine.AUTO_KEEPS_UP and accurate_ms is not None:
        return QCoreApplication.translate(
            "StatusBar",
            "PP-OCR automatic: accurate (it needs {ms} ms per reading here, of the "
            "{interval} ms between readings)",
        ).format(ms=ms, interval=interval)
    return QCoreApplication.translate(
        "StatusBar", "PP-OCR automatic: accurate (measuring how long it needs here)"
    )


def snapshot_auto_text(snapshot: StatusSnapshot) -> str:
    if snapshot.ocr_model != ocr_models.PPOCR_AUTO or ocr_loading(snapshot):
        return ""
    if ocr_fallback(snapshot):
        return ""
    return ppocr_auto_text(
        snapshot.ocr_auto_model,
        snapshot.ocr_auto_reason,
        snapshot.ocr_auto_accurate_ms,
        snapshot.ocr_auto_interval_ms,
    )


def ppocr_cpu_advice() -> str:
    return QCoreApplication.translate(
        "StatusBar",
        "PP-OCR (accurate) needs more CPU than this computer has for these boxes "
        "at this rate. Choose PP-OCR (fast) as the OCR model (it needs about a "
        "fifth of the CPU), or lower Detections / s.",
    )


def ocr_tooltip(snapshot: StatusSnapshot) -> str:
    """The OCR rate part's tooltip: what it means, and what to do about a
    computer that can't keep up or a PP-OCR that couldn't load."""
    tooltip = QCoreApplication.translate(
        "StatusBar",
        "How many times per second the boxes are read (OCR), and the Detections / s "
        "setting. This is not the camera or file frame rate: that is the source's "
        "frames per second. The preview aims for about 15 pictures per second when "
        "the computer can keep up.",
    )
    if ocr_loading(snapshot):
        tooltip += "\n" + QCoreApplication.translate(
            "StatusBar",
            "The PP-OCR model is loading; the boxes are read as soon as it is ready.",
        )
    auto = snapshot_auto_text(snapshot)
    if auto:
        tooltip += "\n" + auto
    if ppocr_accurate_is_slow(snapshot):
        tooltip += "\n" + ppocr_cpu_advice()
    elif ocr_is_slow(snapshot):
        tooltip += "\n" + QCoreApplication.translate(
            "StatusBar",
            "The computer can't keep up: lower Detections / s, use fewer boxes or "
            "a lower source resolution.",
        )
    if ocr_fallback(snapshot):
        tooltip += "\n" + QCoreApplication.translate(
            "StatusBar",
            "PP-OCR couldn't load ({error}), so the boxes are read with Tesseract "
            "(General Scoreboard). The log has the details.",
        ).format(error=snapshot.ocr_engine_error or "?")
    return tooltip


def paused_text(snapshot: StatusSnapshot) -> str:
    if snapshot.paused_seconds is None:
        return ""
    return QCoreApplication.translate("StatusBar", "PAUSED {elapsed}").format(
        elapsed=format_duration(snapshot.paused_seconds)
    )


def http_text(snapshot: StatusSnapshot) -> str:
    if snapshot.http_url:
        return snapshot.http_url
    return QCoreApplication.translate(
        "StatusBar", "HTTP server not running (port {port} busy)"
    ).format(port=snapshot.http_port)


def _set_style_sheet(widget, style_sheet: str):
    # called every second: restyle only on a change
    if widget.styleSheet() != style_sheet:
        widget.setStyleSheet(style_sheet)


def _colored(glyph: str, color) -> str:
    if color is None:
        return glyph
    return f'<span style="color:{color.name()}">{glyph}</span>'


class MainStatusBar(QObject):
    """The permanent widgets of the main window's QStatusBar."""

    # an output's button was clicked: show that destination (its key)
    destination_clicked = Signal(str)
    # the "Collecting training data" chip was clicked
    training_data_clicked = Signal()
    # Feedback was clicked: open ScoreSight's Discussions
    feedback_clicked = Signal()

    def __init__(self, statusbar: QStatusBar):
        super().__init__(statusbar)
        self.statusbar = statusbar
        self.snapshot = StatusSnapshot()
        # what each output button shows: (state, colour)
        self._output_looks: dict[str, tuple] = {}

        self.label_source = QLabel(statusbar)
        self.label_source.setObjectName("label_statusSource")
        self.label_source.setTextFormat(Qt.TextFormat.RichText)
        self.label_ocr = QLabel(statusbar)
        self.label_ocr.setObjectName("label_statusOcr")
        self.label_paused = QLabel(statusbar)
        self.label_paused.setObjectName("label_statusPaused")
        self.label_paused.hide()
        # "● Collecting training data", while Tools > Collect OCR training
        # data is on; a click opens its setup
        self.button_training = QToolButton(statusbar)
        self.button_training.setObjectName("toolButton_statusTrainingData")
        self.button_training.setAutoRaise(True)
        self.button_training.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.button_training.setIconSize(QSize(9, 9))
        self.button_training.clicked.connect(self.training_data_clicked)
        self.button_training.hide()
        self._training_look = None
        self.label_http = QLabel(statusbar)
        self.label_http.setObjectName("label_statusHttp")
        self.label_http.setTextFormat(Qt.TextFormat.RichText)
        self.label_http.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.button_copy = QToolButton(statusbar)
        self.button_copy.setObjectName("toolButton_copyHttpUrl")
        self.button_copy.setAutoRaise(True)
        self.button_copy.clicked.connect(self.copyHttpUrl)
        # "Feedback", always there at the right end: a click opens ScoreSight's
        # Discussions on GitHub (questions, ideas, setups to show)
        self.button_feedback = QToolButton(statusbar)
        self.button_feedback.setObjectName("toolButton_statusFeedback")
        self.button_feedback.setAutoRaise(True)
        self.button_feedback.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.button_feedback.setIconSize(QSize(14, 14))
        self.button_feedback.clicked.connect(self.feedback_clicked)
        self._feedback_look = None
        # GuangWei: muted QQ-group help instead of GitHub Feedback
        self.label_qq_help = QLabel(statusbar)
        self.label_qq_help.setObjectName("label_statusQqHelp")
        self.label_qq_help.setTextFormat(Qt.TextFormat.RichText)
        self.label_qq_help.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.label_qq_help.linkActivated.connect(self.openQqGroup)
        self.label_qq_help.hide()

        # "Outputs: ● Files ● HTTP ▲ vMix ...": a button per output
        self.widget_outputs = QWidget(statusbar)
        self.widget_outputs.setObjectName("widget_statusOutputs")
        layout = QHBoxLayout(self.widget_outputs)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.label_outputs = QLabel(self.widget_outputs)
        self.label_outputs.setObjectName("label_statusOutputs")
        layout.addWidget(self.label_outputs)
        self.output_buttons: dict[str, QToolButton] = {}
        for key in DESTINATIONS:
            button = QToolButton(self.widget_outputs)
            button.setObjectName(f"toolButton_statusOutput_{key}")
            button.setAutoRaise(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setIconSize(QSize(9, 9))
            button.clicked.connect(
                lambda checked=False, key=key: self.destination_clicked.emit(key)
            )
            layout.addWidget(button)
            self.output_buttons[key] = button
        self.widget_outputs.hide()

        statusbar.addWidget(self.label_source)
        statusbar.addWidget(self.label_ocr)
        statusbar.addWidget(self.label_paused)
        statusbar.addWidget(self.button_training)
        statusbar.addPermanentWidget(self.widget_outputs)
        statusbar.addPermanentWidget(self.label_http)
        statusbar.addPermanentWidget(self.button_copy)
        statusbar.addPermanentWidget(self.label_qq_help)
        statusbar.addPermanentWidget(self.button_feedback)
        if SIMPLIFY_OUTPUT_UI:
            self.button_feedback.hide()
            self.label_qq_help.show()
        self.retranslate()

    def retranslate(self):
        self.label_outputs.setText(
            QCoreApplication.translate("StatusBar", "Outputs:") + " "
        )
        for key, button in self.output_buttons.items():
            button.setText(short_name(key))
        self._output_looks.clear()
        self.button_training.setText(
            QCoreApplication.translate("StatusBar", "Collecting training data")
        )
        self._training_look = None
        self.button_copy.setText(QCoreApplication.translate("StatusBar", "Copy"))
        self.button_copy.setToolTip(
            QCoreApplication.translate(
                "StatusBar", "Copy the HTTP server's address to the clipboard"
            )
        )
        self.button_copy.setAccessibleName(
            QCoreApplication.translate("StatusBar", "Copy the HTTP server's address")
        )
        self.button_feedback.setText(
            QCoreApplication.translate("StatusBar", "Feedback")
        )
        self.button_feedback.setToolTip(
            QCoreApplication.translate(
                "StatusBar",
                "Ask a question, share an idea or show your setup: opens "
                "ScoreSight's Discussions on GitHub in your web browser",
            )
        )
        if SIMPLIFY_OUTPUT_UI:
            link = '<a href="qq-group">复制群号</a>'
            self.label_qq_help.setText(f"不会用？加 QQ 群 {QQ_GROUP_NUMBER}（{link}）")
            self.label_qq_help.setToolTip(
                f"点击复制群号 {QQ_GROUP_NUMBER}，到 QQ 里搜索加群"
            )
            muted = theme.muted_color(self.statusbar.palette()).name()
            self.label_qq_help.setStyleSheet(
                f"QLabel {{ color: {muted}; font-size: 12px; }}"
                f" QLabel a {{ color: {muted}; }}"
            )
        self.update(self.snapshot)

    def update(self, snapshot: StatusSnapshot):
        self.snapshot = snapshot
        palette = self.statusbar.palette()

        glyph, color_name = _SOURCE_LOOK.get(
            snapshot.source_state, _SOURCE_LOOK[SOURCE_NONE]
        )
        color = theme.color(color_name, palette) if color_name else None
        self.label_source.setText(
            f"{_colored(glyph, color)}&nbsp;{html.escape(source_text(snapshot), quote=False)}"
        )
        tooltip = QCoreApplication.translate(
            "StatusBar",
            "The video source, its resolution and capture frame rate (how fast "
            "pictures arrive, not how often the boxes are read)",
        )
        adjustments = adjustments_text(snapshot)
        if adjustments:
            tooltip += "\n" + adjustments
        if snapshot.device_enum_headline and snapshot.source_name:
            tooltip += "\n" + snapshot.device_enum_headline
        if snapshot.device_enum_detail:
            tooltip += "\n" + snapshot.device_enum_detail
        self.label_source.setToolTip(tooltip)

        text = ocr_text(snapshot)
        self.label_ocr.setVisible(bool(text))
        self.label_ocr.setText(text)
        warn = ocr_is_slow(snapshot) or ocr_fallback(snapshot)
        _set_style_sheet(
            self.label_ocr,
            f"color: {theme.color('rejected', palette).name()};" if warn else "",
        )
        self.label_ocr.setToolTip(ocr_tooltip(snapshot))

        paused = paused_text(snapshot)
        self.label_paused.setVisible(bool(paused))
        self.label_paused.setText(paused)
        _set_style_sheet(
            self.label_paused,
            "QLabel { background-color: %s; color: %s; font-weight: bold;"
            " border-radius: 3px; padding: 1px 6px; }"
            % (
                theme.color("paused", palette).name(),
                theme.on_color(palette).name(),
            ),
        )
        self.label_paused.setToolTip(
            QCoreApplication.translate(
                "StatusBar", "The outputs are paused: they keep their last values"
            )
        )
        self.updateTrainingChip(snapshot, palette)

        if snapshot.http_url:
            self.label_http.setText(html.escape(http_text(snapshot), quote=False))
            others = "\n".join(snapshot.http_urls or (snapshot.http_url,))
            self.label_http.setToolTip(
                QCoreApplication.translate(
                    "StatusBar",
                    "The HTTP server (scoreboard page, JSON, XML, CSV). Open it in a "
                    "browser or in other software on this computer or network:\n{urls}",
                ).format(urls=others)
            )
        else:
            self.label_http.setText(
                f"{_colored('■', theme.color('empty', palette))}&nbsp;"
                f"{html.escape(http_text(snapshot), quote=False)}"
            )
            self.label_http.setToolTip(
                QCoreApplication.translate(
                    "StatusBar",
                    "Another program uses the port. Close it, or start ScoreSight "
                    "with --port to use a different port.",
                )
            )
        self.button_copy.setVisible(bool(snapshot.http_url))
        self.updateOutputs(snapshot.outputs, snapshot.now, palette)
        self.updateFeedbackButton(palette)

    def updateFeedbackButton(self, palette):
        """Feedback in the link colour, as it opens a web page (again when
        the theme changes)."""
        color = palette.color(QPalette.ColorRole.Link)
        if self._feedback_look != color.name():
            self._feedback_look = color.name()
            self.button_feedback.setIcon(theme.tool_icon("chat", color, 14))
            self.button_feedback.setStyleSheet(
                f"QToolButton {{ color: {color.name()}; }}"
            )

    def updateTrainingChip(self, snapshot: StatusSnapshot, palette):
        """ "● Collecting training data" while it is on: it fills a folder, so
        it shouldn't go unnoticed."""
        collecting = snapshot.collecting_training_data
        self.button_training.setVisible(collecting)
        if not collecting:
            return
        # a red "recording" dot
        color = theme.color("empty", palette)
        if self._training_look != color.name():
            self._training_look = color.name()
            self.button_training.setIcon(theme.state_icon("disc", color, 9))
        self.button_training.setToolTip(
            QCoreApplication.translate(
                "StatusBar",
                "The boxes' pictures and readings are saved for training an OCR "
                "model, to {folder}.\nClick to change the folder or its size limit; "
                "Tools > Collect OCR training data turns it off.",
            ).format(folder=snapshot.training_data_folder or "?")
        )

    def updateOutputs(self, outputs, now: float, palette):
        """The outputs summary: a glyph (its shape and colour tell the state)
        and the short name of every output."""
        self.widget_outputs.setVisible(bool(outputs))
        for status in outputs:
            button = self.output_buttons.get(status.key)
            if button is None:
                continue
            color_name = STATE_COLORS.get(status.state)
            color = (
                theme.color(color_name, palette)
                if color_name
                else theme.muted_color(palette)
            )
            look = (status.state, color.name())
            if self._output_looks.get(status.key) != look:
                self._output_looks[status.key] = look
                button.setIcon(theme.state_icon(STATE_SHAPES[status.state], color, 9))
                button.setAccessibleName(
                    f"{short_name(status.key)}: {state_word(status.state)}"
                )
            button.setToolTip(
                status_tooltip(status, now)
                + "\n"
                + QCoreApplication.translate("StatusBar", "Click to show its settings.")
            )

    def openQqGroup(self, _link: str = ""):
        """Copy the QQ group number. Desktop QQ often rejects mqqapi:// joins
        with an unsupported-content dialog, so we never open that protocol."""
        QGuiApplication.clipboard().setText(QQ_GROUP_NUMBER)
        QToolTip.showText(
            self.label_qq_help.mapToGlobal(QPoint(0, 0)),
            f"群号已复制：{QQ_GROUP_NUMBER}，请打开 QQ → 搜索 → 加群",
            self.label_qq_help,
            msecShowTime=5000,
        )

    def copyHttpUrl(self):
        if not self.snapshot.http_url:
            return
        QGuiApplication.clipboard().setText(self.snapshot.http_url)
        QToolTip.showText(
            self.button_copy.mapToGlobal(QPoint(0, 0)),
            QCoreApplication.translate("StatusBar", "Copied {url}").format(
                url=self.snapshot.http_url
            ),
            self.button_copy,
        )

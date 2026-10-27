"""The texts of the viewer column's source bar and of the messages around the
picture: what the source combo box shows for the open source, the source error
banner, the four-corner picking banner, the playback speeds, the zoom readout,
the first-run card's links (the Quickstart and the community), and whether
macOS lets ScoreSight record the screen.

MainWindow owns the widgets (they are in mainwindow.ui); this module words
and computes what they show, so it can be tested without a window.
CompactToolRow fits the viewer column's rows into narrow windows.
"""

import platform

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtWidgets import QWidget

from camera_info import CameraInfo, is_obs_virtual_camera
from status_bar import without_credentials

DOCS_URL = "https://royshil.github.io/scoresight/"
QUICKSTART_URL = DOCS_URL + "quickstart.html"
SOURCE_TROUBLESHOOTING_URL = DOCS_URL + "troubleshooting.html#source-problems"
# ScoreSight's Discussions on GitHub: questions, ideas, setups to show
COMMUNITY_URL = "https://github.com/royshil/scoresight/discussions"
# System Settings > Privacy & Security > Screen Recording
MACOS_SCREEN_RECORDING_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
)

# the playback speeds of a video file, in the order the speed button cycles
PLAYBACK_SPEEDS = (1, 2, 4, 8, 16, 32)

# the window id of a screen capture of the whole screen
ENTIRE_SCREEN = -1


def file_name(path) -> str:
    path = str(path)
    return path.replace("\\", "/").rsplit("/", 1)[-1] or path


def screen_title(window_id, window_title: str | None = None) -> str:
    """The name of a screen capture's window ("Entire screen" for the whole
    screen)."""
    if window_id == ENTIRE_SCREEN or window_id is None:
        return QCoreApplication.translate("SourceBar", "Entire screen")
    return window_title or QCoreApplication.translate(
        "SourceBar", "Window {id}"
    ).format(id=window_id)


def source_combo_text(
    camera_info: CameraInfo | None, window_title: str | None = None
) -> str | None:
    """What the source combo box shows while a video file, a stream or a
    screen capture is open ("Video file: game.mp4"), or None for the other
    sources (a camera's item already shows its name)."""
    if camera_info is None:
        return None
    if camera_info.type == CameraInfo.CameraType.FILE:
        return QCoreApplication.translate("SourceBar", "Video file: {name}").format(
            name=file_name(camera_info.id)
        )
    if camera_info.type == CameraInfo.CameraType.URL:
        return QCoreApplication.translate("SourceBar", "Stream: {url}").format(
            url=without_credentials(str(camera_info.id))
        )
    if camera_info.type == CameraInfo.CameraType.SCREEN_CAPTURE:
        return QCoreApplication.translate("SourceBar", "Screen: {window}").format(
            window=screen_title(camera_info.id, window_title)
        )
    return None


def source_full_name(
    camera_info: CameraInfo | None, window_title: str | None = None
) -> str:
    """The open source in full: a file's path, a stream's URL (without its
    password), a captured window's title, or a camera's name."""
    if camera_info is None:
        return ""
    if camera_info.type == CameraInfo.CameraType.FILE:
        return str(camera_info.id)
    if camera_info.type == CameraInfo.CameraType.URL:
        return without_credentials(str(camera_info.id))
    if camera_info.type == CameraInfo.CameraType.SCREEN_CAPTURE:
        return screen_title(camera_info.id, window_title)
    return str(camera_info.description)


def chooser_texts() -> tuple[str, str, str, str]:
    """The source combo box's first items: no source, and the choosers of a
    video file, a stream URL and a screen capture."""
    return (
        QCoreApplication.translate("MainWindow", "No source"),
        QCoreApplication.translate("MainWindow", "Video file…"),
        QCoreApplication.translate("MainWindow", "Stream URL (RTSP, HTTP)…"),
        QCoreApplication.translate("MainWindow", "Screen capture…"),
    )


def source_error_text(
    camera_info: CameraInfo | None, window_title: str | None = None
) -> tuple[str, str]:
    """(What failed, what to do) for a source that can't be opened or read."""
    if camera_info is None:
        return QCoreApplication.translate("SourceBar", "The source can't be read."), ""
    name = source_full_name(camera_info, window_title)
    kind = camera_info.type
    if kind == CameraInfo.CameraType.FILE:
        return (
            QCoreApplication.translate(
                "SourceBar", "Can't play the video file {name}."
            ).format(name=file_name(camera_info.id)),
            QCoreApplication.translate(
                "SourceBar",
                "It may have been moved, be damaged or use a format that can't be "
                "read (MP4 and AVI work best).",
            ),
        )
    if kind == CameraInfo.CameraType.URL:
        return (
            QCoreApplication.translate(
                "SourceBar", "Can't open the stream {url}."
            ).format(url=name),
            QCoreApplication.translate(
                "SourceBar",
                "Check the address, the user name and password, and that the camera "
                "or server is on and reachable from this computer. A stream gets "
                "5 seconds to answer.",
            ),
        )
    if kind == CameraInfo.CameraType.SCREEN_CAPTURE:
        return (
            QCoreApplication.translate("SourceBar", "Can't capture {window}.").format(
                window=name
            ),
            QCoreApplication.translate(
                "SourceBar", "The window may have been closed or minimized."
            ),
        )
    if kind == CameraInfo.CameraType.NDI:
        return (
            QCoreApplication.translate(
                "SourceBar", "Can't receive the NDI source {name}."
            ).format(name=name),
            QCoreApplication.translate(
                "SourceBar",
                "Check that it is still on the network, then Retry or reload the "
                "sources.",
            ),
        )
    if kind == CameraInfo.CameraType.OPENCV and is_obs_virtual_camera(
        getattr(camera_info, "description", None)
    ):
        return (
            QCoreApplication.translate(
                "SourceBar", "Can't show the OBS Virtual Camera picture."
            ),
            QCoreApplication.translate(
                "SourceBar",
                "In OBS, click Start Virtual Camera and check that the preview "
                "there has a picture, then press refresh here. Close any other "
                "program that is using the virtual camera, then try again.",
            ),
        )
    return (
        QCoreApplication.translate("SourceBar", "Can't open {name}.").format(name=name),
        QCoreApplication.translate(
            "SourceBar",
            "It may be unplugged or used by another program: close that program, "
            "then Retry.",
        ),
    )


def screen_recording_permission_text() -> tuple[str, str]:
    """(What's wrong, what to do) when macOS doesn't let ScoreSight record the
    screen: the capture then shows only the desktop."""
    return (
        QCoreApplication.translate(
            "SourceBar", "ScoreSight isn't allowed to record the screen."
        ),
        QCoreApplication.translate(
            "SourceBar",
            "macOS shows only the desktop to apps without the Screen Recording "
            "permission. Allow ScoreSight in System Settings, then restart it.",
        ),
    )


def screen_recording_allowed() -> bool | None:
    """Whether macOS lets this app record the screen: True or False on macOS
    10.15 and later, None where there's no such permission or it can't be
    told (CGPreflightScreenCaptureAccess doesn't ask the user)."""
    if platform.system() != "Darwin":
        return None
    try:
        import Quartz  # pyobjc, on macOS only

        preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        if preflight is None:
            return None
        return bool(preflight())
    except Exception:
        return None


def four_corner_banner_text(picked: int, has_correction: bool) -> str:
    """The four-corner picking banner: how many corners were clicked, and what
    Esc does."""
    text = QCoreApplication.translate(
        "SourceBar", "<b>Click the 4 corners of the scoreboard:</b> {picked} of 4."
    ).format(picked=picked)
    if has_correction:
        text += " " + QCoreApplication.translate(
            "SourceBar", "Esc cancels and keeps the previous correction."
        )
    else:
        text += " " + QCoreApplication.translate("SourceBar", "Esc cancels.")
    return text


def next_speed(speed) -> int:
    """The playback speed after speed (1, 2, 4 ... 32, then 1 again)."""
    try:
        index = PLAYBACK_SPEEDS.index(speed)
    except ValueError:
        return PLAYBACK_SPEEDS[0]
    return PLAYBACK_SPEEDS[(index + 1) % len(PLAYBACK_SPEEDS)]


def speed_text(speed) -> str:
    return f"×{speed}"


def zoom_text(scale: float) -> str:
    """The zoom readout: the size of an image pixel on screen, in percent."""
    return QCoreApplication.translate("MainWindow", "{zoom}%").format(
        zoom=round(scale * 100)
    )


def rotate_text(angle) -> str:
    """The Rotate button: what a click does, and the current angle."""
    return QCoreApplication.translate("MainWindow", "Rotate 90° · {angle}°").format(
        angle=int(angle or 0) % 360
    )


def quickstart_link() -> str:
    return '<a href="{url}">{text}</a> ↗'.format(
        url=QUICKSTART_URL,
        text=QCoreApplication.translate("MainWindow", "5-minute Quickstart"),
    )


def community_link() -> str:
    return '<a href="{url}">{text}</a> ↗'.format(
        url=COMMUNITY_URL,
        text=QCoreApplication.translate("MainWindow", "Questions? Ask the community"),
    )


# How the last camera / capture-card search ended. Pending: still running, or
# not started. Empty: it finished and found nothing. Failed: it raised
# (missing DirectShow enumerator, COM error, ...) and must not look like Empty.
ENUM_PENDING = "pending"
ENUM_OK = "ok"
ENUM_EMPTY = "empty"
ENUM_FAILED = "failed"

# Shown when the search itself failed. Not the same sentence as an empty list.
_ENUM_FAILED_ZH = "设备枚举失败，请检查驱动或重装便携包"
_ENUM_FAILED_EN = (
    "Couldn't list cameras. Check the driver, or reinstall the portable package."
)
_ENUM_FAILED_DETAIL_ZH = "具体原因已写在日志里（帮助 → 查看日志）。"
_ENUM_FAILED_DETAIL_EN = "The reason is in the log (Help → View log)."

# A search that finished and found nothing.
_ENUM_EMPTY_ZH = "没有找到摄像头/采集卡"
_ENUM_EMPTY_EN = "No camera found"
_ENUM_EMPTY_TIPS_ZH = (
    "① 设备管理器里能不能看到这块卡。\n"
    "② 关掉 OBS、vMix 之后按 F5 再找一次。\n"
    "③ 若 OBS「视频捕获设备」里有，而这里没有，请截图反馈。"
)
_ENUM_EMPTY_TIPS_EN = (
    "1. See whether Device Manager lists the card.\n"
    "2. Close OBS and vMix, then press F5.\n"
    "3. If OBS Video Capture Device lists it and this app does not, send a "
    "screenshot."
)

# How to pick a capture card. OCR reads one picture; a four-input switcher
# does not need four previews.
_CAPTURE_CARD_LINES_ZH = (
    "OCR 只要选一路画面，不必四路同时预览。",
    "美乐威 Magewell：源列表里选对应名字；多路看名字后面的通道号。",
    "天创恒达：USB 免驱，直接选；PCIe 要先装官网「第三方软件驱动」。",
    "Blackmagic（含雷电 UltraStudio）：先装 Desktop Video，并启用 DirectShow，"
    "源里找 Blackmagic WDM Capture。",
    "禾鸟等四路切换卡：列表里通常只有 1 个设备，用机身切到有记分牌的输入。",
    "OBS 虚拟摄像机：先在 OBS 里点「启动虚拟摄像机」，确认 OBS 预览有画面，"
    "再在这里选 OBS Virtual Camera。还是黑的就点刷新，并关掉别的正在占用它的软件。"
    "不要为了虚拟摄像机去关 OBS。vMix 请选 vMix Video。",
)


def device_enum_messages(state: str, *, chinese: bool) -> tuple[str, str]:
    """(headline, detail) for a search that failed or found nothing.

    ("", "") when the search found a device or has not finished. The failed
    headline is not the empty-list headline: a COM error must not look like
    “no camera”.
    """
    if state == ENUM_FAILED:
        if chinese:
            return _ENUM_FAILED_ZH, _ENUM_FAILED_DETAIL_ZH
        return _ENUM_FAILED_EN, _ENUM_FAILED_DETAIL_EN
    if state == ENUM_EMPTY:
        if chinese:
            return _ENUM_EMPTY_ZH, _ENUM_EMPTY_TIPS_ZH
        return _ENUM_EMPTY_EN, _ENUM_EMPTY_TIPS_EN
    return "", ""


def device_enum_notice_text(state: str, *, chinese: bool) -> str:
    """The headline and the tips, or "" when there is nothing to say."""
    headline, detail = device_enum_messages(state, chinese=chinese)
    if not headline:
        return ""
    if not detail:
        return headline
    return f"{headline}\n{detail}"


def capture_card_guide(*, chinese: bool) -> str:
    """Plain lines for the source step. Empty unless the UI is Chinese."""
    if not chinese:
        return ""
    return "\n".join(_CAPTURE_CARD_LINES_ZH)


def capture_card_guide_html() -> str:
    """The same lines for the first-run wizard."""
    intro, *items = _CAPTURE_CARD_LINES_ZH
    lis = "".join(f"<li>{item}</li>" for item in items)
    return f"<p>{intro}</p><ul>{lis}</ul>"


def classify_discovery(payload) -> tuple[str, list]:
    """(state, cameras) for what source discovery handed back.

    ("ok", list) finished. ("failed", []) raised. None is what
    run_in_background passes when the worker raises, and that is a failure,
    not an empty camera list. A bare list is a finished search.
    """
    if (
        isinstance(payload, tuple)
        and len(payload) == 2
        and payload[0] in (ENUM_OK, ENUM_FAILED)
    ):
        if payload[0] == ENUM_FAILED:
            return ENUM_FAILED, []
        cameras = list(payload[1] or [])
        return (ENUM_EMPTY if not cameras else ENUM_OK), cameras
    if payload is None or isinstance(payload, BaseException):
        return ENUM_FAILED, []
    cameras = list(payload or [])
    return (ENUM_EMPTY if not cameras else ENUM_OK), cameras


def directshow_names_text(names: list[str]) -> str:
    """The FriendlyNames from a search that finished, for a support paste."""
    if not names:
        return "没有找到摄像头/采集卡\n（刷新成功，DirectShow 列表是空的）"
    return "DirectShow 设备：\n" + "\n".join(names)


class CompactToolRow(QObject):
    """Fits a row of controls into less room, in up to three steps:

    0. as designed;
    1. compact: its tool buttons show their icons only (their texts stay
       their tooltips and accessible names) and its labels hide;
    2. wrapped: also compact, and the widgets in wraps move to other cells of
       the row's grid layout, e.g. to a second line.

    The step is chosen from the room the row has (the width of area: the row
    itself, or e.g. the space a centred card can use) against the width each
    step needs. The row's minimum width is the last step's, so the window can
    always be as narrow as that.
    """

    WRAPPED = 2

    def __init__(
        self,
        row: QWidget,
        buttons=(),
        labels=(),
        wraps=(),
        area: QWidget | None = None,
        ignore=(),
        icon_buttons=(),
    ):
        """wraps: (widget, (row, column[, rowspan, colspan[, alignment]]) as
        designed, (...) wrapped) of the row's grid layout; ignore: widgets
        whose width isn't needed (they hide by themselves when there's no
        room); icon_buttons: buttons that also show their icon as designed
        (beside their text)."""
        super().__init__(row)
        self.row = row
        self.buttons = list(buttons) + list(icon_buttons)
        self.icon_buttons = list(icon_buttons)
        self.labels = list(labels)
        self.wraps = list(wraps)
        self.area = area if area is not None else row
        self.ignore = list(ignore)
        self.step = 0
        # the width that steps 0 and 1 need
        self.needs = [0, 0]
        self.area.installEventFilter(self)
        if self.area is not self.row:
            self.row.installEventFilter(self)
        self.refresh()

    @property
    def compact(self) -> bool:
        return self.step > 0

    @property
    def wrapped(self) -> bool:
        return self.step == self.WRAPPED

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self.area and kind == QEvent.Type.Resize:
            self.update()
        elif kind in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            # the texts' widths changed
            self.refresh()
        return False

    def refresh(self):
        """Measure the steps again (after the texts changed, e.g. with the
        language, or the style)."""
        for step in (0, 1):
            self.apply(step)
            self.needs[step] = self.neededWidth()
        last = self.WRAPPED if self.wraps else 1
        self.apply(last)
        self.row.setMinimumWidth(0)
        self.activate()
        minimum = self.row.minimumSizeHint().width()
        # narrower than the earlier steps need: they squeeze until the next
        # resize picks the step that fits
        self.row.setMinimumWidth(minimum)
        self.update()

    def update(self):
        width = self.area.width()
        if width >= self.needs[0]:
            step = 0
        elif width >= self.needs[1] or not self.wraps:
            step = 1
        else:
            step = self.WRAPPED
        if step != self.step:
            self.apply(step)

    def activate(self):
        for widget in [self.row] + self.row.findChildren(QWidget):
            layout = widget.layout()
            if layout is not None:
                layout.invalidate()
                layout.activate()

    def neededWidth(self) -> int:
        self.activate()
        width = self.row.sizeHint().width()
        layout = self.row.layout()
        spacing = max(0, layout.spacing()) if layout is not None else 0
        for widget in self.ignore:
            if not widget.isHidden():
                width -= widget.sizeHint().width() + spacing
        return width

    def apply(self, step: int):
        self.step = step
        for button in self.buttons:
            if step > 0:
                style = Qt.ToolButtonStyle.ToolButtonIconOnly
            elif button in self.icon_buttons:
                style = Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            else:
                style = Qt.ToolButtonStyle.ToolButtonTextOnly
            button.setToolButtonStyle(style)
        for label in self.labels:
            label.setVisible(step == 0)
        for widget, designed, wrapped in self.wraps:
            cell = wrapped if step == self.WRAPPED else designed
            layout = widget.parentWidget().layout()
            layout.removeWidget(widget)
            layout.addWidget(widget, *cell)

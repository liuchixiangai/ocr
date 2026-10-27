from PySide6.QtCore import QThread, Signal, Slot

import atexit
import ctypes
import math
import os
import re
import platform
import sys
import time
import cv2
import numpy as np
import threading

from base_video_capture import BaseVideoCapture
from camera_info import (
    CameraInfo,
    is_obs_virtual_camera,
    msmf_index_for_name,
    opencv_forces_obs_nv12,
)
from defaults import CLEAR_AFTER_SECONDS, CLEAR_BLANK_OR_ZERO, CLEAR_NEVER
from storage import (
    SETTINGS_FILE,
    TextDetectionTargetMemoryStorage,
    fetch_data,
    remove_data,
    store_data,
    subscribe_to_data,
    unsubscribe_from_data,
)
from tesseract import TextDetector, global_binarization, render_binary_view
from text_detection_target import TextDetectionTargetWithResult
from sc_logging import logger
from frame_stabilizer import FrameStabilizer
from ocr_training_data import ocr_training_data_options, snapshot_training_samples

ResultState = TextDetectionTargetWithResult.ResultState

# "Detections / s" (detection_cadence): the default, and the lowest rate the
# capture thread accepts (one detection every 10 s); 0 or less is clamped to it.
DEFAULT_DETECTION_CADENCE = 5
MIN_DETECTION_CADENCE = 0.1
MAX_UPDATE_FRAME_INTERVAL_MS = 1000.0 / MIN_DETECTION_CADENCE

# Open and read timeouts for URL (network stream) sources, so that a stalled
# stream can't block the capture thread (and closing it) for FFmpeg's default
# 30 s per call.
URL_CAPTURE_TIMEOUT_MS = 5000
URL_CAPTURE_PARAMS = [
    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
    URL_CAPTURE_TIMEOUT_MS,
    cv2.CAP_PROP_READ_TIMEOUT_MSEC,
    URL_CAPTURE_TIMEOUT_MS,
]

FFMPEG_CAPTURE_OPTIONS_ENV = "OPENCV_FFMPEG_CAPTURE_OPTIONS"
# RTSP over TCP first, falling back to UDP for a server that refuses TCP (461
# Unsupported Transport). "rtsp_transport;tcp" would be TCP only.
DEFAULT_FFMPEG_CAPTURE_OPTIONS = "rtsp_flags;prefer_tcp"

# How long closing a source waits for its capture thread to stop before letting
# it finish in the background.
STOP_TIMEOUT_MS = 3000

# A frame processing error that repeats on every frame is reported at most this often.
ERROR_REPORT_INTERVAL_S = 5.0

# How long stopping the capture waits for the OCR cycle in progress.
OCR_STOP_TIMEOUT_S = 2.0

# The capture loop sleeps toward this (~33 fps). It is the rate frames are
# read, including a video file's playback, and it is not the preview rate.
CAPTURE_FRAME_INTERVAL_MS = 30.0
# Pictures shown per second when the computer keeps up. 1000 ms made a live
# camera, a vMix virtual camera and a video file all step once a second even
# while frames were being read. OCR stays on the detection cadence.
PREVIEW_TARGET_FPS = 15
PREVIEW_INTERVAL_MS = 1000.0 / PREVIEW_TARGET_FPS

# No saved camera size used to climb toward 1920×1080. A driver that opens at
# 4K (a full court) is asked down to this. Names with vMix / Virtual, whose
# picture is often the whole program, stop at 720p. A size the user saved is
# applied as stored, including 1280×720 chosen in Video Settings.
PHYSICAL_CAMERA_CEILING = (1920, 1080)
MODEST_CAMERA_SIZE = (1280, 720)
# OBS Virtual Camera's SetFormat returns success for a size the canvas is not
# outputting. OpenCV's DirectShow default (640×480, forced to NV12 for the
# exact device name) then copies no samples: read() is a black frame and the
# preview stays black. vMix Video rejects a size it cannot do, so the same
# open falls back to a real picture. Ask OBS for these canvases, in order,
# until a frame has pixels. A stored Video Settings size is tried first.
OBS_CANVAS_SIZES = (
    (1920, 1080),
    (1280, 720),
    (2560, 1440),
    (3840, 2160),
    (1600, 900),
    (1366, 768),
    (1080, 1920),
    (960, 540),
    # 4:3 boards. After the 16:9 canvases, so a normal OBS canvas does not
    # wait on these.
    (1024, 768),
    (1280, 960),
    # OpenCV's own default request. A canvas that really is 640×480 is kept;
    # it is last because asking for it first is what comes back black.
    (640, 480),
)
# Pixel formats tried, after every canvas was black, for names OpenCV does
# not force to NV12 (legacy OBS-Camera). YUY2 is what older virtual cams
# output; NV12 is what current OBS outputs; MJPG is what many capture cards
# use and what a mis-detected name must still be able to ask for.
OBS_FORMATS = ("YUY2", "NV12", "MJPG")
# How many canvases those format retries cover. The full list is already
# probed once without a FOURCC.
OBS_FORMAT_SIZE_LIMIT = 3
# Extra Media Foundation canvases after the device's own media type.
# A failed MSMF read blocks for about 10 s inside OpenCV, so a second size
# is not tried: when DirectShow never delivered a frame, one native open is
# the fallback, not another ten seconds.
OBS_MSMF_SIZE_LIMIT = 0
# Limited-range video black is 16. The empty OBS buffer is a flat field at or
# under that (every sample 0, or a flat Y=16). A dark LED board can keep its
# brightest digit at 8 or below and still be readable: those digits spread the
# samples, so a low peak on its own is not a blank frame.
BLANK_FRAME_LEVEL = 16
# Population variance of the strided view, on the samples at or under
# BLANK_FRAME_LEVEL. Flat 0 and flat 16 are 0. Thin digits at value 6 on a
# 1080p canvas measure about 0.4; a board measures about 1. A flat field with
# a little noise stays under this. Above it, the frame has a picture.
BLANK_FRAME_VARIANCE = 0.15
# Variance and the bright-pixel count use every Nth pixel (all of its
# channels). A flattened stride would skip the green channel and could miss
# a digit. Large frames only: a 640×480 probe is already small, and a tighter
# stride would drop a thin stroke. One pass here is the dark-frame path;
# a normal picture returns on the full-frame peak plus a cheap count.
_BLANK_STRIDE_MIN_PIXELS = 640 * 480
_BLANK_STRIDE = 4
# Blank frames to look at before giving up on a size. A failed read stops at
# once: that is the grabber timing out, not a warmup frame.
OBS_PROBE_READS = 8
# Consecutive blank frames while a picture was already open before the banner
# says the virtual camera has no picture. At the capture loop's 30 ms pace
# this is about a second, so a short fade to black does not flash the tip.
OBS_BLANK_STREAK = 30
# How long a new open waits for the previous OBS graph to release. Refresh
# and a source change close the viewer first; this is the backstop when that
# read is still inside OpenCV. A Media Foundation read can block for about
# 10 s, so this outlasts one of those.
OBS_REOPEN_WAIT_S = 12.0
# Stop opening further canvases this long after the search started. The first
# size always runs: a canvas that already has pixels returns immediately.
# The cap is only the failure path (every size black, or each read timing out
# for about a second) so the Chinese hint is not behind tens of seconds.
OBS_OPEN_BUDGET_S = 8.0
# Ask DirectShow / MSMF for a one-frame queue. If the device still reports a
# deeper queue, drop at most this many already-buffered frames per read.
# Extra grab() calls wait when the queue is empty, so this stays small.
MAX_STALE_GRABS = 2


def set_process_env(name: str, value: str | None):
    """Set (or with None, remove) an environment variable for this process,
    including for OpenCV's FFmpeg backend."""
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    if sys.platform == "win32":
        # OpenCV's FFmpeg plugin (opencv_videoio_ffmpeg*.dll) reads its options
        # with getenv() from msvcrt.dll, which keeps its own copy of the
        # environment that os.environ (the UCRT's) doesn't update.
        try:
            ctypes.cdll.msvcrt._putenv(f"{name}={value or ''}".encode())
        except (OSError, AttributeError):
            logger.debug("Unable to set %s for msvcrt", name, exc_info=True)


def ensure_ffmpeg_capture_options():
    """Have URL sources try RTSP over TCP first (UDP fails behind NAT,
    firewalls and VPNs) and fall back to UDP for cameras that only offer it.
    This is what OpenCV does when the variable is unset; setting it makes the
    default explicit. A user-provided OPENCV_FFMPEG_CAPTURE_OPTIONS (e.g.
    rtsp_transport;udp to force UDP) is left alone."""
    if os.environ.get(FFMPEG_CAPTURE_OPTIONS_ENV) is not None:
        return
    set_process_env(FFMPEG_CAPTURE_OPTIONS_ENV, DEFAULT_FFMPEG_CAPTURE_OPTIONS)


def cadence_to_interval_ms(cadence) -> float:
    """The time between detections, in ms, for a detection_cadence in detections
    per second (clamped to at least MIN_DETECTION_CADENCE)."""
    try:
        cadence = float(cadence)
    except (TypeError, ValueError):
        cadence = DEFAULT_DETECTION_CADENCE
    if math.isnan(cadence):
        cadence = DEFAULT_DETECTION_CADENCE
    return 1000.0 / max(cadence, MIN_DETECTION_CADENCE)


def _clamp_interval_ms(interval_ms) -> float:
    try:
        interval_ms = float(interval_ms)
    except (TypeError, ValueError):
        return cadence_to_interval_ms(DEFAULT_DETECTION_CADENCE)
    if math.isnan(interval_ms):
        return cadence_to_interval_ms(DEFAULT_DETECTION_CADENCE)
    return min(max(interval_ms, 0.0), MAX_UPDATE_FRAME_INTERVAL_MS)


def _clamp_margin(value, limit: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(value, limit))


def crop_margins(width, height, top, bottom, left, right):
    """The crop margins (top, bottom, left, right) that crop_frame cuts off a
    frame of this size: clamped so at least one row and column remain."""
    top = _clamp_margin(top, height - 1)
    bottom = _clamp_margin(bottom, height - 1 - top)
    left = _clamp_margin(left, width - 1)
    right = _clamp_margin(right, width - 1 - left)
    return top, bottom, left, right


def crop_frame(frame, top, bottom, left, right):
    """Crop the given margins off the frame. The margins are clamped to the
    frame so at least one row and column remain, e.g. when a crop set for a
    larger source is applied to a smaller (or rotated) one."""
    height, width = frame.shape[:2]
    top, bottom, left, right = crop_margins(width, height, top, bottom, left, right)
    return frame[top : height - bottom, left : width - right]


def to_gray(frame):
    if frame.ndim == 2:
        return frame
    if frame.shape[2] == 4:
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def fourcc_code(fourcc) -> int | None:
    """The FOURCC int for a stored 4-character code, or None if it isn't one.

    DirectShow sometimes reports a media-subtype GUID (RGB24's is 0xE436EB7D)
    which, read as Latin-1, looks like "}ë6ä". That string must not be sent
    back to the device.
    """
    if not isinstance(fourcc, str) or len(fourcc) != 4 or fourcc == "NULL":
        return None
    if not fourcc.isascii() or not fourcc.isalnum():
        return None
    return cv2.VideoWriter_fourcc(*fourcc)


# Frame rate asked of a camera. 25 is the default when a device doesn't report
# one. Anything below MIN (including 1, the old spin box's floor, which is what
# a failed read of 0 or -1 used to be saved as) is not a real mode.
DEFAULT_CAPTURE_FPS = 25
MIN_CAPTURE_FPS = 5
MAX_CAPTURE_FPS = 240


def usable_capture_fps(value) -> int | None:
    """A frame rate worth showing or applying, or None when the device (or the
    saved value) didn't provide one. 1 and below is never usable."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 1 or number > MAX_CAPTURE_FPS:
        return None
    fps = int(round(number))
    if fps < MIN_CAPTURE_FPS or fps > MAX_CAPTURE_FPS:
        return None
    return fps


def is_bad_low_fps(value) -> bool:
    """True for a stored or reported rate that would stick the camera at a
    crawl: 0, 1, or anything else below the lowest rate the dialog allows."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and 0 <= number < MIN_CAPTURE_FPS


def fps_for_capture(value) -> float | None:
    """The frame rate to pass to CAP_PROP_FPS, or None to leave the driver's.

    A saved 1 is applied as 25. Nothing saved (None) stays the driver's rate.
    """
    if is_bad_low_fps(value):
        return float(DEFAULT_CAPTURE_FPS)
    fps = usable_capture_fps(value)
    if fps is None:
        return None
    return float(fps)


def _positive(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or value <= 0:
        return None
    return value


# Function to set the resolution
def set_resolution(cap, width, height):
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)


# Function to get the resolution
def get_resolution(cap):
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    return width, height


def prefers_modest_camera_size(source_name: str | None) -> bool:
    """Virtual program outputs such as vMix Video.

    OBS Virtual Camera is not included. Asking it for 1280×720 is one of the
    sizes that come back black; it has its own open path.
    """
    if is_obs_virtual_camera(source_name):
        return False
    text = (source_name or "").casefold()
    return "vmix" in text or "virtual" in text


def obs_canvas_attempts(width, height) -> list[tuple[int, int]]:
    """Sizes to request from OBS Virtual Camera, stored size first.

    A stored Video Settings size is tried first because it may be this canvas.
    It is not trusted: a black result moves on to the common canvases.
    """
    sizes: list[tuple[int, int]] = []
    width_n = _positive(width)
    height_n = _positive(height)
    if width_n is not None and height_n is not None:
        sizes.append((int(width_n), int(height_n)))
    for size in OBS_CANVAS_SIZES:
        if size not in sizes:
            sizes.append(size)
    return sizes


def obs_size_attempts(width, height) -> list[tuple[int, int, str | None]]:
    """DirectShow opens: each canvas, no FOURCC.

    For the exact name "OBS Virtual Camera", OpenCV then forces NV12 itself.
    """
    return [(w, h, None) for w, h in obs_canvas_attempts(width, height)]


def obs_format_attempts(
    source_name: str | None, width, height
) -> list[tuple[int, int, str | None]]:
    """DirectShow opens that name a pixel format, after every size was black.

    Empty for the two names OpenCV 4.14 forces to NV12: a FOURCC in the
    VideoCapture constructor is overwritten, and OBS's SetFormat still returns
    success, so repeating those opens only waits on the same black graph.
    Legacy "OBS-Camera" is not in that list, so YUY2 / NV12 / MJPG are real
    requests there.
    """
    if opencv_forces_obs_nv12(source_name):
        return []
    sizes = obs_canvas_attempts(width, height)[:OBS_FORMAT_SIZE_LIMIT]
    return [(w, h, fourcc) for w, h in sizes for fourcc in OBS_FORMATS]


def obs_msmf_attempts(width, height) -> list[tuple[int | None, int | None, str | None]]:
    """Media Foundation opens. (None, None) is the device's own media type.

    Width and height would be applied with set() after open: MSMF ignores them
    in the constructor. Only the native type is opened. A failed read blocks
    for about 10 s, and a size DirectShow already failed does not justify a
    second wait. This runs only when DirectShow never delivered a frame.
    """
    native: list[tuple[int | None, int | None, str | None]] = [(None, None, None)]
    sized = [
        (w, h, None)
        for w, h in obs_canvas_attempts(width, height)[:OBS_MSMF_SIZE_LIMIT]
    ]
    return native + sized


def note_obs_blank(
    streak: int, blank: bool, limit: int = OBS_BLANK_STREAK
) -> tuple[int, str | None]:
    """(new streak, event) for one OBS frame.

    event is "blank" on the read that first reaches `limit` consecutive blank
    frames, "clear" on the first non-blank frame after that warning, else None.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = OBS_BLANK_STREAK
    if limit < 1:
        limit = 1
    try:
        streak = int(streak)
    except (TypeError, ValueError):
        streak = 0
    if streak < 0:
        streak = 0
    if blank:
        streak += 1
        if streak == limit:
            return streak, "blank"
        return streak, None
    if streak >= limit:
        return 0, "clear"
    return 0, None


def msmf_friendly_names() -> list[str]:
    """Friendly names in Media Foundation order, or [] when that list can't be read.

    The order is the CAP_MSMF device index (OpenCV enumerates with
    MFEnumDeviceSources). Off Windows this is always empty: there is no MSMF
    index to guess from the DirectShow index.
    """
    if platform.system() != "Windows":
        return []
    try:
        return _enumerate_msmf_friendly_names_win()
    except Exception:
        logger.warning("Media Foundation camera list is not available", exc_info=True)
        return []


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(data1: int, data2: int, data3: int, data4: tuple[int, ...]) -> _GUID:
    return _GUID(data1, data2, data3, (ctypes.c_ubyte * 8)(*data4))


# mfobjects.h: IMFAttributes vtable. Slot 13 is GetAllocatedString, slot 24 is
# SetGUID (IUnknown's three methods, then the attribute methods in order).
_MF_GET_ALLOCATED_STRING = 13
_MF_SET_GUID = 24
# Win8+ Media Foundation version (MF_SDK_VERSION << 16 | MF_API_VERSION).
_MF_VERSION = 0x00020070
_COINIT_APARTMENTTHREADED = 2
_RPC_E_CHANGED_MODE = 0x80010106


def _hr_failed(hr: int) -> bool:
    return ctypes.c_int32(hr).value < 0


def _com_release(unknown) -> None:
    if not unknown:
        return
    vtable = ctypes.cast(
        ctypes.cast(unknown, ctypes.POINTER(ctypes.c_void_p))[0],
        ctypes.POINTER(ctypes.c_void_p),
    )
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
    release(unknown)


def _enumerate_msmf_friendly_names_win() -> list[str]:
    """Friendly names from MFEnumDeviceSources, in that order.

    A failure returns [] so the caller stays on DirectShow instead of opening
    a capture card under a guessed index. The vtable slots are the Windows
    SDK's IMFAttributes layout; if a future SDK inserts a method, the call
    fails and this returns [].
    """
    ole32 = ctypes.windll.ole32
    mfplat = ctypes.windll.mfplat
    hr = int(ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED))
    # S_OK means this call initialized COM and must uninitialize it. S_FALSE
    # means the thread was already initialized.
    co_owned = ctypes.c_uint32(hr).value == 0
    started = False
    attributes = ctypes.c_void_p()
    devices = ctypes.c_void_p()
    count = ctypes.c_uint32(0)
    names: list[str] = []
    try:
        if co_owned is False and ctypes.c_uint32(hr).value not in (
            1,
            _RPC_E_CHANGED_MODE,
        ):
            if _hr_failed(hr):
                return []
        if _hr_failed(mfplat.MFStartup(_MF_VERSION, 1)):
            return []
        started = True
        if _hr_failed(mfplat.MFCreateAttributes(ctypes.byref(attributes), 1)):
            return []
        source_type = _guid(
            0xC60AC5FE, 0x252A, 0x478F, (0xA0, 0xEF, 0xBC, 0x8F, 0xA5, 0xF7, 0xCA, 0xD3)
        )
        vidcap = _guid(
            0x8AC3587A, 0x4AE7, 0x42D8, (0x99, 0xE0, 0x0A, 0x60, 0x13, 0xEE, 0xF9, 0x0F)
        )
        friendly = _guid(
            0x60D0E559, 0x52F8, 0x4FA2, (0xBB, 0xCE, 0xAC, 0xDB, 0x34, 0xA8, 0xEC, 0x01)
        )
        vtable = ctypes.cast(
            ctypes.cast(attributes, ctypes.POINTER(ctypes.c_void_p))[0],
            ctypes.POINTER(ctypes.c_void_p),
        )
        set_guid = ctypes.WINFUNCTYPE(
            ctypes.HRESULT,
            ctypes.c_void_p,
            ctypes.POINTER(_GUID),
            ctypes.POINTER(_GUID),
        )(vtable[_MF_SET_GUID])
        if _hr_failed(
            set_guid(attributes, ctypes.byref(source_type), ctypes.byref(vidcap))
        ):
            return []
        if _hr_failed(
            mfplat.MFEnumDeviceSources(
                attributes, ctypes.byref(devices), ctypes.byref(count)
            )
        ):
            return []
        array = ctypes.cast(devices, ctypes.POINTER(ctypes.c_void_p))
        get_string = None
        for index in range(int(count.value)):
            activate = array[index]
            if not activate:
                return []
            act_vtable = ctypes.cast(
                ctypes.cast(activate, ctypes.POINTER(ctypes.c_void_p))[0],
                ctypes.POINTER(ctypes.c_void_p),
            )
            if get_string is None:
                get_string = ctypes.WINFUNCTYPE(
                    ctypes.HRESULT,
                    ctypes.c_void_p,
                    ctypes.POINTER(_GUID),
                    ctypes.POINTER(ctypes.c_void_p),
                    ctypes.POINTER(ctypes.c_uint32),
                )(act_vtable[_MF_GET_ALLOCATED_STRING])
            raw = ctypes.c_void_p()
            length = ctypes.c_uint32()
            if _hr_failed(
                get_string(
                    activate,
                    ctypes.byref(friendly),
                    ctypes.byref(raw),
                    ctypes.byref(length),
                )
            ):
                return []
            if not raw:
                return []
            names.append(ctypes.wstring_at(raw))
            ole32.CoTaskMemFree(raw)
        return names
    finally:
        if devices:
            array = ctypes.cast(devices, ctypes.POINTER(ctypes.c_void_p))
            for index in range(int(count.value)):
                _com_release(array[index])
            ole32.CoTaskMemFree(devices)
        _com_release(attributes)
        if started:
            mfplat.MFShutdown()
        if co_owned:
            ole32.CoUninitialize()


# One OBS virtual-camera graph at a time. The next open waits until release()
# so a refresh does not build a second graph while the first read is still
# inside the grabber.
_OBS_GRAPH_LOCK = threading.Lock()


def _blank_view(frame):
    """A spatial subsample of a large frame. Small probes are used whole."""
    if getattr(frame, "ndim", 0) < 2:
        return frame
    height = int(frame.shape[0])
    width = int(frame.shape[1])
    if height * width < _BLANK_STRIDE_MIN_PIXELS:
        return frame
    return frame[::_BLANK_STRIDE, ::_BLANK_STRIDE]


def _speck_limit(sample_count: int) -> int:
    """Bright samples that are still just a speck, not a digit.

    One BGR pixel is 3 samples. A readable digit on a strided 1080p view is
    hundreds of samples, so it clears this. The floor keeps a single hot
    pixel blank on a tiny probe as well.
    """
    return max(12, int(sample_count) // 5000)


def _population_variance(values) -> float:
    if getattr(values, "size", 0) == 0:
        return 0.0
    # float32: uint8.var() is a much slower path, and this only runs when the
    # frame is already known to be near black.
    return float(np.var(np.asarray(values, dtype=np.float32)))


def frame_is_blank(frame) -> bool:
    """True when the frame is the empty OBS buffer, not a dark scoreboard.

    Empty is a flat field at or under limited-range black (all zeros, or a
    flat 16), including a black field with a few hot pixels. A low peak is
    not enough: digits at value 6–8 still spread the samples, and that frame
    is a picture. A flat white or grey program is a picture too.
    """
    if frame is None:
        return True
    try:
        values = np.asarray(frame)
    except (TypeError, ValueError):
        return True
    if values.size == 0 or values.dtype == object:
        return True
    try:
        peak = float(np.max(values))
    except (TypeError, ValueError):
        return True
    if not math.isfinite(peak):
        return True
    # The failed negotiation buffer is exact zeros. This is the common case
    # and does not need a variance pass.
    if peak <= 0:
        return True
    view = _blank_view(values)
    try:
        if peak > BLANK_FRAME_LEVEL:
            bright = int(np.count_nonzero(view > BLANK_FRAME_LEVEL))
            if bright > _speck_limit(view.size):
                return False
            dark = view[view <= BLANK_FRAME_LEVEL]
            variance = _population_variance(dark)
        else:
            variance = _population_variance(view)
    except (TypeError, ValueError):
        return True
    if not math.isfinite(variance):
        return True
    return variance <= BLANK_FRAME_VARIANCE


def obs_probe_result(capture, reads: int = OBS_PROBE_READS, stop_check=None) -> str:
    """What the next reads of an OBS graph did.

    "picture": a frame that is not a flat near-black field.
    "blank": the device delivered frames, and every one was that field.
    "failed": a read returned no frame (the grabber timed out). The device
    did not prove it can deliver a buffer.
    "stopped": refresh or a source change asked the capture thread to stop.
    Checked between reads. A read already inside OpenCV cannot be interrupted.

    A delivered black frame and a timed-out read are not the same. The first
    means the virtual camera is open and the picture is empty, so another
    backend would only add its own timeout.
    """
    try:
        reads = int(reads)
    except (TypeError, ValueError):
        reads = OBS_PROBE_READS
    if reads < 1:
        reads = 1
    delivered = False
    for _ in range(reads):
        if stop_check is not None:
            try:
                if stop_check():
                    return "stopped"
            except Exception:
                logger.exception("OBS open stop check failed")
                return "stopped"
        try:
            ok, frame = capture.read()
        except cv2.error:
            return "blank" if delivered else "failed"
        if not ok or frame is None:
            return "blank" if delivered else "failed"
        delivered = True
        if not frame_is_blank(frame):
            return "picture"
    return "blank"


def capture_delivers_picture(
    capture, reads: int = OBS_PROBE_READS, stop_check=None
) -> bool:
    """True when one of the next reads is a non-blank frame.

    A read that fails ends the probe: the grabber timed out because the buffer
    size does not match, and the next read will not start a picture. Blank
    frames are looked at up to `reads` times, so a few empty frames at the
    start of a good graph are skipped. `stop_check` is polled between reads
    (a read already inside OpenCV's grabber wait cannot be interrupted).
    """
    return obs_probe_result(capture, reads=reads, stop_check=stop_check) == "picture"


def _release_capture(capture) -> None:
    try:
        capture.release()
    except Exception:
        logger.debug("Releasing a capture failed", exc_info=True)


def camera_open_ceiling(source_name: str | None) -> tuple[int, int]:
    """The largest size to request when the user has not saved one."""
    if prefers_modest_camera_size(source_name):
        return MODEST_CAMERA_SIZE
    return PHYSICAL_CAMERA_CEILING


def cap_unbounded_resolution(cap, source_name: str | None) -> None:
    """Leave the driver's size unless it is above the ceiling.

    Nothing saved used to climb toward 1920×1080, and a driver already at 4K
    was left there. Scoreboard OCR does not need a 4K picture of the whole
    court. A size the user saved is applied by the caller instead.
    """
    try:
        ok, _frame = cap.read()
    except cv2.error:
        logger.debug("Camera resolution probe failed", exc_info=True)
        return
    if not ok:
        logger.warning("Error: camera not initialized")
        return
    width, height = get_resolution(cap)
    width_n = _positive(width)
    height_n = _positive(height)
    if width_n is None or height_n is None:
        return
    ceiling = camera_open_ceiling(source_name)
    if width_n <= ceiling[0] and height_n <= ceiling[1]:
        logger.info(
            "Camera opened at the driver's resolution: %d x %d",
            int(width_n),
            int(height_n),
        )
        return
    logger.info(
        "Camera opened at %d x %d; asking for %d x %d",
        int(width_n),
        int(height_n),
        ceiling[0],
        ceiling[1],
    )
    set_resolution(cap, ceiling[0], ceiling[1])


def request_latest_frame_buffer(capture) -> int:
    """Ask for a one-frame queue. Returns how many extra frames to drop per
    read when the device still reports a deeper queue, else 0.

    A report of 1, 0 or an unsupported property means one read() is enough:
    further grab() calls would wait for frames that are not queued yet.
    """
    try:
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except cv2.error:
        logger.debug("CAP_PROP_BUFFERSIZE is not accepted", exc_info=True)
        return 0
    try:
        reported = float(capture.get(cv2.CAP_PROP_BUFFERSIZE))
    except (cv2.error, TypeError, ValueError):
        return 0
    if not math.isfinite(reported) or reported <= 1:
        return 0
    return min(int(reported) - 1, MAX_STALE_GRABS)


def read_newest_frame(capture, extra: int):
    """read(), then drop up to `extra` frames already queued behind it.

    Used only when the device reported a queue deeper than one frame, and
    only for camera devices. Files, URLs and NDI use a plain read().
    """
    ok, frame = capture.read()
    if not ok or frame is None or extra <= 0:
        return ok, frame
    for _ in range(extra):
        try:
            grabbed = capture.grab()
        except cv2.error:
            break
        if not grabbed:
            break
        try:
            newer_ok, newer = capture.retrieve()
        except cv2.error:
            break
        if newer_ok and newer is not None and getattr(newer, "size", 0):
            ok, frame = True, newer
    return ok, frame


# a reading of zero: 0, 00, 0:00, 0.0 (with "When blank": blank or zero)
_ZERO_TEXT = re.compile(r"[0:.,'\s]*0[0:.,'\s]*")


def is_zero_text(text: str) -> bool:
    return _ZERO_TEXT.fullmatch(text) is not None


def apply_flip(frame, horizontal: bool, vertical: bool):
    """Mirror a frame. horizontal is left-right of the picture as shown
    (after rotation); both together is a half turn."""
    if horizontal and vertical:
        return cv2.flip(frame, -1)
    if horizontal:
        return cv2.flip(frame, 1)
    if vertical:
        return cv2.flip(frame, 0)
    return frame


class FrameCropAndRotation:
    def __init__(self):
        self.isCropSet = fetch_data(SETTINGS_FILE, "crop_mode", False)
        self.cropTop = fetch_data(SETTINGS_FILE, "top_crop", 0)
        self.cropBottom = fetch_data(SETTINGS_FILE, "bottom_crop", 0)
        self.cropLeft = fetch_data(SETTINGS_FILE, "left_crop", 0)
        self.cropRight = fetch_data(SETTINGS_FILE, "right_crop", 0)
        self.rotation = fetch_data(SETTINGS_FILE, "rotation", 0)
        self.flipHorizontal = bool(fetch_data(SETTINGS_FILE, "flip_horizontal", False))
        self.flipVertical = bool(fetch_data(SETTINGS_FILE, "flip_vertical", False))
        self._subscriptions = [
            ("crop_mode", self.setCropMode),
            ("top_crop", self.setCropTop),
            ("bottom_crop", self.setCropBottom),
            ("left_crop", self.setCropLeft),
            ("right_crop", self.setCropRight),
            ("rotation", self.setRotation),
            ("flip_horizontal", self.setFlipHorizontal),
            ("flip_vertical", self.setFlipVertical),
        ]
        for document_name, callback in self._subscriptions:
            subscribe_to_data(SETTINGS_FILE, document_name, callback)

    def close(self):
        """Stop following the crop and rotation settings."""
        for document_name, callback in self._subscriptions:
            unsubscribe_from_data(SETTINGS_FILE, document_name, callback)

    def setCropMode(self, crop_mode):
        self.isCropSet = crop_mode

    def setCropTop(self, crop_top):
        self.cropTop = crop_top

    def setCropBottom(self, crop_bottom):
        self.cropBottom = crop_bottom

    def setCropLeft(self, crop_left):
        self.cropLeft = crop_left

    def setCropRight(self, crop_right):
        self.cropRight = crop_right

    def setRotation(self, rotation):
        self.rotation = rotation

    def setFlipHorizontal(self, flip):
        self.flipHorizontal = bool(flip)

    def setFlipVertical(self, flip):
        self.flipVertical = bool(flip)


class _UnopenedCapture:
    """A capture that did not open. The caller sees isOpened() False and the
    device is not held, so a later refresh can open it."""

    def isOpened(self) -> bool:
        return False

    def read(self):
        return False, None

    def release(self):
        return None

    def set(self, prop, value):
        return False

    def get(self, prop):
        return 0.0


class OpenCVVideoCaptureWithSettings(BaseVideoCapture):
    """A cv2.VideoCapture shared by the capture thread (read) and the GUI thread
    (the Video Settings dialog), guarded by video_capture_mutex.

    With apply_video_settings (camera devices), the stored Video Settings are
    used when the device opens: its backend, then FOURCC, resolution and fps.
    A stored backend that can't open the device falls back to the default one.
    When they change later (stored from the dialog, on the GUI thread) they are
    applied by the capture thread right before its next read(), so the GUI
    thread never reopens the device. release() stops following the settings.
    """

    def __init__(
        self,
        capture_id: int | str,
        capture_backend: int = cv2.CAP_ANY,
        params: list[int] | None = None,
        apply_video_settings: bool = False,
        source_name: str | None = None,
        stop_check=None,
    ):
        super().__init__(capture_id, capture_backend)
        self.video_capture_mutex = threading.Lock()
        self.params = list(params) if params else []
        self.apply_video_settings = apply_video_settings
        self.source_name = source_name
        # Called between OBS open attempts. True means this open should let go
        # of the device (a refresh or a source change already asked the thread
        # to stop).
        self.stop_check = stop_check
        self._obs_lock_held = False
        self.default_backend = capture_backend
        self.backend = capture_backend
        self.fps = None
        self.width = None
        self.height = None
        self.fourcc = None
        self._stale_grabs = 0
        self._reopen_requested = False
        self._apply_requested = False
        self._subscriptions = []
        if apply_video_settings:
            raw_fps = fetch_data(SETTINGS_FILE, "fps", None)
            # A saved 1 (the dialog's old minimum, used whenever a capture card
            # reported no frame rate) would lock the device at one frame a
            # second. Replace it before anything applies it.
            if is_bad_low_fps(raw_fps):
                logger.info(
                    "Stored frame rate %s is not usable; using %s",
                    raw_fps,
                    DEFAULT_CAPTURE_FPS,
                )
                store_data(SETTINGS_FILE, "fps", DEFAULT_CAPTURE_FPS)
                self.fps = DEFAULT_CAPTURE_FPS
            else:
                self.fps = raw_fps
            self.width = fetch_data(SETTINGS_FILE, "width", None)
            self.height = fetch_data(SETTINGS_FILE, "height", None)
            raw_fourcc = fetch_data(SETTINGS_FILE, "fourcc", None)
            if raw_fourcc not in (None, "", "NULL") and fourcc_code(raw_fourcc) is None:
                logger.info(
                    "Ignoring stored video format %r; it isn't a format name",
                    raw_fourcc,
                )
                remove_data(SETTINGS_FILE, "fourcc")
                raw_fourcc = None
            self.fourcc = raw_fourcc
            self.backend = self._effective_backend(
                fetch_data(SETTINGS_FILE, "backend", None)
            )
            self._subscriptions = [
                ("fps", self.setFps),
                ("width", self.setWidth),
                ("height", self.setHeight),
                ("fourcc", self.setFourcc),
                ("backend", self.setBackend),
            ]
            for document_name, callback in self._subscriptions:
                subscribe_to_data(SETTINGS_FILE, document_name, callback)
        with self.video_capture_mutex:
            self.video_capture = self._open()

    def _effective_backend(self, backend) -> int:
        # A stored backend of "Auto" (CAP_ANY) or none means the platform default.
        try:
            backend = int(backend)
        except (TypeError, ValueError):
            return self.default_backend
        return backend if backend != cv2.CAP_ANY else self.default_backend

    def _create_capture(self, backend, params=None, capture_id=None):
        device = self.capture_id if capture_id is None else capture_id
        if params is None:
            params = self.params
        if params:
            return cv2.VideoCapture(device, backend, list(params))
        return cv2.VideoCapture(device, backend)

    def _open(self):
        # OBS Virtual Camera lies about SetFormat. vMix Video stays on the
        # path below and keeps the stored Video Settings. Media Foundation is
        # only opened later, and only at the index of this friendly name.
        if is_obs_virtual_camera(self.source_name) and platform.system() == "Windows":
            return self._open_obs_virtual_camera()
        capture = self._create_capture(self.backend)
        if not capture.isOpened() and self.backend != self.default_backend:
            # e.g. a backend this OpenCV build doesn't have: falling back keeps
            # the camera (and the Video Settings dialog, to undo the choice)
            # usable
            logger.warning(
                "Unable to open capture %s with backend %s; using the default backend",
                self.capture_id,
                self.backend,
            )
            capture.release()
            self.backend = self.default_backend
            capture = self._create_capture(self.backend)
        if self.apply_video_settings and capture.isOpened():
            self._apply_settings(capture, opening=True)
        return capture

    def _stop_requested(self) -> bool:
        check = self.stop_check
        if check is None:
            return False
        try:
            return bool(check())
        except Exception:
            logger.exception("OBS open stop check failed")
            return False

    def _acquire_obs_graph(self) -> bool:
        # A reopen on this same object still holds the lock. Drop it before
        # taking it again (the lock is not re-entrant).
        self._release_obs_graph()
        if _OBS_GRAPH_LOCK.acquire(timeout=OBS_REOPEN_WAIT_S):
            self._obs_lock_held = True
            return True
        logger.warning(
            "OBS Virtual Camera is still held by the previous open. "
            "Refresh the source again."
        )
        return False

    def _release_obs_graph(self) -> None:
        if not getattr(self, "_obs_lock_held", False):
            return
        self._obs_lock_held = False
        try:
            _OBS_GRAPH_LOCK.release()
        except RuntimeError:
            logger.warning("OBS graph lock was already released")

    def _obs_elapsed_ms(self) -> float:
        started = getattr(self, "_obs_open_started", None)
        if started is None:
            return 0.0
        return (time.monotonic() - started) * 1000.0

    def _past_open_budget(self) -> bool:
        started = getattr(self, "_obs_open_started", None)
        if started is None:
            return False
        try:
            budget = float(OBS_OPEN_BUDGET_S)
        except (TypeError, ValueError):
            budget = 8.0
        if budget < 0:
            budget = 0.0
        return (time.monotonic() - started) >= budget

    def _open_obs_virtual_camera(self):
        """Open OBS at a canvas that has pixels, then give the device up if not.

        Runs on the capture thread (TimerThread.run), not the UI thread.
        Refresh sets the thread's stop flag; the probe checks it between
        reads and releases the graph it opened, including when a read raises.

        DirectShow sizes first (the graph is built with the size, because
        asking afterwards rebuilds it and OBS reports success for a size it
        is not outputting). The first size that has pixels is kept immediately.
        Formats next, for names OpenCV does not force to NV12. Media Foundation
        last, and only when DirectShow never delivered a frame: a flat black
        frame means the device is already open, and a failed MSMF read blocks
        for about 10 s. The search also stops after OBS_OPEN_BUDGET_S so a
        black device shows the Chinese hint instead of trying every canvas.
        The graph lock is held until release() so a refresh cannot open a
        second graph while this one still exists.
        """
        if self._stop_requested():
            logger.info("OBS Virtual Camera: open cancelled")
            return _UnopenedCapture()
        if not self._acquire_obs_graph():
            return _UnopenedCapture()
        self._obs_open_started = time.monotonic()
        try:
            kept, saw_blank = self._probe_obs_attempts(
                obs_size_attempts(self.width, self.height),
                cv2.CAP_DSHOW,
                self.capture_id,
                "DirectShow size",
            )
            if (
                kept is None
                and not self._stop_requested()
                and not self._past_open_budget()
            ):
                kept, saw_format = self._probe_obs_attempts(
                    obs_format_attempts(self.source_name, self.width, self.height),
                    cv2.CAP_DSHOW,
                    self.capture_id,
                    "DirectShow format",
                )
                saw_blank = saw_blank or saw_format
            if kept is not None:
                return kept
            if self._stop_requested():
                logger.info(
                    "OBS Virtual Camera: open cancelled after %.0f ms",
                    self._obs_elapsed_ms(),
                )
            elif saw_blank:
                logger.info(
                    "OBS Virtual Camera: DirectShow delivered only a flat black "
                    "frame after %.0f ms. Not opening Media Foundation.",
                    self._obs_elapsed_ms(),
                )
            elif self._past_open_budget():
                logger.info(
                    "OBS Virtual Camera: no picture after %.0f ms. Not opening "
                    "Media Foundation.",
                    self._obs_elapsed_ms(),
                )
            else:
                kept = self._probe_obs_msmf()
                if kept is not None:
                    return kept
                logger.warning(
                    "OBS Virtual Camera: no picture after %.0f ms. Start Virtual "
                    "Camera in OBS, then refresh the source.",
                    self._obs_elapsed_ms(),
                )
            if not self._stop_requested() and (saw_blank or self._past_open_budget()):
                logger.warning(
                    "OBS Virtual Camera: no picture. Start Virtual Camera in OBS, "
                    "then refresh the source."
                )
            self._release_obs_graph()
            return _UnopenedCapture()
        except Exception:
            self._release_obs_graph()
            raise

    def _probe_obs_msmf(self):
        names = msmf_friendly_names()
        index = msmf_index_for_name(names, self.source_name)
        if index is None:
            listed = ", ".join(names) if names else "(none)"
            logger.info(
                "OBS Virtual Camera: Media Foundation has no device named %r (%s)",
                self.source_name,
                listed,
            )
            return None
        logger.info(
            "OBS Virtual Camera: Media Foundation index %s for %r "
            "(DirectShow index was %s)",
            index,
            self.source_name,
            self.capture_id,
        )
        kept, _saw_blank = self._probe_obs_attempts(
            obs_msmf_attempts(self.width, self.height),
            cv2.CAP_MSMF,
            index,
            "Media Foundation",
        )
        return kept

    def _probe_obs_attempts(self, attempts, backend, device_id, label):
        """(kept capture or None, whether a delivered frame was flat black).

        The first attempt always runs. Later ones stop when refresh asked, or
        when the open has already used OBS_OPEN_BUDGET_S.
        """
        saw_blank = False
        for index, (width, height, fourcc) in enumerate(attempts):
            if self._stop_requested():
                logger.info("OBS Virtual Camera: open cancelled before %s", label)
                return None, saw_blank
            if index > 0 and self._past_open_budget():
                logger.info(
                    "OBS Virtual Camera: stopping %s after %.0f ms",
                    label,
                    self._obs_elapsed_ms(),
                )
                return None, saw_blank
            size = (
                "native"
                if width is None or height is None
                else f"{int(width)}x{int(height)}"
            )
            logger.info(
                "OBS Virtual Camera: %s %s format %s device %s",
                label,
                size,
                fourcc or "default",
                device_id,
            )
            params: list[int] = []
            if backend == cv2.CAP_DSHOW and width is not None and height is not None:
                params = [
                    cv2.CAP_PROP_FRAME_WIDTH,
                    int(width),
                    cv2.CAP_PROP_FRAME_HEIGHT,
                    int(height),
                ]
                code = fourcc_code(fourcc) if fourcc else None
                if code is not None:
                    params.extend((cv2.CAP_PROP_FOURCC, int(code)))
            # A probe that raises must still release this graph. Leaving it
            # open holds OBS's exclusive pin while the next size is tried.
            capture = None
            try:
                try:
                    capture = self._create_capture(backend, params, device_id)
                except cv2.error:
                    logger.info(
                        "OBS Virtual Camera: %s %s could not be opened", label, size
                    )
                    continue
                try:
                    opened = bool(capture.isOpened())
                except cv2.error:
                    opened = False
                if (
                    opened
                    and backend == cv2.CAP_MSMF
                    and width is not None
                    and height is not None
                ):
                    # The constructor does not apply a size on MSMF. set() does.
                    try:
                        code = fourcc_code(fourcc) if fourcc else None
                        if code is not None:
                            capture.set(cv2.CAP_PROP_FOURCC, int(code))
                        capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
                        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
                    except cv2.error:
                        logger.info(
                            "OBS Virtual Camera: %s %s could not be sized",
                            label,
                            size,
                        )
                        continue
                if not opened:
                    logger.info("OBS Virtual Camera: %s %s did not open", label, size)
                    continue
                if self._stop_requested():
                    logger.info("OBS Virtual Camera: open cancelled during %s", label)
                    return None, saw_blank
                try:
                    result = obs_probe_result(capture, stop_check=self._stop_requested)
                except Exception:
                    logger.exception(
                        "OBS Virtual Camera: %s %s probe failed", label, size
                    )
                    result = "failed"
                if result == "stopped" or self._stop_requested():
                    logger.info("OBS Virtual Camera: open cancelled during %s", label)
                    return None, saw_blank
                if result == "blank":
                    saw_blank = True
                    logger.info(
                        "OBS Virtual Camera: %s %s was flat black; releasing it",
                        label,
                        size,
                    )
                    continue
                if result != "picture":
                    logger.info(
                        "OBS Virtual Camera: %s %s had no frame; releasing it",
                        label,
                        size,
                    )
                    continue
                kept = capture
                capture = None
                self.backend = backend
                try:
                    self._stale_grabs = request_latest_frame_buffer(kept)
                except Exception:
                    logger.exception(
                        "OBS Virtual Camera: could not shorten the frame queue"
                    )
                    self._stale_grabs = 0
                logger.info(
                    "OBS Virtual Camera: picture via %s at %s in %.0f ms",
                    label,
                    size,
                    self._obs_elapsed_ms(),
                )
                return kept, saw_blank
            finally:
                if capture is not None:
                    _release_capture(capture)
        return None, saw_blank

    def _apply_settings(self, capture, opening: bool):
        if is_obs_virtual_camera(self.source_name) and platform.system() == "Windows":
            # FOURCC, size and fps each rebuild the graph. OBS reports success
            # for a format it is not outputting, and the picture goes black.
            # The open path already kept a canvas that had a picture.
            self._stale_grabs = request_latest_frame_buffer(capture)
            return
        # FOURCC first: e.g. MJPG is what unlocks the high resolutions and frame
        # rates on many USB cameras.
        code = fourcc_code(self.fourcc)
        if code is not None:
            capture.set(cv2.CAP_PROP_FOURCC, code)
        width = _positive(self.width)
        height = _positive(self.height)
        if width is not None and height is not None:
            set_resolution(capture, int(width), int(height))
        elif opening:
            # No saved size: keep the driver default, or cap a 4K / virtual
            # open. Do not climb toward the highest mode the device offers.
            cap_unbounded_resolution(capture, self.source_name)
        # Nothing saved (None) leaves the driver's rate. A stored 1 is 25.
        # Do not set CAP_PROP_FPS when the user has not chosen one.
        fps = fps_for_capture(self.fps)
        if fps is not None:
            capture.set(cv2.CAP_PROP_FPS, fps)
        # After the format, so a mode change does not restore a deep queue.
        self._stale_grabs = request_latest_frame_buffer(capture)

    def _apply_pending_changes(self):
        # called by the reading thread with the mutex held
        if self._reopen_requested:
            self._reopen_requested = False
            self._apply_requested = False
            logger.info(
                "Reopening capture %s with backend %s", self.capture_id, self.backend
            )
            self.video_capture.release()
            self.video_capture = self._open()
        elif self._apply_requested:
            self._apply_requested = False
            self._apply_settings(self.video_capture, opening=False)

    def setFps(self, fps):
        self.fps = fps
        self._apply_requested = True

    def setWidth(self, width):
        self.width = width
        self._apply_requested = True

    def setHeight(self, height):
        self.height = height
        self._apply_requested = True

    def setFourcc(self, fourcc: str):
        self.fourcc = fourcc
        self._apply_requested = True

    def setBackend(self, backend):
        backend = self._effective_backend(backend)
        if backend == self.backend:
            return
        self.backend = backend
        self._reopen_requested = True

    def isOpened(self):
        with self.video_capture_mutex:
            return self.video_capture.isOpened()

    def read(self):
        with self.video_capture_mutex:
            self._apply_pending_changes()
            # Files and URLs (apply_video_settings off) keep a plain read so
            # playback is not drained or blocked waiting on a camera queue.
            if self._stale_grabs > 0:
                return read_newest_frame(self.video_capture, self._stale_grabs)
            return self.video_capture.read()

    def release(self):
        for document_name, callback in self._subscriptions:
            unsubscribe_from_data(SETTINGS_FILE, document_name, callback)
        self._subscriptions = []
        try:
            with self.video_capture_mutex:
                self.video_capture.release()
        finally:
            self._release_obs_graph()

    def __del__(self):
        # A capture that was not release()'d must not keep the next OBS open
        # waiting out the lock timeout.
        self._release_obs_graph()

    def set(self, propId, value):
        with self.video_capture_mutex:
            self.video_capture.set(propId, value)

    def get(self, propId):
        with self.video_capture_mutex:
            return self.video_capture.get(propId)

    def run_locked(self, function):
        """Call function(cv2.VideoCapture) with the mutex held, e.g. to get or
        set many properties while waiting for the reading thread only once."""
        with self.video_capture_mutex:
            return function(self.video_capture)


# Capture threads that were started and haven't finished. Holding them here
# keeps a running QThread from being garbage collected (which aborts the
# process) when its view lets go of it without waiting for it to stop.
_active_threads: set["TimerThread"] = set()


def _stop_active_threads_at_exit():
    threads = list(_active_threads)
    for thread in threads:
        thread.should_stop = True
    deadline = time.monotonic() + STOP_TIMEOUT_MS / 1000.0
    for thread in threads:
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        if not thread.wait(remaining_ms):
            logger.warning("Camera thread is still running at exit")


atexit.register(_stop_active_threads_at_exit)


class OcrWorker:
    """Reads the boxes of a TimerThread's frames on a thread of its own, so
    that the capture loop keeps reading frames while the OCR runs. It holds
    at most one frame: the capture loop hands it a frame only when it is
    idle (see TimerThread.process_frame), so the OCR always reads the latest
    frame and never a queue of stale ones."""

    def __init__(self, owner: "TimerThread"):
        self.owner = owner
        self._condition = threading.Condition()
        self._pending = None
        self._busy = False
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="ocr", daemon=True)

    def start(self):
        self._thread.start()

    def idle(self) -> bool:
        """Whether it can take a frame now (no frame waiting or being read)."""
        with self._condition:
            return self._pending is None and not self._busy and not self._stopping

    def submit(self, frame, frame_time: float):
        with self._condition:
            self._pending = (frame, frame_time)
            self._condition.notify()

    def stop(self, timeout: float | None = None) -> bool:
        """Stop after the cycle in progress (up to timeout seconds); returns
        whether the thread ended."""
        with self._condition:
            self._stopping = True
            self._pending = None
            self._condition.notify()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout)
        return not self._thread.is_alive()

    def _run(self):
        while True:
            with self._condition:
                while self._pending is None and not self._stopping:
                    self._condition.wait()
                if self._stopping:
                    return
                frame, frame_time = self._pending
                self._pending = None
                self._busy = True
            try:
                self.owner.run_ocr_cycle(frame, frame_time)
            except Exception:
                # run_ocr_cycle reports its own errors; this is a last resort
                logger.exception("OCR cycle failed")
            finally:
                with self._condition:
                    self._busy = False


class TimerThread(QThread):
    update_signal = Signal(object)
    # the source failed (can't be opened or read): it is stopping
    update_error = Signal(object)
    # OBS frames went blank (True) or a picture came back (False). The capture
    # keeps running; the window shows the virtual-camera hint over the picture.
    blank_preview = Signal(bool)
    # a frame couldn't be processed; the capture keeps running
    processing_error = Signal(object)
    ocr_result_signal = Signal(list)

    def __init__(
        self,
        camera_info: CameraInfo,
        detectionTargetsStorage: TextDetectionTargetMemoryStorage,
    ):
        super().__init__()
        self.camera_info = camera_info
        self.homography = None
        self.detectionTargetsStorage = detectionTargetsStorage
        self.textDetector = TextDetector()  # initialize tesseract
        self.show_binary = False
        self.retry_count = 0
        self.retry_count_max = 50
        self.retry_high_water_mark = 25
        self.stabilizationEnabled = False
        self.framestabilizer = FrameStabilizer()
        self.video_capture: BaseVideoCapture | None = None
        self.should_stop = False
        self._obs_blank_streak = 0
        # monotonic clock (seconds); replaceable in tests
        self._clock = time.monotonic
        # Screen capture's read is a full window copy. Matching the preview
        # rate avoids copying frames that are thrown away. Cameras and video
        # files stay at the capture rate so a file is not played as a slideshow.
        if camera_info.type == CameraInfo.CameraType.SCREEN_CAPTURE:
            self.frame_interval = PREVIEW_INTERVAL_MS
        else:
            self.frame_interval = CAPTURE_FRAME_INTERVAL_MS
        self.update_frame_interval = cadence_to_interval_ms(
            fetch_data(SETTINGS_FILE, "detection_cadence", DEFAULT_DETECTION_CADENCE)
        )
        subscribe_to_data(
            SETTINGS_FILE, "detection_cadence", self.setUpdateFrameInterval
        )
        self.preview_frame_interval = PREVIEW_INTERVAL_MS
        # The viewer sets this: skip a preview whose paint is still on the GUI
        # thread, instead of queuing stale pictures behind it.
        self.coalesce_previews = False
        self.preview_busy = False
        self.fps = 1000 / self.frame_interval  # frames per second
        self.pps = 1000 / self.preview_frame_interval  # previews per second
        self.ups = 1000 / max(self.update_frame_interval, 1)  # updates per second
        self.fps_alpha = 0.1  # Smoothing factor
        self.updateOnChange = True
        self.crop = FrameCropAndRotation()
        self.speed = 1
        # (width, height) of the last frame read from the source
        self.frame_size = (0, 0)
        # the source's own frame rate (capped at what the loop reads), None if
        # it doesn't tell: the window compares fps with it
        self.nominal_fps: float | None = None
        # While new four-corner points are picked the preview shows the picture
        # without the four-corner correction (the corners are clicked on it).
        # The OCR keeps reading the corrected picture until the new correction
        # replaces it.
        self.preview_uncorrected = False
        # While a crop is dragged (Drag to crop) the preview shows the whole
        # rotated frame, without the crop and the corrections after it. The OCR
        # keeps reading the cropped picture until the new crop replaces it.
        self.preview_uncropped = False
        # When the boxes last moved with the crop (boxesMoved), on _clock
        self.boxes_moved_at: float | None = None
        # The last frame transformed (about 15 times a second, or on an OCR
        # pass), as read and as the boxes see it (rotated, cropped, stabilized
        # and corrected): what Auto crop and Auto layout look at
        # (latestRotatedFrame, latestPicture)
        self.latest_source = None
        self.latest_picture = None
        self.last_frame_timestamp = None
        self.last_update_timestamp = None
        self.last_emit_time = None
        # the OCR's thread while the capture runs (None: OCR on the caller's)
        self._ocr_worker: OcrWorker | None = None
        # seconds from reading a frame to publishing its boxes' readings: the
        # last one, and smoothed
        self.last_reading_latency: float | None = None
        self.reading_latency: float | None = None
        self._iteration_start = None
        self._last_error_report = None
        self.finished.connect(self._on_finished)

    @property
    def update_frame_interval(self) -> float:
        """Time between OCR passes in ms (1000 / detection_cadence)."""
        return self._update_frame_interval

    @update_frame_interval.setter
    def update_frame_interval(self, interval_ms):
        # also set directly by the main window when the cadence slider moves
        self._update_frame_interval = _clamp_interval_ms(interval_ms)

    def getSpeed(self):
        return self.speed

    def setSpeed(self, speed):
        self.speed = speed

    def setUpdateFrameInterval(self, cadence):
        self.update_frame_interval = cadence_to_interval_ms(cadence)

    def start(self, *args, **kwargs):
        _active_threads.add(self)
        super().start(*args, **kwargs)

    @Slot()
    def _on_finished(self):
        # Queued to the thread that owns this object (the GUI thread) after
        # run() returned.
        self.wait()
        _active_threads.discard(self)

    def stop(self, timeout_ms: int = STOP_TIMEOUT_MS) -> bool:
        """Ask the thread to stop and wait up to timeout_ms for it. Returns False
        if it is still busy (e.g. blocked reading a stream); it then finishes in
        the background."""
        self.should_stop = True
        if self.wait(int(timeout_ms)):
            _active_threads.discard(self)
            return True
        logger.warning(
            "Camera thread for '%s' did not stop within %d ms; it will finish in "
            "the background",
            self.camera_info.description,
            timeout_ms,
        )
        return False

    def cleanup(self):
        """Stop following settings changes. Safe to call more than once, from
        any thread."""
        unsubscribe_from_data(
            SETTINGS_FILE, "detection_cadence", self.setUpdateFrameInterval
        )
        self.crop.close()

    def _create_video_capture(self) -> BaseVideoCapture:
        camera_type = self.camera_info.type
        if camera_type == CameraInfo.CameraType.NDI:
            # imported when first used (or by the source discovery): cyndilib
            # and the NDI library take a while to load
            from ndi import NDICapture

            return NDICapture(self.camera_info.uuid)
        if camera_type == CameraInfo.CameraType.SCREEN_CAPTURE:
            # imported when first used: it loads pywin32 / pyobjc's Quartz,
            # which would slow down every start
            from screen_capture_source import ScreenCapture

            return ScreenCapture(self.camera_info.id)
        if camera_type == CameraInfo.CameraType.URL:
            url = str(self.camera_info.id).strip()
            ensure_ffmpeg_capture_options()
            return OpenCVVideoCaptureWithSettings(
                url, cv2.CAP_FFMPEG, params=URL_CAPTURE_PARAMS
            )
        if camera_type == CameraInfo.CameraType.FILE:
            return OpenCVVideoCaptureWithSettings(self.camera_info.id)
        # a camera device: on windows use the dshow backend by default
        backend = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
        return OpenCVVideoCaptureWithSettings(
            self.camera_info.id,
            backend,
            apply_video_settings=True,
            source_name=self.camera_info.description,
            stop_check=lambda: self.should_stop,
        )

    def connect_video_capture(self) -> bool:
        try:
            self.video_capture = self._create_video_capture()
        except Exception:
            logger.exception("Error: unable to open the video source")
            self.video_capture = None
            self.update_error.emit("Error: Unable to play video stream")
            return False

        if self.retry_count != self.retry_high_water_mark:
            # at the high water mark this is a reconnect
            self.retry_count = 0

        if not self.video_capture.isOpened():
            logger.warning(
                "Error: unable to open camera. Check if the camera is connected."
            )
            self.update_error.emit("Error: Unable to play video stream")
            return False

        self.nominal_fps = self._read_nominal_fps()
        return True

    def _read_nominal_fps(self) -> float | None:
        """The frame rate the source reports (a camera's mode, a file's or a
        stream's rate), capped at the capture loop's own rate; None if it
        reports none or a bogus one (screen captures and NDI report 0, some
        streams 90000)."""
        try:
            fps = float(self.video_capture.get(cv2.CAP_PROP_FPS))
        except Exception:
            return None
        # 1 (and below) is a failed read on many capture cards, not a mode
        if not math.isfinite(fps) or fps < MIN_CAPTURE_FPS or fps > 1000:
            return None
        return min(fps, 1000.0 / self.frame_interval)

    def requestPreview(self):
        """Emit the next frame as the preview, without waiting for the preview
        interval (e.g. right after the four-corner picking started)."""
        self.last_emit_time = None

    def _release_capture(self):
        capture, self.video_capture = self.video_capture, None
        if capture is not None:
            try:
                capture.release()
            except Exception:
                logger.exception("Error releasing the video capture")

    def run(self):
        description_ascii = (
            self.camera_info.description.encode("ascii", errors="ignore").decode()
            if isinstance(self.camera_info.description, str)
            else str(self.camera_info.description)
        )
        logger.info("Starting camera thread for: '%s'", description_ascii)

        try:
            if not self.connect_video_capture():
                self.should_stop = True
                return
            self._ocr_worker = OcrWorker(self)
            self._ocr_worker.start()
            while not self.should_stop:
                self.run_once()
        except Exception:
            logger.exception("Camera thread failed")
            self.update_error.emit("Error: Unable to play video stream")
        finally:
            worker, self._ocr_worker = self._ocr_worker, None
            if worker is not None and not worker.stop(OCR_STOP_TIMEOUT_S):
                logger.warning("The OCR cycle in progress did not finish in time")
            self._release_capture()
            self.cleanup()
            logger.info("Camera thread stopped")

    def run_once(self):
        """One iteration of the capture loop: read a frame (reconnecting or
        giving up after repeated failures) and process it."""
        self._iteration_start = self._clock()
        if self.video_capture is None:
            logger.warning("Error: video capture is None")
            self.should_stop = True
            return

        if self.retry_count == self.retry_high_water_mark:
            logger.warning("Error: retry high water mark exceeded")
            # reconnect the video cap
            self._release_capture()
            if not self.connect_video_capture():
                self.should_stop = True
                return
            self.sleep_fps_target()
            self.retry_count += 1
            return
        if self.retry_count > self.retry_count_max:
            logger.warning("Error: retry count exceeded")
            self.should_stop = True
            self.update_error.emit("Error: Unable to play video stream")
            return

        # Read the frame from the camera
        ret, frame_rgb = None, None
        try:
            ret, frame_rgb = self.video_capture.read()
        except Exception as e:
            self.retry_count += 1
            logger.exception(
                "Error: unable to read frame from camera (retry count: %d), exception %s",
                self.retry_count,
                e,
            )
            self.sleep_fps_target()
            return

        if self.camera_info.type == CameraInfo.CameraType.FILE:
            if self.speed != 1:
                self.video_capture.set(
                    cv2.CAP_PROP_POS_FRAMES,
                    self.video_capture.get(cv2.CAP_PROP_POS_FRAMES) + self.speed,
                )

        if not ret or frame_rgb is None or frame_rgb.size == 0:
            self.retry_count += 1
            if self.camera_info.type == CameraInfo.CameraType.FILE:
                logger.debug("Restarting video file")
                self.video_capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self.sleep_fps_target()
                return
            logger.warning(
                "Error: unable to read frame from camera, return value False (retry count: %d)",
                self.retry_count,
            )
            self.sleep_fps_target()
            return

        self.retry_count = 0  # good frame, reset the retry count
        self._observe_obs_frame(frame_rgb)
        try:
            self.process_frame(frame_rgb)
        except Exception as e:
            self._report_processing_error(e)
        self.sleep_fps_target()

    def _observe_obs_frame(self, frame) -> None:
        """Warn once a live OBS picture has been blank, and clear that when it returns.

        vMix Video and capture cards are not watched: a covered lens is a dark
        frame, not a virtual camera that was never started.
        """
        if not is_obs_virtual_camera(getattr(self.camera_info, "description", None)):
            return
        streak, event = note_obs_blank(self._obs_blank_streak, frame_is_blank(frame))
        self._obs_blank_streak = streak
        if event == "blank":
            logger.warning(
                "OBS Virtual Camera: %d blank frames in a row. Start Virtual "
                "Camera in OBS, then refresh.",
                streak,
            )
            self.blank_preview.emit(True)
        elif event == "clear":
            logger.info("OBS Virtual Camera: picture is back")
            self.blank_preview.emit(False)

    def _report_processing_error(self, error):
        # Called from an except block. Keeps the thread (and the capture) alive;
        # an error that repeats on every frame is reported once in a while.
        now = self._clock()
        if (
            self._last_error_report is not None
            and now - self._last_error_report < ERROR_REPORT_INTERVAL_S
        ):
            return
        self._last_error_report = now
        logger.exception("Error: unable to process the video frame")
        self.processing_error.emit(f"Error: unable to process the video frame: {error}")

    def process_frame(self, frame_rgb):
        """Process a frame read from the source: on the detection cadence read
        its boxes (OCR), and about 15 times a second emit it as the preview.
        The two are independent, so the preview (and with it the first-frame
        signal that enables the UI) comes at any detection cadence. Cameras,
        virtual cameras and video files share that preview rate. A file's
        playback speed stays the capture loop's rate, not the old 1/s preview.

        While the capture runs (run()), the OCR runs on its own thread
        (OcrWorker), so a slow OCR cycle never holds up reading frames: a live
        source's frames don't go stale in its buffer and a video file plays at
        the loop's rate. A frame is handed to the OCR only when it is idle, so
        it always reads the latest frame and the frames read meanwhile are
        dropped (they may still be previewed). Without a running capture (the
        tests) the OCR runs here, on the calling thread."""
        now = self._clock()
        self.frame_size = (frame_rgb.shape[1], frame_rgb.shape[0])

        # calculate the frame rate
        if self.last_frame_timestamp is not None:
            time_diff_ms = (now - self.last_frame_timestamp) * 1000.0
            if time_diff_ms > 0:
                self.fps = (
                    self.fps_alpha * (1000 / time_diff_ms)
                    + (1.0 - self.fps_alpha) * self.fps
                )
        self.last_frame_timestamp = now

        # check that enough time has passed since the last update and preview,
        # and that the OCR is free to take this frame
        worker = self._ocr_worker
        update_due = (
            self.last_update_timestamp is None
            or (now - self.last_update_timestamp) * 1000.0 >= self.update_frame_interval
        ) and (worker is None or worker.idle())
        preview_due = (
            self.last_emit_time is None
            or (now - self.last_emit_time) * 1000.0 >= self.preview_frame_interval
        )
        # One picture is painted at a time. Queueing the next while that paint
        # is still on the GUI thread is what makes a live view lag a beat
        # behind, then jump. Skip this preview; the next free one is current.
        if preview_due and self.coalesce_previews and self.preview_busy:
            preview_due = False
        if not update_due and not preview_due:
            # dump this frame since not enough time has passed
            return

        source = frame_rgb
        adjusted = self.adjust_frame(frame_rgb)
        frame_rgb = self.correct_frame(adjusted)
        self.latest_source = source
        self.latest_picture = frame_rgb
        show_binary = self.show_binary
        targets = None
        gray = None
        display = None

        if update_due:
            if self.last_update_timestamp is not None:
                time_diff_ms = (now - self.last_update_timestamp) * 1000.0
                if time_diff_ms > 0:
                    self.ups = (
                        self.fps_alpha * (1000 / time_diff_ms)
                        + (1.0 - self.fps_alpha) * self.ups
                    )
            self.last_update_timestamp = now
            if worker is not None:
                worker.submit(frame_rgb, now)
            else:
                targets, gray, display = self.run_ocr_cycle(
                    frame_rgb, now, want_display=show_binary and preview_due
                )

        if preview_due:
            if self.last_emit_time is not None:
                time_diff_ms = (now - self.last_emit_time) * 1000.0
                if time_diff_ms > 0:
                    self.pps = (
                        self.fps_alpha * (1000 / time_diff_ms)
                        + (1.0 - self.fps_alpha) * self.pps
                    )
            self.last_emit_time = now
            if self.coalesce_previews:
                self.preview_busy = True
            if self.preview_uncropped:
                # a new crop is being dragged on the whole picture
                self.update_signal.emit(self.orient_frame(source))
            elif self.preview_uncorrected:
                # new four-corner points are being picked on this picture
                self.update_signal.emit(adjusted)
            elif show_binary:
                if display is None:
                    if gray is None:
                        gray = to_gray(frame_rgb)
                    if targets is None:
                        targets = self._snapshot_targets()
                    uses_ppocr = getattr(self.textDetector, "uses_ppocr", None)
                    display = render_binary_view(
                        global_binarization(gray),
                        gray,
                        targets,
                        ppocr=bool(uses_ppocr and uses_ppocr()),
                    )
                self.update_signal.emit(display)
            else:
                self.update_signal.emit(frame_rgb)

    def latestRotatedFrame(self):
        """The last frame rotated and mirrored, before the crop (the picture
        Drag to crop and Auto crop look at), or None before one."""
        source = self.latest_source
        return None if source is None else self.orient_frame(source)

    def latestPicture(self):
        """The last picture the boxes were read from, or None before one."""
        return self.latest_picture

    def boxesMoved(self):
        """The boxes were moved to stay on the same part of the picture,
        because the crop's corner moved. A frame read until now was cropped
        as before: read with the boxes' new places, it would give readings of
        other pixels, so they are dropped (the OCR worker may hold one)."""
        self.boxes_moved_at = self._clock()

    def readBeforeBoxesMoved(self, frame_time) -> bool:
        moved = self.boxes_moved_at
        return moved is not None and frame_time is not None and frame_time <= moved

    def run_ocr_cycle(self, frame_rgb, frame_time, want_display=False):
        """Read the boxes of a (transformed) frame read at frame_time and
        publish the results. Returns (the boxes, the gray frame, the binary
        view with what the OCR read drawn in, if want_display or OCR training
        data is collected; else None)."""
        # One snapshot of the boxes per frame: the GUI thread may add or
        # remove boxes while the OCR runs.
        targets = self._snapshot_targets()
        if not targets or self.readBeforeBoxesMoved(frame_time):
            return [], None, None
        gray = to_gray(frame_rgb)
        binary = None
        display = None
        if want_display or ocr_training_data_options.save_ocr_training_data:
            binary = global_binarization(gray)
            display = binary.copy()
        try:
            # what PP-OCR (automatic) keeps its reading time under
            self.textDetector.cycle_interval_ms = self.update_frame_interval
            # binary=None: the OCR computes the global binarization only if a
            # box uses it; PP-OCR reads the colour frame
            texts = self.textDetector.detect_multi_text(
                binary, gray, targets, display=display, color=frame_rgb
            )
            self._publish_results(targets, texts, display, gray, frame_time)
        except Exception as e:
            # the preview is still emitted
            self._report_processing_error(e)
        return targets, gray, display

    def _snapshot_targets(self):
        if self.detectionTargetsStorage is None:
            return []
        return list(self.detectionTargetsStorage.get_data())

    def _published_reading(self, target, result) -> tuple[str, ResultState]:
        """The (text, state) to publish for a box's OCR result.

        SameNoChange always means "the same as the box's last good reading"
        (target.last_text, which only a Success reading sets):
        - with "update on change", a Success reading equal to the last good one;
        - a box whose OCR was skipped because its image hasn't changed (skip
          similar image). Without a good reading yet, that box is Empty.
        So a rejected reading (FailedFilter, e.g. text that fails the box's
        format) is never repeated as SameNoChange, which the outputs treat as
        a good reading.

        With "When blank" set (clear_value), a box that has been blank (or
        zero) for CLEAR_AFTER_SECONDS is read as an empty good reading: a
        Success "" once, then SameNoChange "" while it stays blank.
        """
        cleared = self._cleared_reading(target, result)
        if cleared is not None:
            return cleared
        if result.unchanged_image:
            if target.last_text is None:
                return "", ResultState.Empty
            return target.last_text, ResultState.SameNoChange
        if result.state != ResultState.Success:
            return result.text, result.state
        if self.updateOnChange and result.text == target.last_text:
            return result.text, ResultState.SameNoChange
        target.last_text = result.text
        return result.text, ResultState.Success

    def _cleared_reading(self, target, result) -> tuple[str, ResultState] | None:
        """The empty reading of a box that "When blank" clears, or None to
        publish the reading as usual. A blank box is one read as Empty (or,
        with CLEAR_BLANK_OR_ZERO, as zero); a box blank for less than
        CLEAR_AFTER_SECONDS (a flicker, a hand in front of the board) keeps
        its value."""
        clear = (target.settings or {}).get("clear_value", CLEAR_NEVER)
        if clear == CLEAR_NEVER:
            target.blank_since = None
            return None
        blank = not result.unchanged_image and (
            result.state == ResultState.Empty
            or (
                clear == CLEAR_BLANK_OR_ZERO
                and result.state == ResultState.Success
                and is_zero_text(result.text)
            )
        )
        if not blank:
            target.blank_since = None
            return None
        now = self._clock()
        if target.blank_since is None:
            target.blank_since = now
        if target.last_text == "":
            # cleared already
            return "", ResultState.SameNoChange
        if now - target.blank_since < CLEAR_AFTER_SECONDS:
            return None
        target.last_text = ""
        # the value cleared is forgotten: when the board shows it again, it is
        # read (skip similar image would repeat the empty value) and isn't
        # smoothed with the readings before
        target.last_image = None
        target.ocrResultPerCharacterSmoother.clear()
        return "", ResultState.Success

    def _publish_results(self, targets, texts, display, gray, frame_time=None):
        if len(texts) == 0 or len(targets) != len(texts):
            return
        if self.readBeforeBoxesMoved(frame_time):
            # the boxes moved with the crop while this frame was read
            return
        # augment the text detection targets with the results
        results = []
        for target, result in zip(targets, texts):
            text, state = self._published_reading(target, result)
            results.append(
                TextDetectionTargetWithResult(
                    target, text, state, result.rect, result.extra
                )
            )

        # The GUI thread edits the results (template fields) once they are
        # emitted, so the training data is snapshotted first.
        samples = None
        if ocr_training_data_options.save_ocr_training_data and display is not None:
            samples = snapshot_training_samples(results)

        if frame_time is not None:
            latency = max(0.0, self._clock() - frame_time)
            self.last_reading_latency = latency
            self.reading_latency = (
                latency
                if self.reading_latency is None
                else self.fps_alpha * latency
                + (1.0 - self.fps_alpha) * self.reading_latency
            )
        self.ocr_result_signal.emit(results)

        if samples:
            # save the image patches and the texts
            ocr_training_data_options.save_ocr_results_to_folder(display, gray, samples)

    def transform_frame(self, frame_rgb):
        """Apply the rotation, crop, stabilization and four-corner homography."""
        return self.correct_frame(self.adjust_frame(frame_rgb))

    def rotate_frame(self, frame_rgb):
        """Apply the rotation."""
        rotation = self.crop.rotation
        if rotation != 0:
            # use cv2.rotate to rotate the frame
            rotateCode = (
                cv2.ROTATE_90_CLOCKWISE
                if rotation == 90
                else (
                    cv2.ROTATE_90_COUNTERCLOCKWISE
                    if rotation == 270
                    else cv2.ROTATE_180
                )
            )
            frame_rgb = cv2.rotate(frame_rgb, rotateCode)
        return frame_rgb

    def orient_frame(self, frame_rgb):
        """Rotation, then mirror, in the space the crop and the boxes use.

        The mirror is of the picture as shown, so a horizontal flip stays
        left-right after a 90° rotation. The preview, the boxes and the OCR
        all read this picture.
        """
        crop = self.crop
        return apply_flip(
            self.rotate_frame(frame_rgb),
            bool(crop.flipHorizontal),
            bool(crop.flipVertical),
        )

    def adjust_frame(self, frame_rgb):
        """Apply the rotation, mirror, crop and stabilization."""
        crop = self.crop
        frame_rgb = self.orient_frame(frame_rgb)

        # apply top-level crop if set
        if crop.isCropSet:
            frame_rgb = crop_frame(
                frame_rgb, crop.cropTop, crop.cropBottom, crop.cropLeft, crop.cropRight
            )

        # Stabilize the frame
        if self.stabilizationEnabled:
            frame_rgb = self.framestabilizer.stabilize_frame(frame_rgb)
        return frame_rgb

    def correct_frame(self, frame_rgb):
        """Apply the four-corner homography (the perspective correction)."""
        homography = self.homography
        if homography is not None:
            frame_rgb = cv2.warpPerspective(
                frame_rgb, homography, (frame_rgb.shape[1], frame_rgb.shape[0])
            )
        return frame_rgb

    def sleep_fps_target(self):
        # cap the loop at about 1000 / frame_interval iterations per second
        if self._iteration_start is None:
            return
        time_diff_ms = (self._clock() - self._iteration_start) * 1000.0
        if time_diff_ms < self.frame_interval:
            time.sleep((self.frame_interval - time_diff_ms) / 1000.0)

    def toggleStabilization(self, state):
        self.stabilizationEnabled = state
        if not state:
            self.framestabilizer.reset()

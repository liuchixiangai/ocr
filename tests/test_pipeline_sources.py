"""Source discovery and capture backends: NDI (src/ndi.py), Linux V4L2
enumeration (src/get_camera_info.py) and macOS screen capture
(src/screen_capture_source_mac.py, exercised with a fake Quartz)."""

import importlib
import os
import sys
import time
import types

import numpy as np
import pytest

from camera_info import CameraInfo

from get_camera_info import get_camera_info_linux, query_v4l2_device


# --- NDI ------------------------------------------------------------------------------------


class FakeFinder:
    """Mimics cyndilib's Finder: wait_for_sources() is True only if the source
    list *changed* within the timeout."""

    def __init__(self, names=(), appear_while_waiting=()):
        self.names = list(names)
        self.appear_while_waiting = list(appear_while_waiting)
        self.waits = []
        self.closed = False

    def update_sources(self):
        return list(self.names)

    def wait_for_sources(self, timeout):
        self.waits.append(timeout)
        if self.appear_while_waiting:
            self.names += self.appear_while_waiting
            self.appear_while_waiting = []
            return True
        return False

    def close(self):
        self.closed = True


@pytest.fixture
def ndi(monkeypatch):
    ndi = pytest.importorskip("ndi")
    monkeypatch.setattr(ndi.NDICapture, "finder", None)
    monkeypatch.setattr(ndi.NDICapture, "_finder_created", time.monotonic() - 60)
    return ndi


def test_ndi_sources_are_listed_on_every_refresh(ndi, monkeypatch):
    finder = FakeFinder(["STUDIO (Camera 1)", "STUDIO (Scoreboard)"])
    monkeypatch.setattr(ndi.NDICapture, "finder", finder)
    for _ in range(3):
        sources = ndi.NDICapture.get_camera_info_ndi()
        assert [(s.description, s.uuid, s.type) for s in sources] == [
            ("STUDIO (Camera 1)", "STUDIO (Camera 1)", CameraInfo.CameraType.NDI),
            ("STUDIO (Scoreboard)", "STUDIO (Scoreboard)", CameraInfo.CameraType.NDI),
        ]
    # nothing was found to be missing, so the GUI thread was never blocked
    assert finder.waits == []


def test_ndi_waits_for_discovery_only_right_after_startup(ndi, monkeypatch):
    finder = FakeFinder(appear_while_waiting=["STUDIO (Scoreboard)"])
    monkeypatch.setattr(ndi.NDICapture, "finder", finder)
    monkeypatch.setattr(ndi.NDICapture, "_finder_created", time.monotonic())
    sources = ndi.NDICapture.get_camera_info_ndi(timeout=1.0)
    assert [s.uuid for s in sources] == ["STUDIO (Scoreboard)"]
    assert len(finder.waits) == 1 and 0 < finder.waits[0] <= 1.0


def test_ndi_without_sources_does_not_block_later_refreshes(ndi, monkeypatch):
    finder = FakeFinder()
    monkeypatch.setattr(ndi.NDICapture, "finder", finder)
    assert ndi.NDICapture.get_camera_info_ndi() == []
    assert finder.waits == []


def test_ndi_finder_is_closed_and_recreated(ndi, monkeypatch):
    finder = FakeFinder()
    monkeypatch.setattr(ndi.NDICapture, "finder", finder)
    ndi.NDICapture.close_finder()
    assert finder.closed and ndi.NDICapture.finder is None
    ndi.NDICapture.close_finder()  # again: no-op
    monkeypatch.setattr(ndi, "Finder", FakeFinder)
    assert isinstance(ndi.NDICapture.get_finder(), FakeFinder)


# --- Linux V4L2 enumeration -----------------------------------------------------------------


def test_linux_lists_capture_nodes_by_device_number():
    queried = {
        "/dev/video0": ("HD Webcam", True),
        "/dev/video1": ("HD Webcam", False),  # the webcam's metadata node
        "/dev/video2": ("USB Capture", True),
        "/dev/video10": None,  # no permission: sysfs fallback
        "/dev/video11": None,
    }
    sysfs = {
        (10, "name"): "Dummy video device",
        (10, "index"): "0",
        (11, "index"): "1",
    }
    cameras = get_camera_info_linux(
        device_paths=[
            "/dev/video10",
            "/dev/video2",
            "/dev/video1",
            "/dev/video0",
            "/dev/video11",
            "/dev/videoX",
        ],
        query_device=queried.get,
        read_sysfs=lambda number, attribute: sysfs.get((number, attribute)),
    )
    assert [(c.id, c.description, c.uuid) for c in cameras] == [
        (0, "HD Webcam (/dev/video0)", "/dev/video0"),
        (2, "USB Capture (/dev/video2)", "/dev/video2"),
        (10, "Dummy video device (/dev/video10)", "/dev/video10"),
    ]


def test_linux_unnamed_device_gets_a_generic_label():
    [camera] = get_camera_info_linux(
        device_paths=["/dev/video3"],
        query_device=lambda path: None,
        read_sysfs=lambda number, attribute: None,
    )
    assert (camera.id, camera.description) == (3, "Camera 3")


def _v4l2_capability(card, capabilities, device_caps):
    buffer = bytearray(104)
    buffer[0:6] = b"uvcvid"
    buffer[16 : 16 + len(card)] = card
    buffer[84:88] = capabilities.to_bytes(4, sys.byteorder)
    buffer[88:92] = device_caps.to_bytes(4, sys.byteorder)
    return buffer


@pytest.mark.parametrize(
    ("device_caps", "can_capture"),
    [
        (0x04200001, True),  # VIDEO_CAPTURE | EXT_PIX_FORMAT | STREAMING
        (0x04A00000, False),  # META_CAPTURE | EXT_PIX_FORMAT | STREAMING
    ],
)
def test_v4l2_querycap_is_parsed(monkeypatch, device_caps, can_capture):
    calls = []

    def ioctl(fd, request, buffer):
        calls.append(request)
        buffer[:] = _v4l2_capability(b"HD Webcam: HD Webcam", 0x84A00001, device_caps)
        return 0

    monkeypatch.setitem(sys.modules, "fcntl", types.SimpleNamespace(ioctl=ioctl))
    monkeypatch.setattr(os, "open", lambda *a: 3)
    monkeypatch.setattr(os, "close", lambda fd: None)
    assert query_v4l2_device("/dev/video0") == ("HD Webcam: HD Webcam", can_capture)
    assert calls == [0x80685600]


def test_v4l2_query_of_a_missing_device():
    assert query_v4l2_device("/dev/no-such-video-device") is None


# --- macOS screen capture --------------------------------------------------------------------


@pytest.fixture
def mac_capture(monkeypatch):
    """screen_capture_source_mac imported against a fake Quartz."""
    windows = [
        {
            "kCGWindowLayer": 0,
            "kCGWindowBounds": {"Width": 800, "Height": 600},
            "kCGWindowOwnerName": "OBS",
            "kCGWindowNumber": 42,
        }
    ]
    width, height = 4, 2
    quartz = types.ModuleType("Quartz")
    quartz.kCGWindowListOptionOnScreenOnly = 1
    quartz.kCGWindowListOptionIncludingWindow = 8
    quartz.kCGNullWindowID = 0
    quartz.kCGWindowImageDefault = 0
    quartz.kCGWindowImageBoundsIgnoreFraming = 1
    for key in (
        "kCGWindowLayer",
        "kCGWindowBounds",
        "kCGWindowOwnerName",
        "kCGWindowNumber",
    ):
        setattr(quartz, key, key)
    quartz.CGWindowListCopyWindowInfo = lambda option, window_id: windows
    core_graphics = types.ModuleType("Quartz.CoreGraphics")
    core_graphics.CGRectInfinite = "infinite"
    core_graphics.CGRectNull = "null"
    core_graphics.CGWindowListCreateImage = lambda rect, option, window, flags: "image"
    core_graphics.CGImageGetWidth = lambda image: width
    core_graphics.CGImageGetHeight = lambda image: height
    core_graphics.CGImageGetDataProvider = lambda image: "provider"
    core_graphics.CGDataProviderCopyData = lambda provider: bytes(
        range(width * height * 4)
    )
    quartz.CoreGraphics = core_graphics
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setitem(sys.modules, "Quartz.CoreGraphics", core_graphics)
    monkeypatch.delitem(sys.modules, "screen_capture_source_mac", raising=False)
    module = importlib.import_module("screen_capture_source_mac")
    yield module
    sys.modules.pop("screen_capture_source_mac", None)


@pytest.mark.parametrize("window_id", [-1, None])
def test_mac_whole_screen_capture_is_open(mac_capture, window_id):
    capture = mac_capture.ScreenCaptureMacOS(window_id)
    assert capture.isOpened()
    ok, image = capture.read()
    assert ok and image.shape == (2, 4, 3)


def test_mac_window_capture(mac_capture):
    assert mac_capture.ScreenCaptureMacOS.list_windows() == [("OBS", 42)]
    assert mac_capture.ScreenCaptureMacOS(42).isOpened()
    with pytest.raises(ValueError):
        mac_capture.ScreenCaptureMacOS(7)  # not on screen any more


def test_mac_rgba_conversion(mac_capture):
    _, image = mac_capture.ScreenCaptureMacOS(-1).read()
    rgba = np.arange(32, dtype=np.uint8).reshape(2, 4, 4)
    assert np.array_equal(image, rgba[:, :, :3])

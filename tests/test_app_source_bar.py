"""The viewer column's texts (src/source_bar.py) and the source's health
(status_bar.source_state)."""

import sys
import types

import pytest

import source_bar
from camera_info import CameraInfo
from status_bar import (
    SOURCE_ERROR,
    SOURCE_NONE,
    SOURCE_OPENING,
    SOURCE_RECONNECTING,
    SOURCE_RUNNING,
    SOURCE_SLOW,
    SOURCE_STALLED,
    StatusSnapshot,
    adjustments_text,
    source_look,
    source_state,
    source_text,
)


def source(kind, value, description=None):
    return CameraInfo(description or value, value, value, kind)


# --- what the source combo box shows -------------------------------------------------


def test_the_open_file_stream_or_screen_is_named():
    video = source(CameraInfo.CameraType.FILE, "C:\\Games\\final\\scoreboard.avi")
    assert source_bar.source_combo_text(video) == "Video file: scoreboard.avi"
    assert source_bar.source_full_name(video) == "C:\\Games\\final\\scoreboard.avi"

    stream = source(CameraInfo.CameraType.URL, "rtsp://admin:secret@10.0.1.200:554/11")
    # never the password
    assert source_bar.source_combo_text(stream) == "Stream: rtsp://10.0.1.200:554/11"
    assert "secret" not in source_bar.source_full_name(stream)

    screen = source(CameraInfo.CameraType.SCREEN_CAPTURE, 1234)
    assert source_bar.source_combo_text(screen, "OBS - Program") == (
        "Screen: OBS - Program"
    )
    entire = source(CameraInfo.CameraType.SCREEN_CAPTURE, source_bar.ENTIRE_SCREEN)
    assert source_bar.source_combo_text(entire) == "Screen: Entire screen"

    # a camera's item already shows its name
    camera = CameraInfo("Capture Card", "0", 0, CameraInfo.CameraType.OPENCV)
    assert source_bar.source_combo_text(camera) is None
    assert source_bar.source_full_name(camera) == "Capture Card"
    assert source_bar.source_combo_text(None) is None


def test_the_chooser_items_keep_their_order():
    # the indexes are those of mainwindow's SOURCE_INDEX_* constants
    assert source_bar.chooser_texts() == (
        "No source",
        "Video file…",
        "Stream URL (RTSP, HTTP)…",
        "Screen capture…",
    )


@pytest.mark.parametrize(
    "kind, value, headline",
    [
        (CameraInfo.CameraType.FILE, "/videos/game.mp4", "Can't play the video file"),
        (CameraInfo.CameraType.URL, "rtsp://u:p@cam/1", "Can't open the stream"),
        (CameraInfo.CameraType.SCREEN_CAPTURE, 7, "Can't capture"),
        (CameraInfo.CameraType.NDI, "NDI Scoreboard", "Can't receive the NDI"),
        (CameraInfo.CameraType.OPENCV, "Capture Card", "Can't open Capture Card"),
    ],
)
def test_a_source_error_says_what_failed_and_what_to_do(kind, value, headline):
    what, hint = source_bar.source_error_text(source(kind, value))
    assert what.startswith(headline)
    assert hint
    assert ":p@" not in what


def test_only_obs_virtual_camera_matches_that_device():
    from camera_info import is_obs_virtual_camera

    assert is_obs_virtual_camera("OBS Virtual Camera")
    assert is_obs_virtual_camera("OBS-Camera")
    assert is_obs_virtual_camera("Streamlabs Desktop Virtual Webcam")
    assert not is_obs_virtual_camera("vMix Video")
    assert not is_obs_virtual_camera("vMix Virtual Camera")
    assert not is_obs_virtual_camera("HD Pro Webcam")
    assert not is_obs_virtual_camera("Blackmagic WDM Capture")
    assert not is_obs_virtual_camera("Magewell USB Capture")
    assert not is_obs_virtual_camera("禾鸟四路")
    assert not is_obs_virtual_camera(None)


def test_obs_virtual_camera_error_says_to_start_it_and_refresh():
    what, hint = source_bar.source_error_text(
        source(CameraInfo.CameraType.OPENCV, "OBS Virtual Camera")
    )
    assert what.startswith("Can't show the OBS Virtual Camera")
    assert "Start Virtual Camera" in hint
    assert "refresh" in hint
    # vMix Video keeps the generic camera message
    vmix, _hint = source_bar.source_error_text(
        source(CameraInfo.CameraType.OPENCV, "vMix Video")
    )
    assert vmix.startswith("Can't open vMix Video")


def test_the_playback_speeds_cycle():
    speeds = [1]
    for _ in range(6):
        speeds.append(source_bar.next_speed(speeds[-1]))
    assert speeds == [1, 2, 4, 8, 16, 32, 1]
    assert source_bar.next_speed(3) == 1  # an unknown speed starts over
    assert source_bar.speed_text(4) == "×4"


def test_readouts():
    assert source_bar.zoom_text(1.0) == "100%"
    assert source_bar.zoom_text(0.587) == "59%"
    assert source_bar.rotate_text(270) == "Rotate 90° · 270°"
    assert source_bar.rotate_text(None) == "Rotate 90° · 0°"
    assert source_bar.QUICKSTART_URL in source_bar.quickstart_link()


def test_the_four_corner_banner_counts_and_says_what_esc_does():
    text = source_bar.four_corner_banner_text(2, has_correction=True)
    assert "2 of 4" in text
    assert "keeps the previous correction" in text
    assert "previous" not in source_bar.four_corner_banner_text(0, False)


# --- macOS screen recording permission ------------------------------------------------


def test_screen_recording_permission_only_on_macos(monkeypatch):
    monkeypatch.setattr(source_bar.platform, "system", lambda: "Windows")
    assert source_bar.screen_recording_allowed() is None


@pytest.mark.parametrize("allowed", [True, False])
def test_screen_recording_permission_is_asked_without_a_prompt(monkeypatch, allowed):
    monkeypatch.setattr(source_bar.platform, "system", lambda: "Darwin")
    quartz = types.ModuleType("Quartz")
    quartz.CGPreflightScreenCaptureAccess = lambda: allowed
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    assert source_bar.screen_recording_allowed() is allowed


def test_screen_recording_permission_unknown_on_old_macos(monkeypatch):
    monkeypatch.setattr(source_bar.platform, "system", lambda: "Darwin")
    monkeypatch.setitem(sys.modules, "Quartz", types.ModuleType("Quartz"))
    assert source_bar.screen_recording_allowed() is None


# --- the source's health ---------------------------------------------------------------------


def running(**overrides):
    values = dict(
        has_source=True,
        first_frame=True,
        now=100.0,
        last_frame_at=99.9,
        fps=29.0,
        nominal_fps=30.0,
        failed_reads=0,
    )
    values.update(overrides)
    return source_state(**values)


def test_source_states():
    assert source_state(has_source=False) == SOURCE_NONE
    assert running() == SOURCE_RUNNING
    assert running(error=True) == SOURCE_ERROR
    assert running(first_frame=False) == SOURCE_OPENING
    # below half the source's own frame rate
    assert running(fps=14.0) == SOURCE_SLOW
    assert running(fps=16.0) == SOURCE_RUNNING
    # a source that doesn't tell its frame rate is never "slow"
    assert running(fps=2.0, nominal_fps=None) == SOURCE_RUNNING
    assert running(failed_reads=3) == SOURCE_RECONNECTING
    assert running(failed_reads=1) == SOURCE_RUNNING  # a video file looping
    assert running(last_frame_at=90.0) == SOURCE_STALLED


def test_the_health_dot_tells_the_state_by_shape_and_colour():
    assert source_look(SOURCE_RUNNING) == ("disc", "new")
    for state in (SOURCE_OPENING, SOURCE_SLOW, SOURCE_RECONNECTING, SOURCE_STALLED):
        assert source_look(state) == ("triangle", "rejected")
    assert source_look(SOURCE_ERROR) == ("square", "empty")
    assert source_look(SOURCE_NONE) == ("ring", None)


def test_the_status_bar_says_slow_and_reconnecting():
    slow = StatusSnapshot(
        source_name="cam", source_state=SOURCE_SLOW, fps=11.04, nominal_fps=30.0
    )
    assert "slow: 11.0 of 30 fps" in source_text(slow)
    reconnecting = StatusSnapshot(source_name="cam", source_state=SOURCE_RECONNECTING)
    assert source_text(reconnecting) == "cam: reconnecting…"


def test_the_source_tooltip_lists_the_picture_adjustments():
    assert adjustments_text(StatusSnapshot()) == ""
    snapshot = StatusSnapshot(
        rotation=90, cropped=True, four_corner_corrected=True, stabilized=True
    )
    assert adjustments_text(snapshot) == (
        "Adjust picture: rotated 90°, cropped, 4-corner corrected, stabilized"
    )
    mirrored = StatusSnapshot(flip_horizontal=True, flip_vertical=True)
    assert adjustments_text(mirrored) == (
        "Adjust picture: flipped horizontally, flipped vertically"
    )


def test_a_failed_search_is_not_an_empty_camera_list():
    failed, cameras = source_bar.classify_discovery(None)
    assert failed == source_bar.ENUM_FAILED and cameras == []
    failed, cameras = source_bar.classify_discovery(RuntimeError("COM"))
    assert failed == source_bar.ENUM_FAILED and cameras == []
    failed, cameras = source_bar.classify_discovery(("failed", []))
    assert failed == source_bar.ENUM_FAILED

    empty, cameras = source_bar.classify_discovery([])
    assert empty == source_bar.ENUM_EMPTY and cameras == []
    empty, cameras = source_bar.classify_discovery(("ok", []))
    assert empty == source_bar.ENUM_EMPTY

    ok, cameras = source_bar.classify_discovery(
        ("ok", [source(CameraInfo.CameraType.OPENCV, 0, "Magewell")])
    )
    assert ok == source_bar.ENUM_OK and cameras[0].description == "Magewell"


def test_empty_and_failed_searches_use_different_words():
    failed_zh = source_bar.device_enum_notice_text(source_bar.ENUM_FAILED, chinese=True)
    empty_zh = source_bar.device_enum_notice_text(source_bar.ENUM_EMPTY, chinese=True)
    assert failed_zh.startswith("设备枚举失败，请检查驱动或重装便携包")
    assert "没有找到摄像头/采集卡" not in failed_zh
    assert empty_zh.startswith("没有找到摄像头/采集卡")
    assert "设备枚举失败" not in empty_zh
    assert "设备管理器" in empty_zh
    assert "OBS" in empty_zh and "vMix" in empty_zh and "F5" in empty_zh
    assert "视频捕获设备" in empty_zh and "截图" in empty_zh
    assert source_bar.device_enum_notice_text(source_bar.ENUM_OK, chinese=True) == ""
    assert (
        source_bar.device_enum_notice_text(source_bar.ENUM_PENDING, chinese=True) == ""
    )

    # a failure replaces "No source"; a successful empty search does not have to
    assert (
        source_text(
            StatusSnapshot(device_enum_headline="设备枚举失败，请检查驱动或重装便携包")
        )
        == "设备枚举失败，请检查驱动或重装便携包"
    )
    assert source_text(StatusSnapshot()) == "No source"


def test_capture_card_guide_names_the_common_cards():
    guide = source_bar.capture_card_guide(chinese=True)
    assert "不必四路同时预览" in guide
    assert "美乐威" in guide and "Magewell" in guide and "通道号" in guide
    assert "天创恒达" in guide and "免驱" in guide and "第三方软件驱动" in guide
    assert "Blackmagic" in guide and "UltraStudio" in guide
    assert "Desktop Video" in guide and "DirectShow" in guide
    assert "WDM Capture" in guide
    assert "禾鸟" in guide and "1 个设备" in guide
    assert "启动虚拟摄像机" in guide and "vMix Video" in guide
    assert source_bar.capture_card_guide(chinese=False) == ""
    html = source_bar.capture_card_guide_html()
    assert "<li>" in html and "禾鸟" in html


def test_directshow_names_for_support():
    assert source_bar.directshow_names_text([]) == (
        "没有找到摄像头/采集卡\n（刷新成功，DirectShow 列表是空的）"
    )
    assert source_bar.directshow_names_text(["A", "B"]) == "DirectShow 设备：\nA\nB"


@pytest.mark.qt
def test_the_wizard_explains_how_to_pick_a_capture_card(qapp):
    from PySide6.QtWidgets import QLabel

    from cn_first_run_wizard import CnFirstRunWizard

    wizard = CnFirstRunWizard()
    try:
        text = "\n".join(label.text() for label in wizard.page(0).findChildren(QLabel))
    finally:
        wizard.deleteLater()
    assert "vMix Video" in text
    assert "美乐威" in text and "Magewell" in text
    assert "天创恒达" in text and "第三方软件驱动" in text
    assert "Blackmagic" in text and "UltraStudio" in text and "WDM Capture" in text
    assert "禾鸟" in text and "不必四路同时预览" in text

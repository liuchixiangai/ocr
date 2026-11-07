"""Blank 24-second lamp publishes "24", and only that field, only when the
crop itself is dark for more than one sample.

A low-confidence failure, a model that is not ready, and a single LED-scan
frame stay on the old path. 0, 0.0, 4.9 and 14 are never replaced. The next
real digit comes back on the sample that reads it.
"""

import json
import os

import pytest

from shot_clock_blank import (
    FALLBACK_EXTRA,
    FALLBACK_TEXT,
    SHOT_CLOCK_BLANK_SETTING,
    apply_shot_clock_blank,
    plausibility_skip_names,
    setting_enabled,
)
from storage import fetch_data, store_data
from text_detection_target import TextDetectionResult, TextDetectionTargetWithResult

ResultState = TextDetectionTargetWithResult.ResultState


def _reading(text, state="Empty", *, display_blank=False, unchanged=False):
    return TextDetectionResult(
        text,
        ResultState[state],
        display_blank=display_blank,
        unchanged_image=unchanged,
    )


def _apply(target, result, enabled=True):
    return apply_shot_clock_blank(target, result, enabled)


def test_missing_setting_is_on_and_explicit_off_stays_off(data_dir):
    # Older configs have no key. That is the on position, not a silent off.
    from storage import get_storage_path

    path = get_storage_path("scoresight.json")
    assert str(data_dir) in path
    assert setting_enabled(
        fetch_data("scoresight.json", SHOT_CLOCK_BLANK_SETTING, None)
    )
    if os.path.exists(path):
        assert SHOT_CLOCK_BLANK_SETTING not in json.loads(
            open(path, encoding="utf-8").read()
        )
    store_data("scoresight.json", SHOT_CLOCK_BLANK_SETTING, False)
    saved = json.loads(open(path, encoding="utf-8").read())
    assert saved[SHOT_CLOCK_BLANK_SETTING] is False
    assert (
        setting_enabled(fetch_data("scoresight.json", SHOT_CLOCK_BLANK_SETTING, None))
        is False
    )
    store_data("scoresight.json", SHOT_CLOCK_BLANK_SETTING, True)
    assert (
        setting_enabled(fetch_data("scoresight.json", SHOT_CLOCK_BLANK_SETTING, None))
        is True
    )
    for off in ("false", "off", "no", "0", "否", False, 0):
        assert setting_enabled(off) is False
    for on in (None, True, 1, "true", "yes", ""):
        assert setting_enabled(on) is True


def test_one_dark_frame_does_not_replace_a_live_countdown(make_target):
    shot = make_target("24秒")
    assert _apply(shot, _reading("4.9", "Success")) is None
    # LED scan: one dark sample while 4.9 is on the lamp.
    assert _apply(shot, _reading("", display_blank=True)) is None
    assert shot.last_text is None  # the publisher still owns 4.9
    assert shot.shot_clock_blank_streak == 1
    # the digit is back on the next sample
    assert _apply(shot, _reading("4.8", "Success")) is None
    assert shot.shot_clock_blank_streak == 0


@pytest.mark.parametrize("text", ["0", "0.0", "0.5", "4.9", "14", "24"])
def test_real_shot_clock_values_are_not_replaced(make_target, text):
    shot = make_target("24秒")
    shot.last_text = "9"
    assert _apply(shot, _reading(text, "Success")) is None
    assert shot.last_text == "9"
    assert shot.shot_clock_blank_streak == 0


def test_two_dark_samples_publish_24_then_a_digit_comes_back(make_target):
    shot = make_target("24秒")
    shot.last_text = "4.9"
    shot.last_image = object()
    shot.ocrResultPerCharacterSmoother.get_smoothed_result("4.9")
    shot.ocrResultPerCharacterSmoother.get_smoothed_result("4.8")
    shot.led_value_stabilizer.stabilize("4.9", 5000)
    assert _apply(shot, _reading("", display_blank=True)) is None
    published = _apply(shot, _reading("", display_blank=True))
    assert published == (FALLBACK_TEXT, ResultState.Success)
    assert published[0] == "24"
    assert isinstance(published[0], str)
    assert shot.last_text == "24"
    assert shot.last_image is None
    assert not shot.ocrResultPerCharacterSmoother.history
    # the next real digit is not debounced against the pre-blank second
    assert shot.led_value_stabilizer.stabilize("14", 5000) == ("14", True)
    assert _apply(shot, _reading("", display_blank=True)) == (
        "24",
        ResultState.SameNoChange,
    )
    assert _apply(shot, _reading("14", "Success")) is None
    assert shot.shot_clock_blank_streak == 0


def test_low_confidence_and_other_empties_do_not_become_24(make_target):
    shot = make_target("24秒")
    shot.last_text = "14"
    # rejected read (confidence / format), not a dark crop
    assert _apply(shot, _reading("8", "FailedFilter")) is None
    assert shot.shot_clock_blank_streak == 0
    # one dark frame after the failure is still only a flicker
    assert _apply(shot, _reading("", display_blank=True)) is None
    assert shot.last_text == "14"
    # model not ready, box off the frame, OCR exception: Empty without the flag
    assert _apply(shot, _reading("", "Empty")) is None
    assert _apply(shot, _reading("", "Empty")) is None
    assert shot.shot_clock_blank_streak == 0
    # skip-similar still shows the last digits
    assert _apply(shot, _reading("", "SameNoChange", unchanged=True)) is None
    assert shot.shot_clock_blank_streak == 0


def test_switch_off_keeps_the_old_blank_path(make_target):
    shot = make_target("24秒")
    shot.last_text = "4.9"
    for _ in range(4):
        assert _apply(shot, _reading("", display_blank=True), enabled=False) is None
    assert shot.last_text == "4.9"
    assert shot.shot_clock_blank_streak == 0
    # turning it back on starts the count over; one frame is still not 24
    assert _apply(shot, _reading("", display_blank=True), enabled=True) is None
    assert _apply(shot, _reading("", display_blank=True), enabled=True)[0] == "24"


@pytest.mark.parametrize("name", ["比赛时间", "总计", "主队得分", "客队犯规", "节次"])
def test_other_fields_ignore_a_dark_crop(make_target, name):
    box = make_target(name)
    box.last_text = "8"
    for _ in range(3):
        assert _apply(box, _reading("", display_blank=True)) is None
    assert box.last_text == "8"
    assert box.shot_clock_blank_streak == 0


@pytest.mark.parametrize("name", ["进攻24秒", "Shot Clock", "shot clock"])
def test_aliases_of_the_shot_clock_get_the_fallback(make_target, name):
    box = make_target(name)
    assert _apply(box, _reading("", display_blank=True)) is None
    assert _apply(box, _reading("", display_blank=True)) == ("24", ResultState.Success)


def test_fallback_is_exempt_from_a_plausibility_hold(make_result):
    from plausibility_holds import PlausibilityHolds

    holds = PlausibilityHolds()
    holds.apply([make_result("24秒", "4.9")], {})
    fallback = make_result("24秒", "24")
    fallback.extras = {FALLBACK_EXTRA: True}
    plain = make_result("24秒", "24")
    assert plausibility_skip_names([fallback, plain]) == {"24秒"}
    assert plausibility_skip_names([plain]) == set()
    reasons = {"24秒": "the shot clock jumping from 4.9 to 24"}
    skip = plausibility_skip_names([fallback])
    reasons = {name: why for name, why in reasons.items() if name not in skip}
    out = holds.apply([fallback], reasons)
    assert [(r.result, r.result_state.name) for r in out] == [("24", "Success")]


def test_list_keeps_4_9_on_a_flicker_and_shows_24_when_blank(qapp):
    from PySide6.QtWidgets import QTableWidgetItem

    from readings_view import record_reading

    item = QTableWidgetItem("4.9")
    record_reading(item, ResultState.Empty, "")
    assert item.text() == "4.9"
    record_reading(item, ResultState.Success, "24")
    assert item.text() == "24"
    record_reading(item, ResultState.FailedFilter, "8")
    assert item.text() == "24"
    record_reading(item, ResultState.Success, "14")
    assert item.text() == "14"


# --- the capture thread, JSON and vMix ----------------------------------------


class _Board:
    def __init__(self, frames):
        self.frames = list(frames)

    def detect_multi_text(self, binary, gray, rects, display=None, color=None):
        frame = self.frames.pop(0)
        results = []
        for rect in rects:
            spec = frame[rect.name]
            if spec == "unchanged":
                results.append(
                    TextDetectionResult(
                        "", ResultState.SameNoChange, unchanged_image=True
                    )
                )
            elif spec == "display_blank":
                results.append(
                    TextDetectionResult("", ResultState.Empty, display_blank=True)
                )
            else:
                text, state = spec
                results.append(TextDetectionResult(text, ResultState[state]))
        return results


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def capture(memory_storage):
    pytest.importorskip("tesserocr")
    import numpy as np

    from camera_info import CameraInfo
    from camera_thread import TimerThread

    thread = TimerThread(
        CameraInfo("test", "test", "x", CameraInfo.CameraType.FILE), memory_storage
    )
    thread._clock = _Clock()
    thread.update_frame_interval = 0
    thread.results = []
    thread.ocr_result_signal.connect(thread.results.append)
    thread._frame = np.zeros((48, 64, 3), np.uint8)

    def run(boxes, frames, step=1.0):
        thread.results.clear()
        thread.textDetector = _Board(frames)
        storage = thread.detectionTargetsStorage
        storage.clear()
        for box in boxes:
            storage.add_item(box)
        for _ in frames:
            thread._clock.now += step
            thread.process_frame(thread._frame)
        return thread.results

    yield thread, run
    thread.cleanup()


def _cell(cycle, name):
    found = [r for r in cycle if r.name == name]
    assert len(found) == 1
    result = found[0]
    return (
        result.result,
        result.result_state.name,
        bool((result.extras or {}).get(FALLBACK_EXTRA)),
    )


def test_pipeline_blank_then_24_then_the_digit_and_other_fields(capture, make_target):
    thread, run = capture
    assert thread.shotClockBlankAs24 is True
    boxes = [
        make_target("24秒"),
        make_target("比赛时间"),
        make_target("主队得分"),
        make_target("总计"),
    ]
    frames = [
        {
            "24秒": ("4.9", "Success"),
            "比赛时间": ("8:01", "Success"),
            "主队得分": ("10", "Success"),
            "总计": ("8:01", "Success"),
        },
        {
            "24秒": "display_blank",
            "比赛时间": ("8:00", "Success"),
            "主队得分": ("10", "Success"),
            "总计": ("8:00", "Success"),
        },
        {
            "24秒": "display_blank",
            "比赛时间": ("7:59", "Success"),
            "主队得分": ("12", "Success"),
            "总计": ("7:59", "Success"),
        },
        {
            "24秒": ("14", "Success"),
            "比赛时间": ("7:58", "Success"),
            "主队得分": ("12", "Success"),
            "总计": ("7:58", "Success"),
        },
    ]
    cycles = run(boxes, frames, step=0.05)
    assert _cell(cycles[0], "24秒") == ("4.9", "Success", False)
    # one dark OCR sample: 4.9 stays, and the game clock is not held back a sample
    assert _cell(cycles[1], "24秒") == ("", "Empty", False)
    assert _cell(cycles[1], "比赛时间") == ("8:00", "Success", False)
    assert _cell(cycles[1], "主队得分") == ("10", "SameNoChange", False)
    assert _cell(cycles[1], "总计") == ("8:00", "Success", False)
    assert _cell(cycles[2], "24秒") == ("24", "Success", True)
    assert _cell(cycles[2], "比赛时间") == ("7:59", "Success", False)
    assert _cell(cycles[2], "主队得分") == ("12", "Success", False)
    assert _cell(cycles[2], "总计") == ("7:59", "Success", False)
    # the digit replaces 24 on that same sample
    assert _cell(cycles[3], "24秒") == ("14", "Success", False)
    assert _cell(cycles[3], "比赛时间") == ("7:58", "Success", False)


def test_zero_tenth_and_fourteen_survive_a_single_dark_frame(capture, make_target):
    _, run = capture
    cycles = run(
        [make_target("24秒")],
        [
            {"24秒": ("0", "Success")},
            {"24秒": ("0.0", "Success")},
            {"24秒": ("14", "Success")},
            {"24秒": "display_blank"},
            {"24秒": ("0", "Success")},
        ],
    )
    assert [_cell(cycle, "24秒")[0:2] for cycle in cycles] == [
        ("0", "Success"),
        ("0.0", "Success"),
        ("14", "Success"),
        ("", "Empty"),
        ("0", "Success"),
    ]


def test_when_blank_does_not_replace_the_fallback_with_empty(capture, make_target):
    _, run = capture
    cycles = run(
        [make_target("24秒", clear_value="blank")],
        [{"24秒": ("1", "Success")}] + [{"24秒": "display_blank"}] * 4,
    )
    assert [_cell(cycle, "24秒")[0:2] for cycle in cycles] == [
        ("1", "Success"),
        ("", "Empty"),
        ("24", "Success"),
        ("24", "SameNoChange"),
        ("24", "SameNoChange"),
    ]


def test_switch_off_does_not_publish_24(capture, make_target):
    thread, run = capture
    thread.shotClockBlankAs24 = False
    cycles = run(
        [make_target("24秒", clear_value="blank")],
        [
            {"24秒": ("4.9", "Success")},
            {"24秒": "display_blank"},
            {"24秒": "display_blank"},
            {"24秒": "display_blank"},
        ],
    )
    assert [_cell(cycle, "24秒")[0:2] for cycle in cycles] == [
        ("4.9", "Success"),
        ("", "Empty"),
        ("", "Empty"),
        ("", "Success"),  # "When blank" still clears after 2 seconds
    ]


def test_failed_read_never_publishes_24(capture, make_target):
    _, run = capture
    cycles = run(
        [make_target("24秒")],
        [
            {"24秒": ("4.9", "Success")},
            {"24秒": ("8", "FailedFilter")},
            {"24秒": ("", "Empty")},
            {"24秒": ("", "Empty")},
            {"24秒": "display_blank"},
        ],
    )
    assert [_cell(cycle, "24秒")[0] for cycle in cycles] == ["4.9", "8", "", "", ""]
    assert all(not _cell(cycle, "24秒")[2] for cycle in cycles)


def test_json_http_and_vmix_send_the_same_24(capture, make_target, tmp_path):
    _, run = capture
    boxes = [make_target("24秒"), make_target("主队得分"), make_target("比赛时间")]
    frames = [
        {
            "24秒": ("4.9", "Success"),
            "主队得分": ("88", "Success"),
            "比赛时间": ("0:04.9", "Success"),
        },
        {
            "24秒": "display_blank",
            "主队得分": ("88", "Success"),
            "比赛时间": ("0:04.8", "Success"),
        },
        {
            "24秒": "display_blank",
            "主队得分": ("88", "Success"),
            "比赛时间": ("0:04.7", "Success"),
        },
    ]
    cycles = run(boxes, frames)
    confirming = cycles[2]
    assert _cell(confirming, "24秒") == ("24", "Success", True)

    from file_output import JSON_FILE_NAME, FileOutput, json_row_from_values

    files = FileOutput()
    files.record(confirming)
    folder = tmp_path / "out"
    folder.mkdir()
    files.flush(str(folder), save_json=True)
    written = json.loads((folder / JSON_FILE_NAME).read_text(encoding="utf-8"))
    assert written == [{"shot_clock": "24", "home_score": "88", "game_clock": "0:04.7"}]
    assert (
        json_row_from_values(
            [(r.name, r.result) for r in confirming if r.result_state.name == "Success"]
        )["shot_clock"]
        == "24"
    )

    import http_server

    http_server.update_http_server(confirming)
    try:
        assert http_server._pivot_values(None) == {
            "24秒": "24",
            "主队得分": "88",
            "比赛时间": "0:04.7",
        }
        # "?all" is the last good value of every box, the row vMix JSON follows
        assert http_server._pivot_values("1")["24秒"] == "24"
    finally:
        http_server.update_http_server([])

    from api_output import good_values

    assert good_values(confirming)["24秒"] == "24"
    assert good_values(confirming)["主队得分"] == "88"

    from vmix_output import VMixAPI

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            return type("R", (), {"status_code": 200, "text": "ok", "headers": {}})()

    api = VMixAPI(
        "127.0.0.1",
        "8088",
        "1",
        {
            "24秒": "ShotClock.Text",
            "主队得分": "HomeScore.Text",
            "比赛时间": "Clock.Text",
        },
    )
    api._session = Session()
    try:
        api.start()
        for cycle in cycles:
            api.update_vmix(cycle)
        assert api._worker.wait_idle(10)
    finally:
        api.close()
    from urllib.parse import parse_qs, urlsplit

    sent = []
    for url in api._session.calls:
        query = parse_qs(urlsplit(url).query)
        sent.append((query["SelectedName"][0], query["Value"][0]))
    by_field = {}
    for name, value in sent:
        by_field.setdefault(name, []).append(value)
    assert by_field["ShotClock.Text"] == ["4.9", "24"]
    assert by_field["HomeScore.Text"] == ["88"]
    assert by_field["Clock.Text"][-1] == "0:04.7"
    assert "24" not in by_field["HomeScore.Text"]
    assert "24" not in by_field["Clock.Text"]


def test_the_thread_follows_the_stored_switch(memory_storage):
    pytest.importorskip("tesserocr")
    from camera_info import CameraInfo
    from camera_thread import TimerThread

    store_data("scoresight.json", SHOT_CLOCK_BLANK_SETTING, False)
    thread = TimerThread(
        CameraInfo("test", "test", "x", CameraInfo.CameraType.FILE), memory_storage
    )
    try:
        assert thread.shotClockBlankAs24 is False
        store_data("scoresight.json", SHOT_CLOCK_BLANK_SETTING, True)
        assert thread.shotClockBlankAs24 is True
    finally:
        thread.cleanup()


def test_a_dark_crop_is_display_blank_and_a_failed_read_is_not(make_target):
    pytest.importorskip("tesserocr")
    import numpy as np

    from tesseract import TextDetector

    detector = TextDetector(TextDetector.OcrModelIndex.SCOREBOARD_GENERAL)
    shot = make_target("24秒")
    flat = np.zeros((24, 48), np.uint8)
    blank = detector._recognize(shot, shot.settings, flat, 0, 0)
    assert blank.state == ResultState.Empty
    assert blank.display_blank is True

    lit = flat.copy()
    lit[4:20, 8:40] = 255
    read = detector._recognize(shot, shot.settings, lit, 0, 0)
    assert read.display_blank is False

    rejected = detector._finish_reading(
        shot, {**shot.settings, "conf_thresh": 0.9}, "4.9", 10.0
    )
    assert rejected.state == ResultState.FailedFilter
    assert rejected.text == "4.9"
    assert rejected.display_blank is False

    gray = np.zeros((40, 80), np.uint8)
    color = np.zeros((40, 80, 3), np.uint8)
    dark = detector._detect_ppocr(
        None, None, False, [shot], gray, color, lambda: gray, 80, 40, None
    )
    assert dark[0].display_blank is True
    contrasted = gray.copy()
    contrasted[0:20, 0:30] = 255
    not_ready = detector._detect_ppocr(
        None, None, False, [shot], contrasted, color, lambda: contrasted, 80, 40, None
    )
    # the model is not ready: empty, but the crop is not a dark lamp
    assert not_ready[0].state == ResultState.Empty
    assert not_ready[0].display_blank is False

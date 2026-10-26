"""OCRResultPerCharacterSmoother (src/text_detection_target.py)."""

import pytest

from text_detection_target import OCRResultPerCharacterSmoother, TextDetectionTarget


def smooth(values, max_history=5):
    smoother = OCRResultPerCharacterSmoother(max_history)
    return [smoother.get_smoothed_result(value) for value in values]


@pytest.mark.parametrize(
    ("values", "possible"),
    [
        (["12"] * 5 + ["9"] * 5, {"12", "9"}),  # 12 -> 9: never "92"
        (["10"] * 5 + ["9"] * 5, {"10", "9"}),  # shot clock 10 -> 9: never "90"
        (["9"] * 5 + ["10"] * 5, {"9", "10"}),  # score 9 -> 10: never "1"
    ],
)
def test_digit_count_changes_never_produce_mixed_values(values, possible):
    outputs = smooth(values)
    assert set(outputs) <= possible
    assert outputs[-1] == values[-1]


def test_switches_once_the_new_value_is_the_majority():
    assert smooth(["12"] * 5 + ["13"] * 3) == ["12"] * 5 + ["12", "12", "13"]


def test_a_failed_read_is_not_remembered():
    smoother = OCRResultPerCharacterSmoother()
    outputs = [smoother.get_smoothed_result(v) for v in ["15", "15", "", "15", "15"]]
    outputs += [smoother.get_smoothed_result("15") for _ in range(5)]
    assert outputs[2] == "15"
    assert "" not in outputs
    assert "" not in smoother.history


def test_ties_go_to_the_most_recent_value():
    assert smooth(["3", "4"]) == ["3", "4"]
    assert smooth(["3", "4", "4", "3"]) == ["3", "4", "4", "3"]


def test_empty_history_returns_the_input():
    smoother = OCRResultPerCharacterSmoother()
    assert smoother.get_smoothed_result("") == ""
    smoother.get_smoothed_result("7")
    smoother.clear()
    assert smoother.get_smoothed_result("") == ""


def test_targets_do_not_share_mutable_defaults():
    a = TextDetectionTarget(0, 0, 1, 1, "a")
    b = TextDetectionTarget(0, 0, 1, 1, "b")
    a.settings["x"] = 1
    a.mini_rects.append(None)
    assert b.settings == {} and b.mini_rects == []

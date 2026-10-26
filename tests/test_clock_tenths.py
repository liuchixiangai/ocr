"""Last-seconds tenths on the game clock and the 24-second shot clock.

Boards switch from whole seconds to one decimal place (4.9, 0:04.9). The dot
is sometimes read as a comma or a colon. Those readings have to be published,
including the step from an integer to the first tenth, and scores stay integers.
"""

import re

import pytest

import sports
from cn_reading import prepare_reading, soft_accept_reading, whitelist_for_box
from defaults import (
    default_info_for_box_name,
    default_led_stable_ms,
    default_led_stack_frames,
    force_clock_zero_latency,
    normalize_settings_dict,
)
from file_output import json_field_key, json_row_from_values
from game_state import GameEngine
from plausibility_holds import PlausibilityHolds

# The digits-only Shotclock 0-39 preset saved before tenths were allowed.
OLD_SHOT_CLOCK = r"^[0-3]?\d$"


def _settings(name, **overrides):
    return normalize_settings_dict(overrides, default_info_for_box_name(name))


def publish(name, raw, **overrides):
    """What the OCR post-filter would send, or None when the reading is rejected."""
    settings = _settings(name, **overrides)
    text = prepare_reading(name, raw, settings)
    regex = settings.get("format_regex")
    if regex and re.fullmatch(regex, text):
        return text
    if soft_accept_reading(name, text, settings):
        return text
    return None


@pytest.mark.parametrize(
    ("raw", "published"),
    [
        ("24", "24"),
        ("12", "12"),
        ("6", "6"),
        ("08", "08"),
        ("4.9", "4.9"),
        ("0.5", "0.5"),
        ("4,9", "4.9"),
        ("0,5", "0.5"),
        ("4:9", "4.9"),
        ("0:5", "0.5"),
        ("4.95", "4.9"),
        ("0.50", "0.5"),
        ("４．９", "4.9"),
        ("04.9", "4.9"),
        ("0:04.9", "4.9"),
        ("0:04:9", "4.9"),
        ("40", None),
        ("49", None),
        ("4.99", "4.9"),
    ],
)
def test_shot_clock_publishes_whole_seconds_and_one_tenth(raw, published):
    assert publish("24秒", raw) == published
    # Names that are not the preset box keep their own integer format (often
    # "any number", which still accepts 40). A tenth is not in that format,
    # and still has to be published.
    if published is not None:
        assert publish("Shot Clock", raw) == published
        assert publish("进攻24秒", raw) == published


def test_shot_clock_tenths_pass_a_saved_digits_only_preset():
    # Existing setups still store ^[0-3]?\d$. Loading widens that preset, and
    # a reading is accepted even before the saved expression is rewritten.
    raw_settings = {"type": 0, "format_regex": OLD_SHOT_CLOCK}
    text = prepare_reading("24秒", "4,9", raw_settings)
    assert text == "4.9"
    assert re.fullmatch(OLD_SHOT_CLOCK, text) is None
    assert soft_accept_reading("24秒", text, raw_settings)
    assert soft_accept_reading("主队得分", "4.9", {"type": 0}) is False
    upgraded = _settings("24秒", format_regex=OLD_SHOT_CLOCK)
    assert upgraded["format_regex"] != OLD_SHOT_CLOCK
    assert re.fullmatch(upgraded["format_regex"], "4.9")
    assert re.fullmatch(upgraded["format_regex"], "12")
    score = _settings("主队得分", format_regex=r"^1?\d{1,2}$")
    assert score["format_regex"] == r"^1?\d{1,2}$"
    assert publish("24秒", "12", format_regex=OLD_SHOT_CLOCK) == "12"
    assert publish("24秒", "4.9", format_regex=OLD_SHOT_CLOCK) == "4.9"
    assert publish("24秒", "0.5", format_regex=OLD_SHOT_CLOCK) == "0.5"
    assert publish("24秒", "40", format_regex=OLD_SHOT_CLOCK) is None
    # A game clock saved as mm:ss (no tenth) still publishes 0:04.9.
    old_clock = r"^(?:(?:[0-5]?\d:[0-5]\d)|(?:[0-5]?\d\.\d))$"
    assert publish("比赛时间", "0:04.9", format_regex=old_clock) == "0:04.9"
    assert publish("比赛时间", "12:00", format_regex=old_clock) == "12:00"


@pytest.mark.parametrize(
    ("raw", "published"),
    [
        ("12:00", "12:00"),
        ("1:16.7", "1:16.7"),
        ("01:16.7", "01:16.7"),
        ("0:06", "0:06"),
        ("0:05.9", "0:05.9"),
        ("0:04.9", "0:04.9"),
        ("0:4.9", "0:04.9"),
        ("0:04,9", "0:04.9"),
        ("0:04:9", "0:04.9"),
        ("4.9", "4.9"),
        ("4,9", "4.9"),
        ("4:9", "4.9"),
        ("0.5", "0.5"),
        ("0:5", "0.5"),
        ("4.95", "4.9"),
        ("0:04.95", "0:04.9"),
        ("1:16.76", "1:16.7"),
        # two decimals without a colon can be a missed colon (12:34); leave it out
        ("12.34", None),
        ("1:02:03", None),
    ],
)
def test_game_clock_publishes_tenths_without_flooring(raw, published):
    assert publish("比赛时间", raw) == published
    assert publish("Time", raw) == published
    assert publish("总计", raw) == published


def test_integer_to_tenth_stays_a_tenth():
    shot = [publish("24秒", text) for text in ("6", "5.9", "5.8", "5.0", "4.9", "0.5")]
    assert shot == ["6", "5.9", "5.8", "5.0", "4.9", "0.5"]
    game = [
        publish("比赛时间", text)
        for text in ("0:06", "0:05.9", "0:05.8", "4.9", "4.8", "0.5")
    ]
    assert game == ["0:06", "0:05.9", "0:05.8", "4.9", "4.8", "0.5"]


def test_scores_and_fouls_stay_integers():
    assert publish("主队得分", "52") == "52"
    assert publish("主队得分", "4.9") is None
    assert publish("主队得分", "4,9") is None
    assert publish("客队犯规", "3") == "3"
    assert publish("客队犯规", "3.2") is None
    assert publish("节次", "4") == "4"
    assert "." not in whitelist_for_box("主队得分", {"type": 0}, "0123456789")
    assert "." not in whitelist_for_box("客队犯规", {"type": 0}, "0123456789")


def test_clock_whitelist_includes_the_decimal_point():
    digits = "0123456789"
    shot = whitelist_for_box("24秒", {"type": 0}, digits)
    game = whitelist_for_box("比赛时间", {"type": 1}, digits + ":.")
    assert "." in shot and "," in shot and ":" in shot
    assert "." in game and "," in game


def test_clocks_stay_zero_latency_scores_do_not():
    shot = _settings("24秒")
    game = _settings("比赛时间")
    score = _settings("主队得分")
    assert (shot["led_stack_frames"], shot["led_stable_ms"]) == (0, 0)
    assert (game["led_stack_frames"], game["led_stable_ms"]) == (0, 0)
    assert (score["led_stack_frames"], score["led_stable_ms"]) == (4, 200)
    assert default_led_stack_frames("进攻24秒") == 0
    assert default_led_stable_ms("总计") == 0
    assert default_led_stack_frames("主队得分") == 4
    held = {"led_stack_frames": 4, "led_stable_ms": 200, "type": 0}
    assert force_clock_zero_latency(held, "24秒", 0) is True
    assert (held["led_stack_frames"], held["led_stable_ms"]) == (0, 0)
    fouls = {"led_stack_frames": 4, "led_stable_ms": 200, "type": 0}
    assert force_clock_zero_latency(fouls, "主队犯规", 0) is False
    assert (fouls["led_stack_frames"], fouls["led_stable_ms"]) == (4, 200)


def test_outbound_keys_stay_english_and_keep_the_tenth():
    assert json_field_key("24秒") == "shot_clock"
    assert json_field_key("Shot Clock") == "shot_clock"
    assert json_field_key("比赛时间") == "game_clock"
    assert json_field_key("总计") == "game_clock"
    assert json_field_key("Time") == "game_clock"
    assert sports.role_of("总计") == sports.CLOCK
    assert sports.role_of("24秒") == sports.SHOT_CLOCK
    row = json_row_from_values(
        [("比赛时间", "0:04.9"), ("24秒", "4.9"), ("主队得分", "88")]
    )
    assert row == {"game_clock": "0:04.9", "shot_clock": "4.9", "home_score": "88"}


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_tenths_are_not_held_back(make_result):
    """Integer → tenth, and 4.9 → 4.8, go out at once. A score jump still waits."""
    clock = _Clock()
    engine = GameEngine(sports.BASKETBALL, clock=clock)
    holds = PlausibilityHolds()

    def cycle(**values):
        clock.now += 0.1
        results = []
        for key, raw in values.items():
            name = key.replace("_", " ")
            text = publish(name, raw)
            assert text is not None, raw
            results.append(make_result(name, text))
        reasons = engine.held_back(results)
        sent = holds.apply(results, reasons)
        engine.update(results)
        return reasons, [(item.name, item.result) for item in sent]

    assert cycle(Time="0:06")[0] == {}
    reasons, sent = cycle(Time="0:05.9")
    assert reasons == {}
    assert sent == [("Time", "0:05.9")]
    assert engine.state().clock == "0:05.9"
    # Each step is a tenth or the integer→tenth switch, never a multi-second jump.
    for raw in ("0:05.8", "0:05.0", "4.9", "4.8"):
        reasons, sent = cycle(Time=raw)
        assert reasons == {}
        assert sent == [("Time", publish("Time", raw))]
    assert engine.state().clock == "4.8"

    reasons, sent = cycle(Shot_Clock="6")
    assert reasons == {} and sent == [("Shot Clock", "6")]
    for raw in ("5.9", "5,8", "4:9", "4.8", "0.5"):
        reasons, sent = cycle(Shot_Clock=raw)
        assert reasons == {}
        assert sent == [("Shot Clock", publish("Shot Clock", raw))]
    assert engine.state().shot_clock == "0.5"

    # Chinese names take the same path. 4.8 → 0:04.9 is the same tenth, not a jump.
    reasons, sent = cycle(**{"比赛时间": "0:04.9", "24秒": "3.8"})
    assert reasons == {}
    assert ("比赛时间", "0:04.9") in sent
    assert ("24秒", "3.8") in sent
    assert engine.state().clock == "0:04.9"
    assert engine.state().shot_clock == "3.8"

    cycle(Home_Score="21")
    cycle(Home_Score="21")
    reasons, sent = cycle(Home_Score="81")
    assert "Home Score" in reasons
    assert sent == [("Home Score", "21")]

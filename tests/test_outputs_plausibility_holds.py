"""Plausibility holds (src/plausibility_holds.py): what the outputs and the
template fields get while a change the sport doesn't allow is held back, and
after; with the game engine deciding (game_state.GameEngine.held_back), as
MainWindow.ocrResult runs them."""

import logging

import pytest

import sports
from game_state import GameEngine
from plausibility_holds import PlausibilityHolds
from template_fields import TemplateFieldEvaluator


def sent(results):
    return [(r.name, r.result, r.result_state.name) for r in results]


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def pipeline(make_result):
    """pipeline(**values) -> what the outputs get for one cycle of readings
    ({box name: text or (text, state)}, "_" for spaces), 0.5 s after the
    last."""
    clock = Clock()
    engine = GameEngine(sports.BASKETBALL, clock=clock)
    holds = PlausibilityHolds()

    def run(step=0.5, **values):
        clock.now += step
        results = []
        for key, value in values.items():
            text, state = value if isinstance(value, tuple) else (value, "Success")
            results.append(make_result(key.replace("_", " "), text, state))
        out = holds.apply(results, engine.held_back(results))
        engine.update(results)
        return out

    run.holds = holds
    run.engine = engine
    return run


def test_without_holds_the_readings_go_out_as_they_are(make_result):
    holds = PlausibilityHolds()
    results = [make_result("Home Score", "12"), make_result("Sponsor", "ACME")]
    out = holds.apply(results, {})
    assert all(a is b for a, b in zip(out, results))


def test_a_flicker_never_goes_out(pipeline):
    assert sent(pipeline(Home_Score="21")) == [("Home Score", "21", "Success")]
    pipeline(Home_Score=("21", "SameNoChange"))
    # 21 -> 81 for one cycle: the outputs keep 21 (unchanged for them)
    assert sent(pipeline(Home_Score="81")) == [("Home Score", "21", "SameNoChange")]
    assert "Home Score" in pipeline.holds
    assert pipeline.holds.held() == {"Home Score": "21"}
    # back to 21: sent as new once (harmless: the outputs had 21)
    assert sent(pipeline(Home_Score="21")) == [("Home Score", "21", "Success")]
    assert "Home Score" not in pipeline.holds


def test_a_change_the_sport_allows_goes_out_at_once(pipeline):
    pipeline(Home_Score="21")
    pipeline(Home_Score="21")
    assert sent(pipeline(Home_Score="24")) == [("Home Score", "24", "Success")]
    # a flicker after a change sent before the game counted it: the outputs
    # keep what they got
    assert sent(pipeline(Home_Score="84")) == [("Home Score", "24", "SameNoChange")]


def test_a_correction_goes_out_once_read_steadily(pipeline):
    pipeline(Home_Score="21")
    pipeline(Home_Score="21")
    # the scorer's table fixes 21 to 18: held back for 3 s...
    for _ in range(6):
        out = pipeline(Home_Score="18")
        assert sent(out) == [("Home Score", "21", "SameNoChange")]
    # ...then sent as new, even when the OCR reports it unchanged
    out = pipeline(Home_Score=("18", "SameNoChange"))
    assert sent(out) == [("Home Score", "18", "Success")]
    assert sent(pipeline(Home_Score=("18", "SameNoChange"))) == [
        ("Home Score", "18", "SameNoChange")
    ]


def test_rejected_readings_pass_and_keep_the_hold(pipeline):
    pipeline(Home_Score="21")
    pipeline(Home_Score="21")
    pipeline(Home_Score="81")
    assert sent(pipeline(Home_Score=("8l", "FailedFilter"))) == [
        ("Home Score", "8l", "FailedFilter")
    ]
    assert "Home Score" in pipeline.holds


def test_templates_are_built_from_what_is_sent(pipeline, make_result):
    templates = TemplateFieldEvaluator()

    def cycle(home):
        template = make_result(
            "Score",
            "",
            "Empty",
            templatefield=True,
            templatefield_text="{{Home Score}}-{{Away Score}}",
        )
        out = pipeline(Home_Score=home, Away_Score="30")
        out.append(template)
        templates.apply(out)
        return template.result

    cycle("21")
    assert cycle("21") == "21-30"
    assert cycle("81") == "21-30"
    assert cycle("22") == "22-30"


def test_a_field_without_a_value_sent_is_not_held(make_result):
    holds = PlausibilityHolds()
    out = holds.apply([make_result("Home Score", "81")], {"Home Score": "why"})
    assert sent(out) == [("Home Score", "81", "Success")]


def test_a_removed_field_is_forgotten(make_result):
    holds = PlausibilityHolds()
    holds.apply([make_result("Home Score", "21")], {})
    holds.apply([make_result("Away Score", "3")], {})
    # a new field with the old name has no value sent yet
    out = holds.apply([make_result("Home Score", "81")], {"Home Score": "why"})
    assert sent(out) == [("Home Score", "81", "Success")]


def test_the_log_says_what_was_held_and_why(pipeline, caplog):
    caplog.set_level(logging.INFO)
    pipeline(Home_Score="21")
    pipeline(Home_Score="21")
    pipeline(Home_Score="81")
    pipeline(Home_Score="81")
    pipeline(Home_Score="21")
    for _ in range(8):
        pipeline(Home_Score="18")
    pipeline(Home_Score="19")
    pipeline(Home_Score="50")
    pipeline(Home_Score="20")
    messages = [r.getMessage() for r in caplog.records if "Plausibility" in r.message]
    assert messages == [
        "Plausibility hold: Home Score read 81, 21 kept: a score going up by 60, "
        "which Basketball doesn't allow; it is sent once read steadily for a few "
        "seconds",
        "Plausibility hold: Home Score back to 21, 81 dropped",
        "Plausibility hold: Home Score read 18, 21 kept: a score going down, which "
        "Basketball doesn't allow; it is sent once read steadily for a few seconds",
        "Plausibility hold: Home Score read 18 steadily, sent",
        "Plausibility hold: Home Score read 50, 19 kept: a score going up by 32, "
        "which Basketball doesn't allow; it is sent once read steadily for a few "
        "seconds",
        "Plausibility hold: Home Score read 20, sent (50 dropped)",
    ]


def test_a_clock_flicker_does_not_reach_the_file(pipeline):
    from file_output import FileOutput

    pipeline(Time="8:00")
    output = FileOutput()
    output.record(pipeline(Time="1:00"))
    assert output.latest_text("Time") == "8:00"


def test_a_live_countdown_reaches_results_json(pipeline, tmp_path):
    """Basketball: after a jump off 0:00.0, game_clock follows the countdown."""
    import json

    from file_output import JSON_FILE_NAME, FileOutput

    pipeline(step=0.2, Time="0:00.0")
    output = FileOutput()
    output.record(pipeline(step=0.2, Time="0:00.0"))
    tenths = 8 * 600 + 50 * 10 + 7  # 8:50.7
    fed = []
    for _ in range(40):
        text = f"{tenths // 600}:{(tenths // 10) % 60:02d}.{tenths % 10}"
        output.record(pipeline(step=0.1, Time=text))
        fed.append(output.latest_text("Time"))
        tenths -= 1
    assert fed[0] == "0:00.0"
    assert fed[29] == "0:00.0"
    assert fed[30] != "0:00.0"
    assert fed[-1].startswith("8:")
    output.flush(str(tmp_path), save_json=True)
    row = json.loads((tmp_path / JSON_FILE_NAME).read_text(encoding="utf-8"))
    assert row[0]["game_clock"] == fed[-1]
    assert ":00.0" not in row[0]["game_clock"]

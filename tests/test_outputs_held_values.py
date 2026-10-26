"""Hold value (src/held_values.py): what the outputs and the template fields
get while a field's value is held, and after it is released."""

import pytest

from held_values import HeldValues
from template_fields import TemplateFieldEvaluator
from text_detection_target import TextDetectionTargetWithResult

ResultState = TextDetectionTargetWithResult.ResultState


def sent(results):
    return [(r.name, r.result, r.result_state.name) for r in results]


@pytest.fixture
def held():
    return HeldValues()


def test_nothing_held_sends_the_readings_themselves(held, make_result):
    results = [make_result("Home Score", "12")]
    assert held.apply(results) is results
    assert not held and "Home Score" not in held


def test_a_held_value_is_new_once_then_unchanged(held, make_result):
    held.hold("Period", "3rd")
    assert "Period" in held and held.value("Period") == "3rd"
    home = make_result("Home Score", "12")
    period = make_result("Period", "Z", "FailedFilter")
    out = held.apply([home, period])
    assert sent(out) == [("Home Score", "12", "Success"), ("Period", "3rd", "Success")]
    # the OCR's reading isn't changed (the viewer shows it)
    assert out[0] is home
    assert (period.result, period.result_state) == ("Z", ResultState.FailedFilter)
    for state in ("Success", "SameNoChange", "Empty"):
        out = held.apply([make_result("Period", "2nd", state)])
        assert sent(out) == [("Period", "3rd", "SameNoChange")]
    # another value: new once
    held.hold("Period", "4th")
    assert sent(held.apply([make_result("Period", "2nd")])) == [
        ("Period", "4th", "Success")
    ]
    # the same value again: still unchanged
    held.hold("Period", "4th")
    assert sent(held.apply([make_result("Period", "2nd")])) == [
        ("Period", "4th", "SameNoChange")
    ]


def test_release_makes_the_next_good_reading_new(held, make_result):
    held.hold("Time", "0:00")
    held.apply([make_result("Time", "1:00")])
    assert held.release("Time") is True
    assert held.release("Time") is False
    assert "Time" not in held
    # a rejected reading isn't sent as good: still waiting for a good one
    assert sent(held.apply([make_result("Time", "1:0O", "FailedFilter")])) == [
        ("Time", "1:0O", "FailedFilter")
    ]
    # the OCR's unchanged value is new for the outputs (they had 0:00)
    assert sent(held.apply([make_result("Time", "1:00", "SameNoChange")])) == [
        ("Time", "1:00", "Success")
    ]
    assert sent(held.apply([make_result("Time", "1:00", "SameNoChange")])) == [
        ("Time", "1:00", "SameNoChange")
    ]


def test_templates_use_the_held_value(held, make_result):
    templates = TemplateFieldEvaluator()

    def cycle(home, away):
        template = make_result(
            "Score",
            "",
            "Empty",
            templatefield=True,
            templatefield_text="{{Home Score}} - {{Away Score}}",
        )
        out = held.apply(
            [
                make_result("Home Score", *home),
                make_result("Away Score", *away),
                template,
            ]
        )
        templates.apply(out)
        held.finish_released(out)
        return sent(out)[2]

    assert cycle(("12", "Success"), ("7", "Success")) == ("Score", "12 - 7", "Success")
    held.hold("Home Score", "14")
    assert cycle(("12", "SameNoChange"), ("7", "SameNoChange")) == (
        "Score",
        "14 - 7",
        "Success",
    )
    assert cycle(("12", "SameNoChange"), ("7", "SameNoChange")) == (
        "Score",
        "14 - 7",
        "SameNoChange",
    )
    # released: back to the OCR's value, although it didn't change
    held.release("Home Score")
    assert cycle(("12", "SameNoChange"), ("7", "SameNoChange")) == (
        "Score",
        "12 - 7",
        "Success",
    )


def test_a_held_template_field_is_not_evaluated(held, make_result):
    templates = TemplateFieldEvaluator()

    def cycle():
        template = make_result(
            "Score",
            "",
            "Empty",
            templatefield=True,
            templatefield_text="{{Home Score}}",
        )
        out = held.apply([make_result("Home Score", "12", "SameNoChange"), template])
        templates.apply(out)
        held.finish_released(out)
        return sent(out)[1]

    assert cycle() == ("Score", "12", "Success")
    assert cycle() == ("Score", "12", "SameNoChange")
    held.hold("Score", "HOME WINS")
    assert cycle() == ("Score", "HOME WINS", "Success")
    assert cycle() == ("Score", "HOME WINS", "SameNoChange")
    held.release("Score")
    # its text is the same as before the hold, but the outputs had another one
    assert cycle() == ("Score", "12", "Success")
    assert cycle() == ("Score", "12", "SameNoChange")


def test_rename_and_forget(held, make_result):
    held.hold("Custom 1", "x")
    held.apply([make_result("Custom 1", "1")])
    held.rename("Custom 1", "Shot Clock")
    assert held.values() == {"Shot Clock": "x"}
    # already sent under its old name
    assert sent(held.apply([make_result("Shot Clock", "1")])) == [
        ("Shot Clock", "x", "SameNoChange")
    ]
    held.forget("Shot Clock")
    assert held.values() == {}
    # forgotten, not released: the next reading is what the OCR says
    assert sent(held.apply([make_result("Shot Clock", "1", "SameNoChange")])) == [
        ("Shot Clock", "1", "SameNoChange")
    ]

import pytest

from template_fields import (
    TemplateFieldEvaluator,
    apply_template_fields,
    evaluate_template_field,
)


@pytest.fixture
def make_template(make_result):
    def _make(name, template, text="", state="Empty"):
        return make_result(
            name, text, state, templatefield=True, templatefield_text=template
        )

    return _make


def states(results):
    return {r.name: (r.result, r.result_state.name) for r in results}


def test_substitutes_good_readings(make_result, make_template):
    evaluator = TemplateFieldEvaluator()
    score = make_template("Score", "{{Home Score}} - {{Away Score}}")
    results = [
        make_result("Home Score", "12", "Success"),
        make_result("Away Score", "7", "Success"),
        score,
    ]
    evaluator.apply(results)
    assert (score.result, score.result_state.name) == ("12 - 7", "Success")


def test_failed_readings_are_never_substituted(make_result, make_template):
    # skip-similar-image yields "SIM" (FailedFilter, then SameNoChange); a
    # regex-rejected misread is FailedFilter. The template used to become
    # "SIM - SIM" and be reported as Success on every cycle.
    evaluator = TemplateFieldEvaluator()

    def cycle(home, away):
        score = make_template("Score", "{{Home Score}} - {{Away Score}}")
        results = [make_result("Home Score", *home), make_result("Away Score", *away)]
        evaluator.apply([*results, score])
        return score.result, score.result_state.name

    assert cycle(("12", "Success"), ("7", "Success")) == ("12 - 7", "Success")
    assert cycle(("SIM", "FailedFilter"), ("SIM", "FailedFilter")) == (
        "12 - 7",
        "SameNoChange",
    )
    assert cycle(("SIM", "SameNoChange"), ("8B", "FailedFilter")) == (
        "12 - 7",
        "SameNoChange",
    )
    assert cycle(("SIM", "SameNoChange"), ("", "Empty")) == ("12 - 7", "SameNoChange")
    assert cycle(("13", "Success"), ("7", "SameNoChange")) == ("13 - 7", "Success")
    assert cycle(("13", "SameNoChange"), ("7", "SameNoChange")) == (
        "13 - 7",
        "SameNoChange",
    )


def test_empty_until_every_referenced_box_had_a_good_reading(
    make_result, make_template
):
    evaluator = TemplateFieldEvaluator()
    score = make_template("Score", "{{Home Score}} - {{Away Score}}", "junk", "Success")
    evaluator.apply(
        [
            make_result("Home Score", "12", "Success"),
            make_result("Away Score", "SIM", "FailedFilter"),
            score,
        ]
    )
    assert (score.result, score.result_state.name) == ("", "Empty")

    score = make_template("Score", "{{Home Score}} - {{Away Score}}")
    evaluator.apply(
        [
            make_result("Home Score", "12", "SameNoChange"),
            make_result("Away Score", "7", "Success"),
            score,
        ]
    )
    assert (score.result, score.result_state.name) == ("12 - 7", "Success")


def test_same_no_change_counts_when_nothing_better_is_known(make_result, make_template):
    # e.g. the box was read before the template field was created
    evaluator = TemplateFieldEvaluator()
    period = make_template("Q", "Q{{Period}}")
    evaluator.apply([make_result("Period", "2", "SameNoChange"), period])
    assert (period.result, period.result_state.name) == ("Q2", "Success")


def test_a_box_given_a_removed_boxs_name_starts_fresh(make_result, make_template):
    # A box keeps its last good reading when it's renamed, and repeats it as
    # SameNoChange while its value doesn't change. The old box's value under
    # that name was kept forever, so the template showed it instead.
    evaluator = TemplateFieldEvaluator()

    def cycle(*boxes):
        template = make_template("Clock", "{{Shot Clock}}")
        evaluator.apply([*(make_result(*box) for box in boxes), template])
        return template.result, template.result_state.name

    assert cycle(("Shot Clock", "14", "Success")) == ("14", "Success")
    # "Shot Clock" is deleted (or renamed away): the placeholder names no box
    assert cycle(("Custom 2", "24", "Success")) == ("{{Shot Clock}}", "Success")
    # and another box that steadily reads 24 is renamed to "Shot Clock"
    assert cycle(("Shot Clock", "24", "SameNoChange")) == ("24", "Success")
    assert cycle(("Shot Clock", "24", "SameNoChange")) == ("24", "SameNoChange")


def test_unknown_placeholders_are_left_as_they_are(make_result, make_template):
    evaluator = TemplateFieldEvaluator()
    template = make_template("T", "{{Time}} left {{No Such Box}}")
    evaluator.apply([make_result("Time", "4:27", "Success"), template])
    assert (template.result, template.result_state.name) == (
        "4:27 left {{No Such Box}}",
        "Success",
    )


def test_empty_template(make_result, make_template):
    evaluator = TemplateFieldEvaluator()
    template = make_template("T", "", "junk", "Success")
    evaluator.apply([make_result("Time", "4:27", "Success"), template])
    assert (template.result, template.result_state.name) == ("", "Empty")


def test_templates_can_reference_templates(make_result, make_template):
    evaluator = TemplateFieldEvaluator()
    # "Line" is listed before "Score", which it references
    line = make_template("Line", "Score: {{Score}} ({{Period}})")
    score = make_template("Score", "{{Home Score}}-{{Away Score}}")
    evaluator.apply(
        [
            line,
            make_result("Home Score", "3", "Success"),
            make_result("Away Score", "1", "Success"),
            make_result("Period", "2nd", "Success"),
            score,
        ]
    )
    assert line.result == "Score: 3-1 (2nd)"
    assert score.result == "3-1"


def test_circular_templates_are_empty_and_do_not_grow(make_template):
    evaluator = TemplateFieldEvaluator()
    for _ in range(3):
        a = make_template("A", "x{{B}}")
        b = make_template("B", "y{{A}}")
        selfref = make_template("C", "{{C}}!")
        evaluator.apply([a, b, selfref])
        assert states([a, b, selfref]) == {
            "A": ("", "Empty"),
            "B": ("", "Empty"),
            "C": ("", "Empty"),
        }


def test_template_box_ocr_text_is_ignored(make_result, make_template):
    # template fields are boxes too; whatever OCR read in them is not a value
    evaluator = TemplateFieldEvaluator()
    other = make_template("Other", "{{Home Score}}", "99", "Success")
    t = make_template("T", "[{{Other}}]")
    evaluator.apply([make_result("Home Score", "4", "Success"), other, t])
    assert t.result == "[4]"


def test_placeholder_values_are_not_substituted_again(make_result, make_template):
    evaluator = TemplateFieldEvaluator()
    t = make_template("T", "{{A}} {{B}}")
    evaluator.apply(
        [make_result("A", "{{B}}", "Success"), make_result("B", "b", "Success"), t]
    )
    assert t.result == "{{B}} b"


def test_module_functions(make_result, make_template, monkeypatch):
    import template_fields

    monkeypatch.setattr(template_fields, "_evaluator", TemplateFieldEvaluator())
    template = make_template("T", "{{Time}}!")
    results = [make_result("Time", "SIM", "FailedFilter"), template]
    assert evaluate_template_field(results, template) is None
    apply_template_fields(results)
    assert (template.result, template.result_state.name) == ("", "Empty")

    results = [make_result("Time", "1:00", "Success"), template]
    assert evaluate_template_field(results, template) == "1:00!"
    apply_template_fields(results)
    assert (template.result, template.result_state.name) == ("1:00!", "Success")
    apply_template_fields(results)
    assert (template.result, template.result_state.name) == ("1:00!", "SameNoChange")

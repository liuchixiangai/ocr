"""ocr_eval: normalization, per-item scores, aggregate metrics and intervals."""

import pytest

pytest.importorskip("numpy")

from ocr_eval.metrics import (  # noqa: E402
    ItemScore,
    bootstrap_ratio_interval,
    cluster_bootstrap_interval,
    cluster_count,
    cluster_sign_flip_p,
    latency_summary,
    levenshtein,
    mcnemar_exact_p,
    normalize,
    score_item,
    summarize,
    wilson_interval,
)


def test_normalize_removes_whitespace_and_applies_nfkc():
    assert normalize(" 1 2\n", "number") == "12"
    # full-width digits and colon
    assert normalize("１２：３４", "clock") == "12:34"
    assert normalize(None, "score") == ""


def test_normalize_is_case_insensitive_for_text_only():
    assert normalize("HOME", "text") == normalize("home", "text")
    assert normalize("Guest", "other") == "guest"
    assert normalize("2ND", "number") == "2ND"


def test_clock_separator_must_match_for_exact_but_not_digits():
    score = score_item("12.34", "12:34", "clock")
    assert not score.exact
    assert score.digits
    assert score.edits == 1
    assert score_item("12:34", "12:34", "clock").exact


def test_digits_metric_needs_the_right_digits():
    assert not score_item("1O", "10", "number").digits
    assert score_item("1 0", "10", "number").digits
    # for text fields the digits metric is the exact metric
    assert not score_item("2rd", "2nd", "text").digits
    assert score_item("2ND", "2nd", "text").digits


def test_empty_prediction():
    score = score_item("", "45", "score")
    assert score.empty and not score.exact
    assert (score.edits, score.length) == (2, 2)


@pytest.mark.parametrize(
    ("a", "b", "distance"),
    [
        ("", "", 0),
        ("abc", "", 3),
        ("", "ab", 2),
        ("kitten", "sitting", 3),
        ("12", "21", 2),
    ],
)
def test_levenshtein(a, b, distance):
    assert levenshtein(a, b) == distance
    assert levenshtein(b, a) == distance


def test_wilson_interval():
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and high == pytest.approx(0.2775, abs=1e-3)
    low, high = wilson_interval(50, 100)
    assert (low, high) == (
        pytest.approx(0.4038, abs=1e-3),
        pytest.approx(0.5962, abs=1e-3),
    )
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_bootstrap_interval_is_reproducible_and_brackets_the_estimate():
    edits = [0, 1, 0, 2, 0, 0, 1, 0, 0, 3] * 5
    lengths = [2, 2, 3, 2, 1, 2, 4, 2, 2, 3] * 5
    first = bootstrap_ratio_interval(edits, lengths)
    assert first == bootstrap_ratio_interval(edits, lengths)
    assert first[0] < sum(edits) / sum(lengths) < first[1]
    assert bootstrap_ratio_interval([], []) == (0.0, 0.0)


def test_mcnemar_exact():
    assert mcnemar_exact_p(0, 0) == 1.0
    assert mcnemar_exact_p(10, 0) == pytest.approx(2 / 1024)
    assert mcnemar_exact_p(3, 7) == mcnemar_exact_p(7, 3)
    assert mcnemar_exact_p(5, 5) == 1.0
    # large counts don't overflow
    assert 0.0 < mcnemar_exact_p(700, 640) < 1.0


def test_cluster_bootstrap_with_one_item_per_cluster_is_the_item_bootstrap():
    values = [1, 0, 1, 1, 0, 1, 1, 1, 0, 1] * 4
    assert cluster_bootstrap_interval(values, range(len(values))) == (
        bootstrap_ratio_interval(values, [1] * len(values))
    )
    assert cluster_bootstrap_interval([], []) == (0.0, 0.0)
    with pytest.raises(ValueError):
        cluster_bootstrap_interval([1, 0], ["a"])


def test_cluster_bootstrap_is_wider_when_crops_of_a_photo_agree():
    # 20 photos of 10 crops each: a photo is read entirely right or wrong
    values = [photo % 2 for photo in range(20) for _ in range(10)]
    photos = [photo for photo in range(20) for _ in range(10)]
    low, high = cluster_bootstrap_interval(values, photos)
    crop_low, crop_high = cluster_bootstrap_interval(values, range(len(values)))
    assert low < 0.5 < high
    assert high - low > 2 * (crop_high - crop_low)
    assert cluster_count(photos) == 20
    assert cluster_bootstrap_interval(values, photos) == (low, high)  # seeded


def test_sign_flip_with_one_item_per_cluster_is_mcnemar():
    # 3 crops only a reads, 9 only b reads, 20 both or neither
    differences = [-1] * 3 + [1] * 9 + [0] * 20
    p = cluster_sign_flip_p(differences, range(len(differences)))
    assert p == pytest.approx(mcnemar_exact_p(3, 9))
    assert cluster_sign_flip_p([0, 0, 0], ["a", "b", "c"]) == 1.0


def test_sign_flip_counts_photos_not_crops():
    # one photo where b reads 12 more crops: significant if the crops were
    # independent, not as one photo
    differences = [1] * 12 + [0] * 30
    photos = ["big"] * 12 + [f"p{i}" for i in range(30)]
    assert mcnemar_exact_p(0, 12) < 0.001
    assert cluster_sign_flip_p(differences, photos) == 1.0
    # many photos that each lean the same way: significant (Monte Carlo)
    differences = [1, 1, -1, 0] * 40
    photos = [i // 4 for i in range(160)]
    p = cluster_sign_flip_p(differences, photos)
    assert p == 1 / 20001  # all 40 photos +1: no random pattern reaches it
    assert p == cluster_sign_flip_p(differences, photos)  # seeded


class _Sample:
    def __init__(self, field_type, display_tech="led7", split="test"):
        self.field_type = field_type
        self.display_tech = display_tech
        self.split = split


def test_summarize_overall_and_by_group():
    scored = [
        (_Sample("score"), score_item("12", "12", "score")),
        (_Sample("score", "lcd"), score_item("13", "12", "score")),
        (_Sample("clock", split="dev"), score_item("", "1:00", "clock")),
    ]
    summary = summarize(scored)
    assert summary["n"] == 3
    assert summary["exact"]["k"] == 1
    assert summary["exact"]["value"] == pytest.approx(1 / 3)
    assert summary["empty_rate"]["k"] == 1
    assert summary["cer"]["value"] == pytest.approx((0 + 1 + 4) / (2 + 2 + 4))
    assert set(summary["by_field_type"]) == {"score", "clock"}
    assert summary["by_field_type"]["score"]["exact"]["k"] == 1
    assert summary["by_display_tech"]["lcd"]["n"] == 1
    assert summary["by_split"]["dev"]["n"] == 1
    low, high = summary["exact"]["ci95"]
    assert low < 1 / 3 < high


def test_latency_summary():
    assert latency_summary([]) is None
    summary = latency_summary([1.0, 2.0, 3.0, 4.0, 100.0])
    assert summary["p50_ms"] == 3.0
    assert summary["max_ms"] == 100.0
    assert summary["n"] == 5


def test_item_score_is_hashable_value():
    assert ItemScore(True, True, 0, 2, False) == score_item("12", "12", "score")

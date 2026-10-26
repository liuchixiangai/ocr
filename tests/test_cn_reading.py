"""光为 OCR reading cleanup: Chinese periods and clock leading zeros."""

import re

from cn_reading import (
    CLOCK_FORMAT_EXPANDED,
    normalize_period_text,
    prepare_reading,
    strip_leading_zeros,
)


def test_bare_chinese_period_becomes_a_digit():
    assert normalize_period_text("二") == "2"
    assert normalize_period_text(" 四 ") == "4"
    assert normalize_period_text("十") == "10"
    assert normalize_period_text("第二节") == "2"
    assert normalize_period_text("第2节") == "2"
    assert normalize_period_text("二节") == "2"
    # not a single numeral
    assert normalize_period_text("二三") is None
    assert prepare_reading("节次", "二") == "2"
    assert prepare_reading("Period", "二") == "2"
    # the basketball period format expects a digit; 「二」must pass it
    assert re.fullmatch(r"^[1-4]$", prepare_reading("节次", "二"))


def test_strip_leading_zeros_keeps_a_clock():
    assert strip_leading_zeros("007") == "7"
    assert strip_leading_zeros("00") == "0"
    assert strip_leading_zeros("0") == "0"
    assert strip_leading_zeros("0:00.0") == "0:00.0"
    assert strip_leading_zeros("00:00.0") == "0:00.0"
    assert strip_leading_zeros("01:16.7") == "1:16.7"
    assert strip_leading_zeros("08:50.7") == "8:50.7"
    assert strip_leading_zeros("08:05") == "8:05"
    assert strip_leading_zeros("00:09.8") == "0:09.8"
    assert strip_leading_zeros("10:05") == "10:05"
    assert strip_leading_zeros("12:00.9") == "12:00.9"
    assert strip_leading_zeros("09.8") == "9.8"
    assert strip_leading_zeros("00.0") == "0.0"
    # a whole-string lstrip used to produce this; it is not a clock
    assert not re.fullmatch(CLOCK_FORMAT_EXPANDED, ":00.0")
    for text in ("0:00.0", "1:16.7", "8:50.7", "8:05", "0:09.8", "9.8", "0.0"):
        assert re.fullmatch(CLOCK_FORMAT_EXPANDED, strip_leading_zeros(text))

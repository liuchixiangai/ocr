import re

import pytest

from defaults import (
    FieldType,
    default_boxes,
    default_custom_box_info,
    default_info_for_box_name,
    format_prefixes,
    normalize_settings_dict,
)

CANONICAL_SETTINGS = {
    "is_custom": True,
    "obs_source_name": "",
    "format_regex": format_prefixes[11],
    "type": FieldType.NUMBER,
    "smoothing": False,
    "clear_value": "never",
    "conf_thresh": 0.0,
    "cleanup_thresh": 0,
    "dilate": 1,
    "skew": 0,
    "vscale": 10,
    "autocrop": False,
    "skip_similar_image": False,
    "remove_leading_zeros": False,
    "rescale_patch": True,
    "normalize_wh_ratio": False,
    "invert_patch": False,
    "dot_detector": False,
    "binarization_method": 0,
    "ordinal_indicator": False,
    "templatefield": False,
    "templatefield_text": "",
    "composite_box": False,
    "led_stack_frames": 4,
    "led_stack_mode": "max",
    "led_stable_ms": 200,
}


@pytest.mark.parametrize("settings", [{}, None])
def test_normalize_settings_defaults(settings):
    assert normalize_settings_dict(settings, None) == CANONICAL_SETTINGS


def test_normalize_settings_uses_box_info_for_missing_keys():
    period = normalize_settings_dict({}, default_info_for_box_name("Period"))
    assert period["is_custom"] is False
    assert period["ordinal_indicator"] is True
    assert period["format_regex"] == format_prefixes[7]
    assert period["obs_source_name"] == "Period"
    assert period["type"] == FieldType.NUMBER

    clock = normalize_settings_dict({}, default_info_for_box_name("Time"))
    assert clock["type"] == FieldType.TIME
    assert clock["is_custom"] is False
    assert clock["format_regex"] == format_prefixes[0]
    # digits-only "1200" is what a NUMBER whitelist publishes for 12:00
    assert re.fullmatch(clock["format_regex"], "12:00")
    assert re.fullmatch(clock["format_regex"], "1200") is None
    chinese = normalize_settings_dict({}, default_info_for_box_name("比赛时间"))
    assert chinese["type"] == FieldType.TIME
    assert chinese["format_regex"] == clock["format_regex"]


def test_normalize_settings_keeps_user_values():
    user = {
        "type": FieldType.TEXT,
        "conf_thresh": 0.9,
        "ordinal_indicator": False,
        "format_regex": "^x$",
        "dilate": 0,
    }
    result = normalize_settings_dict(user, default_info_for_box_name("Period"))
    for key, value in user.items():
        assert result[key] == value
    # missing keys still get defaults
    assert result["vscale"] == 10
    # the input dict is not modified
    assert set(user) == {
        "type",
        "conf_thresh",
        "ordinal_indicator",
        "format_regex",
        "dilate",
    }


def test_normalize_settings_drops_unknown_and_runtime_keys():
    result = normalize_settings_dict(
        {"median_wh_ratio": 0.5, "no_such_setting": 1}, None
    )
    assert set(result) == set(CANONICAL_SETTINGS)


def test_default_info_for_box_name():
    names = [box["name"] for box in default_boxes]
    assert names == [
        "主队得分",
        "客队得分",
        "比赛时间",
        "节次",
        "主队犯规",
        "客队犯规",
        "24秒",
    ]
    for box in default_boxes:
        assert default_info_for_box_name(box["name"]) is box
        assert box["is_custom"] is False
        # every key normalize_settings_dict reads from box info is present
        assert {"type", "obs_source_name", "format_regex", "ordinal_indicator"} <= set(
            box
        )
    assert default_info_for_box_name("Team Name") is default_custom_box_info
    assert default_custom_box_info["is_custom"] is True


def test_format_prefixes_compile():
    assert len(format_prefixes) == 13
    for pattern in format_prefixes:
        re.compile(pattern)


@pytest.mark.parametrize(
    ("index", "text", "matches"),
    [
        (0, "12:34", True),
        (0, "9.5", True),
        (0, "60:00", False),
        (1, "5:07", True),
        (1, "5.7", False),
        (2, "59.9", True),
        (3, "07", True),
        # clocks show 7, not 07
        (3, "7", True),
        (3, "60", False),
        (3, "007", False),
        (4, "39", True),
        (4, "8", True),
        (4, "08", True),
        (4, "0", True),
        (4, "4.9", True),
        (4, "0.5", True),
        (4, "39.9", True),
        (4, "40", False),
        (4, "4.99", False),
        (4, "40.0", False),
        (4, "", False),
        (4, "100", False),
        (5, "199", True),
        (5, "200", False),
        (6, "999", True),
        (7, "4", True),
        (7, "5", False),
        (8, "7", True),
        (9, "Home1", True),
        (9, "Home 1", False),
        (10, "anything at all", True),
        (11, "", True),
        (11, "12a", False),
    ],
)
def test_format_prefix_presets(index, text, matches):
    assert (re.fullmatch(format_prefixes[index], text) is not None) == matches


@pytest.mark.parametrize(
    ("text", "matches"),
    [
        ("24", True),
        ("10", True),
        ("9", True),
        ("8", True),
        ("0", True),
        ("4.9", True),
        ("0.5", True),
        ("40", False),
        ("4.99", False),
    ],
)
def test_shot_clock_accepts_a_single_digit(text, matches):
    # A shot clock shows 9, 8, ... 0 in its last ten seconds, not 09, and
    # 4.9 / 0.5 in the last five. A digits-only format dropped those tenths.
    shot_clock = normalize_settings_dict({}, default_info_for_box_name("24秒"))
    assert shot_clock["format_regex"] == format_prefixes[4]
    assert (re.fullmatch(shot_clock["format_regex"], text) is not None) == matches


def test_scoresight_output_dir_overrides_the_desktop_folder(tmp_path, monkeypatch):
    from product_brand import configured_output_folder

    monkeypatch.delenv("SCORESIGHT_OUTPUT_DIR", raising=False)
    assert configured_output_folder() is None
    dest = tmp_path / "e2e-out"
    monkeypatch.setenv("SCORESIGHT_OUTPUT_DIR", str(dest))
    assert configured_output_folder() == str(dest)
    assert dest.is_dir()
    monkeypatch.setenv("SCORESIGHT_OUTPUT_DIR", "  ")
    assert configured_output_folder() is None

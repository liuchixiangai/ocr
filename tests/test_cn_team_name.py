"""中文队名: the digit whitelist must not strip simplified Chinese.

PP-OCRv6 already contains the characters. A new field used to be a number
(format ^\\d*$, charset 0-9), so a boxed team name came out blank or as
garbage digits.
"""

import os

import numpy as np
import pytest

from box_pick import (
    BOX_PICKS,
    initial_key,
    next_custom_name,
    settings_for_pick,
    pick_by_key,
)
from cn_reading import is_free_text_box, keep_free_text, whitelist_for_box
from defaults import (
    CHAR_WHITELISTS,
    FieldType,
    default_info_for_box_name,
    default_led_stack_frames,
    default_led_stable_ms,
    format_prefixes,
    normalize_settings_dict,
)
from file_output import json_field_key
import ppocr_engine

DIGITS = CHAR_WHITELISTS[FieldType.NUMBER]


def test_team_name_whitelist_is_the_whole_model_charset():
    assert whitelist_for_box("主队队名", {"type": FieldType.NUMBER}, DIGITS) is None
    assert whitelist_for_box("客队队名", {"type": FieldType.TEXT}, DIGITS) is None
    assert whitelist_for_box("文本 1", {"type": FieldType.NUMBER}, DIGITS) is None
    # scores, fouls, clocks and the period stay narrow
    assert whitelist_for_box("主队得分", {"type": FieldType.NUMBER}, DIGITS) == DIGITS
    assert "广" not in whitelist_for_box("主队犯规", {"type": FieldType.NUMBER}, DIGITS)
    assert "." not in whitelist_for_box("主队得分", {"type": FieldType.NUMBER}, DIGITS)
    period = whitelist_for_box("节次", {"type": FieldType.NUMBER}, DIGITS)
    assert period is not None and "节" in period and "广" not in period
    clock = whitelist_for_box("比赛时间", {"type": FieldType.TIME}, "0123456789:.")
    assert clock is not None and "广" not in clock


def test_a_digit_format_does_not_drop_a_team_name():
    assert keep_free_text("主队队名", "广东", {"type": 0}, r"^\d*$")
    assert keep_free_text("文本 2", "辽宁", {"type": 2}, r"^[A-Za-z0-9]*$")
    # a score is not rescued
    assert not keep_free_text("主队得分", "广东", {"type": 0}, r"^\d*$")
    # an expression that already allows 广东 is left to fullmatch
    assert not keep_free_text("主队队名", "广东", {"type": 2}, r"^.*$")
    assert is_free_text_box("主队得分", {"type": 0}) is False
    assert is_free_text_box("Custom", {"type": FieldType.TEXT}) is True


def test_ppocr_decode_keeps_chinese_only_when_the_box_allows_it():
    characters = ["", "8", "广", "东", " "]
    steps = 8
    probs = np.full((steps, len(characters)), 0.01, np.float32)
    probs[:, 0] = 0.97
    guang, dong = characters.index("广"), characters.index("东")
    eight = characters.index("8")
    # a blank step between them, so a digit fallback is "88" and not one "8"
    for t, cls in ((1, guang), (3, dong)):
        probs[t] = 0.01
        probs[t, cls] = 0.9
        probs[t, eight] = 0.05
        probs[t, 0] = 0.03
    recognizer = ppocr_engine.Recognizer(ppocr_engine.SMALL)
    assert recognizer.decode(probs, characters, None)[0] == "广东"
    # the old digit whitelist cannot emit 广/东, so it falls back to digits
    assert recognizer.decode(probs, characters, DIGITS)[0] == "88"


def test_new_team_name_box_is_text_and_a_score_stays_digits():
    info = default_info_for_box_name("主队队名")
    settings = normalize_settings_dict(
        {"type": FieldType.NUMBER, "format_regex": r"^\d*$", "led_stable_ms": 200},
        info,
    )
    assert settings["type"] == FieldType.TEXT
    assert settings["format_regex"] == format_prefixes[10]
    assert settings["led_stack_frames"] == 0
    assert settings["led_stable_ms"] == 0
    score = normalize_settings_dict({}, default_info_for_box_name("主队得分"))
    assert score["type"] == FieldType.NUMBER
    assert score["format_regex"] == format_prefixes[5]
    assert default_led_stack_frames("主队得分") == 4
    assert default_led_stable_ms("主队队名", FieldType.TEXT) == 0
    assert default_led_stack_frames("比赛时间") == 0
    assert json_field_key("主队队名") == "home_name"
    assert json_field_key("客队队名") == "away_name"
    assert json_field_key("主队得分") == "home_score"


def test_box_pick_dialog_choices_name_chinese_team_names():
    labels = [pick.label_zh for pick in BOX_PICKS]
    assert any("主队队名" in label and "中文" in label for label in labels)
    assert any("客队队名" in label and "中文" in label for label in labels)
    assert any("主队得分" in label and "数字" in label for label in labels)
    assert any("自定义文本" in label and "中文" in label for label in labels)
    assert initial_key(None, new_field=True) == "custom_text"
    assert initial_key("主队得分", new_field=False) == "home_score"
    assert initial_key("客队队名", new_field=False) == "away_name"
    home = pick_by_key("home_name")
    settings = settings_for_pick(
        "主队队名", home, {"type": 0, "format_regex": r"^\d*$"}
    )
    assert settings["type"] == FieldType.TEXT
    assert settings["format_regex"] == "^.*$"
    number = settings_for_pick("数字 1", pick_by_key("custom_number"))
    assert number["type"] == FieldType.NUMBER
    assert next_custom_name({"文本 1", "主队得分"}, "文本") == "文本 2"


def test_box_pick_dialog_lists_the_choices(qtbot):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QDialog
    from box_pick import BoxPickDialog

    dialog = BoxPickDialog(None, "home_name", chinese=True)
    qtbot.addWidget(dialog)
    labels = [dialog.list.item(i).text() for i in range(dialog.list.count())]
    assert dialog.list.currentItem().text().startswith("主队队名")
    assert any("中文文字" in label for label in labels)
    assert any("数字" in label for label in labels)
    dialog.list.setCurrentRow(0)
    dialog._accept_current()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.pick.key == "home_score"


@pytest.mark.ppocr
def test_real_model_reads_a_chinese_team_name(make_target):
    """A 队名 box on a rendered 广东 crop returns Chinese, not a digit."""
    font_path = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
    if not os.path.isfile(font_path):
        pytest.skip("no CJK font at " + font_path)
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("tesserocr")
    from PIL import Image, ImageDraw, ImageFont

    import tesseract
    from tesseract import TextDetector
    from text_detection_target import TextDetectionTargetWithResult

    image = Image.new("RGB", (360, 96), (8, 8, 8))
    draw = ImageDraw.Draw(image)
    draw.text(
        (12, 16),
        "广东",
        font=ImageFont.truetype(font_path, 64),
        fill=(255, 255, 255),
    )
    color = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
    team = make_target("主队队名", rect=(0, 0, color.shape[1], color.shape[0]))
    score = make_target("主队得分", rect=(0, 0, color.shape[1], color.shape[0]))
    detector = TextDetector(ppocr_engine.SMALL)
    assert detector.ppocr.load()
    try:
        [team_result] = detector.detect_multi_text(None, gray, [team], color=color)
        [score_result] = detector.detect_multi_text(None, gray, [score], color=color)
    finally:
        if detector.api is not None:
            detector.api.End()
    success = TextDetectionTargetWithResult.ResultState.Success
    assert team_result.state == success
    assert "广" in team_result.text
    assert "东" in team_result.text
    assert "广" not in (score_result.text or "")
    # the digit path must not be what published the team name
    assert team.settings["type"] == FieldType.TEXT
    assert score.settings["type"] == FieldType.NUMBER
    assert tesseract.CHAR_WHITELISTS[FieldType.NUMBER] == "0123456789"

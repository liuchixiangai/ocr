"""框选: pick what the next box is before it is drawn.

Scores stay digit OCR. 主队队名 / 客队队名 / 自定义文本 use the text field
(PP-OCR's full character list, so simplified Chinese is not stripped).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from defaults import (
    FieldType,
    default_boxes,
    default_custom_box_info,
    default_info_for_box_name,
    default_text_box_info,
    format_prefixes,
    is_chinese_text_name,
    normalize_settings_dict,
)


@dataclass(frozen=True)
class BoxPick:
    key: str
    label_zh: str
    detail_zh: str
    label_en: str
    detail_en: str
    # None: allocate 文本 N / 数字 N (or reclassify the row that was clicked)
    name: str | None
    field_type: int
    format_index: int
    custom_prefix: str | None = None

    def label(self, chinese: bool) -> str:
        return self.label_zh if chinese else self.label_en

    def detail(self, chinese: bool) -> str:
        return self.detail_zh if chinese else self.detail_en


BOX_PICKS: tuple[BoxPick, ...] = (
    BoxPick(
        "home_score",
        "主队得分 · 数字",
        "比分，只认 0–9",
        "Home score · digits",
        "Score, digits only",
        "主队得分",
        FieldType.NUMBER,
        5,
    ),
    BoxPick(
        "away_score",
        "客队得分 · 数字",
        "比分，只认 0–9",
        "Away score · digits",
        "Score, digits only",
        "客队得分",
        FieldType.NUMBER,
        5,
    ),
    BoxPick(
        "clock",
        "比赛时间 · 时间",
        "如 12:34 或 0:04.9",
        "Game clock · time",
        "Such as 12:34 or 0:04.9",
        "比赛时间",
        FieldType.TIME,
        0,
    ),
    BoxPick(
        "shot_clock",
        "24秒 · 数字",
        "可带一位小数，如 4.9",
        "Shot clock · digits",
        "One decimal is allowed, such as 4.9",
        "24秒",
        FieldType.NUMBER,
        4,
    ),
    BoxPick(
        "period",
        "节次 · 数字",
        "1–4，或「二」这种汉字节次",
        "Period · digits",
        "1–4, or a Chinese numeral such as 二",
        "节次",
        FieldType.NUMBER,
        7,
    ),
    BoxPick(
        "home_fouls",
        "主队犯规 · 数字",
        "犯规次数",
        "Home fouls · digits",
        "Foul count",
        "主队犯规",
        FieldType.NUMBER,
        11,
    ),
    BoxPick(
        "away_fouls",
        "客队犯规 · 数字",
        "犯规次数",
        "Away fouls · digits",
        "Foul count",
        "客队犯规",
        FieldType.NUMBER,
        11,
    ),
    BoxPick(
        "home_name",
        "主队队名 · 中文文字",
        "简体中文队名，不要选数字",
        "Home team name · Chinese text",
        "Simplified Chinese. Not a digit field.",
        "主队队名",
        FieldType.TEXT,
        10,
    ),
    BoxPick(
        "away_name",
        "客队队名 · 中文文字",
        "简体中文队名，不要选数字",
        "Away team name · Chinese text",
        "Simplified Chinese. Not a digit field.",
        "客队队名",
        FieldType.TEXT,
        10,
    ),
    BoxPick(
        "custom_text",
        "自定义文本 · 中文文字",
        "队名、赞助、其他汉字",
        "Custom text · Chinese text",
        "Team names, sponsors, other Chinese",
        None,
        FieldType.TEXT,
        10,
        "文本",
    ),
    BoxPick(
        "custom_number",
        "自定义数字 · 数字",
        "其他比分、次数",
        "Custom number · digits",
        "Another score or count",
        None,
        FieldType.NUMBER,
        11,
        "数字",
    ),
)

_BY_KEY = {pick.key: pick for pick in BOX_PICKS}
_DEFAULT_NAMES = {box["name"] for box in default_boxes}


def pick_by_key(key: str | None) -> BoxPick | None:
    if not key:
        return None
    return _BY_KEY.get(key)


def initial_key(preferred_name: str | None, *, new_field: bool) -> str:
    """Which row to highlight. A new field defaults to 自定义文本 (Chinese)."""
    if preferred_name:
        for pick in BOX_PICKS:
            if pick.name == preferred_name:
                return pick.key
        if is_chinese_text_name(preferred_name):
            return "custom_text"
        if preferred_name.startswith("数字"):
            return "custom_number"
        if preferred_name not in _DEFAULT_NAMES:
            return "custom_number"
    if new_field:
        return "custom_text"
    return BOX_PICKS[0].key


def info_for_pick(name: str, pick: BoxPick) -> dict:
    """Box info passed to normalize_settings_dict for this choice."""
    if pick.field_type == FieldType.TEXT or is_chinese_text_name(name):
        return {**default_text_box_info, "name": name}
    if pick.name and pick.name in _DEFAULT_NAMES:
        info = default_info_for_box_name(pick.name)
        return {**info, "name": name}
    return {**default_custom_box_info, "name": name}


def settings_for_pick(name: str, pick: BoxPick, existing: dict | None = None) -> dict:
    """Settings for a box created or reclassified as this choice.

    A placed score keeps a format the user already tuned when the choice is
    that same score. 队名 always becomes text with a format that allows 汉字,
    and without the score LED hold.
    """
    info = info_for_pick(name, pick)
    base = dict(existing or {})
    if pick.field_type == FieldType.TEXT or is_chinese_text_name(pick.name or name):
        base["type"] = FieldType.TEXT
        base["format_regex"] = format_prefixes[10]
        base["led_stack_frames"] = 0
        base["led_stable_ms"] = 0
        base["is_custom"] = name not in _DEFAULT_NAMES
    elif pick.name is None:
        base["type"] = pick.field_type
        base["format_regex"] = format_prefixes[pick.format_index]
        base["is_custom"] = True
    return normalize_settings_dict(base, info)


def next_custom_name(existing: set[str], prefix: str) -> str:
    i = 1
    while f"{prefix} {i}" in existing:
        i += 1
    return f"{prefix} {i}"


class BoxPickDialog(QDialog):
    """这次框选什么？ Shown before the user draws on the picture."""

    def __init__(self, parent, current_key: str | None, *, chinese: bool = True):
        super().__init__(parent)
        self._chinese = chinese
        self.pick: BoxPick | None = None
        self.setWindowTitle("这次框选什么？" if chinese else "What are you boxing?")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        hint = QLabel(self)
        hint.setWordWrap(True)
        if chinese:
            hint.setText(
                "先选要比的内容，再在画面上拖框。\n"
                "队名、赞助请选「中文文字」。选「数字」会把汉字认成空白或乱数字。"
            )
        else:
            hint.setText(
                "Choose what this box is, then drag it on the picture.\n"
                "Team names need Chinese text. Digits cannot read 汉字."
            )
        layout.addWidget(hint)
        self.list = QListWidget(self)
        self.list.setObjectName("listWidget_boxPick")
        for pick in BOX_PICKS:
            item = QListWidgetItem(pick.label(chinese))
            item.setData(Qt.ItemDataRole.UserRole, pick.key)
            item.setToolTip(pick.detail(chinese))
            self.list.addItem(item)
        key = (
            current_key if current_key in _BY_KEY else initial_key(None, new_field=True)
        )
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == key:
                self.list.setCurrentRow(row)
                break
        self.list.itemDoubleClicked.connect(self._accept_current)
        layout.addWidget(self.list, 1)
        buttons = QDialogButtonBox(self)
        go = buttons.addButton(
            "开始框选" if chinese else "Draw the box",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        buttons.addButton(
            "取消" if chinese else "Cancel",
            QDialogButtonBox.ButtonRole.RejectRole,
        )
        go.setObjectName("pushButton_boxPickGo")
        buttons.accepted.connect(self._accept_current)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_current(self, *_args):
        row = self.list.currentRow()
        if row < 0:
            return
        key = self.list.item(row).data(Qt.ItemDataRole.UserRole)
        self.pick = pick_by_key(str(key))
        self.accept()

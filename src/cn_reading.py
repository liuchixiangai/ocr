"""光为 OCR: clean scoreboard readings before format / ordinal filters.

Chinese boards often show 第四节 / 01:16.7. The English ScoreSight presets
expected a bare digit + optional "th", and a clock of either mm:ss *or* ss.d
(not mm:ss.d). This module normalizes common OCR text so the existing filters
accept real Chinese LED boards.
"""

from __future__ import annotations

import re

from sports import CLOCK, PERIOD, role_of

# 一..十 for quarter / period labels (basketball rarely past 4 + OT)
_CN_DIGITS = {
    "一": "1",
    "二": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
    "十": "10",
}

# Characters PP-OCR / Tesseract may emit for a period box on a Chinese board.
PERIOD_CHAR_WHITELIST = "0123456789第节上下半一二三四五六七八九十QqPpHhOoTtNnDdRrSs "

# Expanded clock: mm:ss, mm:ss.d, m:ss.d, ss.d (七段常见 01:16.7)
CLOCK_FORMAT_EXPANDED = r"^(?:(?:[0-5]?\d:[0-5]\d(?:\.\d)?)|(?:[0-5]?\d\.\d))$"
# Old preset 0 (and UI label said mm:ss.d but regex lacked the fraction)
CLOCK_FORMAT_OLD = r"^(?:(?:[0-5]?\d:[0-5]\d)|(?:[0-5]?\d\.\d))$"

_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９：．",
    "0123456789:.",
)

_PERIOD_DI = re.compile(r"第\s*([1-9]|10|[一二三四五六七八九十])\s*节")
_PERIOD_CN_SHORT = re.compile(r"([一二三四五六七八九十])\s*节")
_PERIOD_Q = re.compile(r"^[QqPpHh]\s*([1-9]|10)$")
_PERIOD_ORD = re.compile(
    r"^([1-9]|10)(?:st|nd|rd|th)?$",
    re.IGNORECASE,
)
# OCR often turns 节 into a trailing 5 / S next to the quarter digit
_PERIOD_DIGIT_NOISE = re.compile(r"^([1-4])[5Ss节]$")


def _cn_digit(token: str) -> str | None:
    if token in _CN_DIGITS:
        return _CN_DIGITS[token]
    if token.isdigit():
        n = int(token)
        if 1 <= n <= 20:
            return str(n)
    return None


def normalize_period_text(text: str) -> str | None:
    """Extract a period number as digits (\"4\"), or None if unknown.

    Accepts 第四节 / 第4节 / Q4 / 4th / 4 / OT. Output is always plain digits
    (or \"OT\" / \"OT2\") so vMix gets one stable shape — not 4th / 45th.
    """
    if text is None:
        return None
    s = str(text).strip().translate(_FULLWIDTH)
    if not s:
        return None
    upper = s.upper().replace(" ", "")
    # overtime
    if upper in ("OT", "O", "加时", "加時"):
        return "OT"
    m = re.fullmatch(
        r"(?:OT|O|加时|加時)\s*([1-9])?",
        upper if upper.startswith("OT") or upper.startswith("O") else s,
    )
    if upper.startswith("OT") or re.fullmatch(r"O\d?", upper):
        extra = re.search(r"(\d)", upper)
        return "OT" if not extra else f"OT{extra.group(1)}"
    m = _PERIOD_DI.search(s)
    if m:
        return _cn_digit(m.group(1))
    m = _PERIOD_CN_SHORT.search(s)
    if m:
        return _cn_digit(m.group(1))
    m = _PERIOD_Q.fullmatch(s.replace(" ", ""))
    if m:
        return m.group(1)
    m = _PERIOD_ORD.fullmatch(s.replace(" ", ""))
    if m:
        return m.group(1)
    m = _PERIOD_DIGIT_NOISE.fullmatch(s.replace(" ", ""))
    if m:
        return m.group(1)
    if re.fullmatch(r"[1-9]|10", s):
        return s
    return None


def normalize_clock_text(text: str) -> str:
    """Light cleanup: fullwidth digits/colon, strip spaces."""
    if text is None:
        return ""
    s = str(text).strip().translate(_FULLWIDTH).replace(" ", "")
    # OCR sometimes uses ; or , for : or .
    s = s.replace(";", ":").replace(",", ".")
    # collapse duplicate separators
    s = re.sub(r":+", ":", s)
    s = re.sub(r"\.+", ".", s)
    return s


def is_period_box(name: str | None, settings: dict | None = None) -> bool:
    if name and role_of(name) == PERIOD:
        return True
    if name:
        key = re.sub(r"[^a-z0-9]", "", name.lower())
        if key in ("period", "quarter", "qtr", "half", "set", "per", "periodnumber"):
            return True
        if name.strip() in ("节次", "节", "小节"):
            return True
    settings = settings or {}
    # legacy English boards that still tick "Add ordinal suffix"
    if settings.get("ordinal_indicator") and settings.get("type") == 0:
        # NUMBER + ordinal is almost always period in ScoreSight defaults
        return True
    return False


def is_clock_box(name: str | None, settings: dict | None = None) -> bool:
    if name and role_of(name) == CLOCK:
        return True
    settings = settings or {}
    if settings.get("type") == 1:  # FieldType.TIME
        return True
    return False


def prepare_reading(name: str | None, text: str, settings: dict | None = None) -> str:
    """Normalize text before format_regex / ordinal. Period -> plain digit."""
    settings = settings or {}
    if is_clock_box(name, settings):
        return normalize_clock_text(text)
    if is_period_box(name, settings):
        cleaned = normalize_period_text(text)
        if cleaned is not None:
            return cleaned
        return (text or "").strip()
    return text if text is not None else ""


def whitelist_for_box(name: str | None, settings: dict | None, default_for_type) -> str:
    """Char whitelist; period boxes allow Chinese 第N节 glyphs."""
    settings = settings or {}
    if is_period_box(name, settings):
        return PERIOD_CHAR_WHITELIST
    return default_for_type


def clock_passes_expanded(text: str) -> bool:
    return bool(re.fullmatch(CLOCK_FORMAT_EXPANDED, text or ""))

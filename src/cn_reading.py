"""光为 OCR: clean scoreboard readings before format / ordinal filters.

Chinese boards often show 第四节 / 01:16.7. The English ScoreSight presets
expected a bare digit + optional "th", and a clock of either mm:ss *or* ss.d
(not mm:ss.d). This module normalizes common OCR text so the existing filters
accept real Chinese LED boards.
"""

from __future__ import annotations

import re

from defaults import SHOT_CLOCK_FORMAT
from sports import CLOCK, PERIOD, SHOT_CLOCK, role_of

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
# A Chinese board often shows only the numeral (二), not 第二节.
_PERIOD_BARE_CN = re.compile(r"^[一二三四五六七八九十]$")


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

    Accepts 第四节 / 第4节 / 二 / Q4 / 4th / 4 / OT. A bare 一二三四五六七八九十
    is the period number (二 -> 2). Output is always plain digits (or \"OT\" /
    \"OT2\") so vMix gets one stable shape — not 4th / 45th.
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
    m = _PERIOD_BARE_CN.fullmatch(s.replace(" ", ""))
    if m:
        return _cn_digit(m.group(0))
    if re.fullmatch(r"[1-9]|10", s):
        return s
    return None


def _strip_numeric_token(token: str) -> str:
    """Leading zeros off one clock segment. An empty segment becomes \"0\"
    so \"0:00\" is not stripped down to \":00\"."""
    if not token:
        return "0"
    stripped = token.lstrip("0")
    return stripped or "0"


def strip_leading_zeros(text: str) -> str:
    """Remove leading zeros without breaking a clock.

    A pure number is stripped as a whole (\"007\" -> \"7\", \"00\" -> \"0\").
    A clock is stripped per segment: only the leftmost colon-separated part
    (\"01:16.7\" -> \"1:16.7\", \"0:00.0\" stays \"0:00.0\", \"08:05\" -> \"8:05\").
    The seconds keep their digits, so \"05\" does not become \"5\". A lone
    \"ss.d\" (\"09.8\", \"00.0\") strips the integer part only.
    """
    if not text:
        return text
    if ":" in text:
        parts = text.split(":")
        parts[0] = _strip_numeric_token(parts[0])
        return ":".join(parts)
    if text.count(".") == 1:
        whole, frac = text.split(".", 1)
        if (whole == "" or whole.isdigit()) and frac.isdigit():
            return f"{_strip_numeric_token(whole)}.{frac}"
    return text.lstrip("0") or "0"


# OCR often draws the tenth's dot as a comma, a middle dot, or (shot clock) a colon.
_TENTH_DOTS = str.maketrans(
    {
        ",": ".",
        "，": ".",
        "、": ".",
        "·": ".",
        "•": ".",
        "・": ".",
        "｡": ".",
        "．": ".",
        "：": ":",
        "；": ":",
        ";": ":",
    }
)


def _repair_game_clock_tenths(text: str) -> str:
    """Turn last-seconds OCR variants into mm:ss.d or s.d.

    Keeps a real minute:second (`12:34`, `1:02:03`) and a two-digit `12.34`
    (the colon may have been missed). Extra fractional digits are cut, not
    rounded, so 4.95 does not become 5.0.
    """
    if not text:
        return text
    # 0:04:9 — the tenth's dot read as a colon. h:mm:ss ends in two digits.
    match = re.fullmatch(r"(\d{1,2}):([0-5]\d):(\d)$", text)
    if match:
        return f"{match.group(1)}:{match.group(2)}.{match.group(3)}"
    match = re.fullmatch(r"(\d{1,2}):(\d):(\d)$", text)
    if match:
        return f"{match.group(1)}:0{match.group(2)}.{match.group(3)}"
    # 0:4.9 / 0:4.95 — one second digit, then a tenth
    match = re.fullmatch(r"(\d{1,2}):(\d)\.(\d{1,3})$", text)
    if match:
        return f"{match.group(1)}:0{match.group(2)}.{match.group(3)[0]}"
    # 0:04.95 / 1:16.70 — keep one decimal place
    match = re.fullmatch(r"(\d{1,2}):([0-5]\d)\.(\d{2,3})$", text)
    if match:
        return f"{match.group(1)}:{match.group(2)}.{match.group(3)[0]}"
    # last seconds shown as s.dd: 4.95 -> 4.9. Not 12.34.
    match = re.fullmatch(r"(\d)\.(\d{2,3})$", text)
    if match:
        return f"{match.group(1)}.{match.group(2)[0]}"
    # 4:9 / 0:5 — a tenth whose dot was read as a colon
    match = re.fullmatch(r"(\d):(\d)$", text)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    return text


def normalize_clock_text(text: str) -> str:
    """Light cleanup: fullwidth digits/colon, strip spaces, tenths in the last seconds."""
    if text is None:
        return ""
    s = str(text).strip().translate(_FULLWIDTH).translate(_TENTH_DOTS).replace(" ", "")
    # collapse duplicate separators
    s = re.sub(r":+", ":", s)
    s = re.sub(r"\.+", ".", s)
    return _repair_game_clock_tenths(s)


def normalize_shot_clock_text(text: str) -> str:
    """24秒 / shot clock: whole seconds, or one tenth (`4.9`, `0.5`).

    A comma or a single colon where the dot should be becomes `.`. A hundredth
    or millisecond digit is cut (`4.95` -> `4.9`), not rounded. Integers stay
    integers (`12` stays `12`, not `12.0`).
    """
    if text is None:
        return ""
    s = str(text).strip().translate(_FULLWIDTH).translate(_TENTH_DOTS).replace(" ", "")
    s = re.sub(r":+", ":", s)
    s = re.sub(r"\.+", ".", s)
    # 0:04.9 / 0:04:9 on a shot clock is still 4.9 seconds
    match = re.fullmatch(r"0:0*(\d{1,2})[.:](\d{1,3})", s)
    if match:
        s = f"{int(match.group(1))}.{match.group(2)}"
    # colon as the decimal point: 4:9, 14:9, 0:5
    match = re.fullmatch(r"(\d{1,2}):(\d)$", s)
    if match:
        s = f"{int(match.group(1))}.{match.group(2)}"
    # 4.95 / 0.50 — one decimal place, no rounding
    match = re.fullmatch(r"(\d{1,2})\.(\d{2,3})$", s)
    if match:
        s = f"{int(match.group(1))}.{match.group(2)[0]}"
    match = re.fullmatch(r"\.(\d)$", s)
    if match:
        s = f"0.{match.group(1)}"
    # 04.9 -> 4.9. Bare integers (08, 24) are left as read.
    match = re.fullmatch(r"0+(\d+)\.(\d)$", s)
    if match:
        s = f"{int(match.group(1))}.{match.group(2)}"
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


def is_shot_clock_box(name: str | None, settings: dict | None = None) -> bool:
    """24秒 / shot clock, including 「进攻24秒」 and a second shot clock."""
    if not name:
        return False
    if role_of(name) == SHOT_CLOCK:
        return True
    raw = str(name).strip()
    if "24秒" in raw or "进攻时间" in raw:
        return True
    folded = raw.lower()
    return "shot" in folded and "clock" in folded


def prepare_reading(name: str | None, text: str, settings: dict | None = None) -> str:
    """Normalize text before format_regex / ordinal. Period -> plain digit."""
    settings = settings or {}
    # Shot clock before the game clock: a 24秒 box is not mm:ss.
    if is_shot_clock_box(name, settings):
        return normalize_shot_clock_text(text)
    if is_clock_box(name, settings):
        return normalize_clock_text(text)
    if is_period_box(name, settings):
        cleaned = normalize_period_text(text)
        if cleaned is not None:
            return cleaned
        return (text or "").strip()
    return text if text is not None else ""


# Digits plus the separators a tenth is drawn with. Scores stay on digits only.
_TENTH_WHITELIST = "0123456789.,:·•・．"


def whitelist_for_box(name: str | None, settings: dict | None, default_for_type) -> str:
    """Char whitelist; period boxes allow Chinese 第N节 glyphs.

    Shot clocks and game clocks must be allowed to emit a decimal point.
    A digits-only whitelist drops the dot and the last seconds come out as
    `49` or nothing.
    """
    settings = settings or {}
    if is_shot_clock_box(name, settings):
        return _TENTH_WHITELIST
    if is_clock_box(name, settings):
        base = default_for_type or ""
        return "".join(dict.fromkeys(base + _TENTH_WHITELIST))
    if is_period_box(name, settings):
        return PERIOD_CHAR_WHITELIST
    return default_for_type


def clock_passes_expanded(text: str) -> bool:
    return bool(re.fullmatch(CLOCK_FORMAT_EXPANDED, text or ""))


def shot_clock_passes(text: str) -> bool:
    """One or two digits, 0-39, or one tenth (`4.9`). Not a score."""
    return bool(re.fullmatch(SHOT_CLOCK_FORMAT, text or ""))


def soft_accept_reading(
    name: str | None, text: str, settings: dict | None = None
) -> bool:
    """A tenth the box's saved format still rejects.

    Shot-clock presets up through the digits-only `^[0-3]?\\d$` reject `4.9`.
    A game clock saved as mm:ss rejects `0:04.9`. The text is already
    prepare_reading's. Scores and fouls are not accepted here.
    """
    settings = settings or {}
    if is_shot_clock_box(name, settings) and shot_clock_passes(text):
        return True
    if is_clock_box(name, settings) and clock_passes_expanded(text):
        return True
    return False

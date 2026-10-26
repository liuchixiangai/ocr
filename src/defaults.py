class FieldType:
    # Enum for the type of the field
    NUMBER = 0
    TIME = 1
    TEXT = 2


# The characters Tesseract may output for a box, by its field type (TEXT boxes
# use GENERAL_CHAR_WHITELIST). Here rather than in tesseract.py so tools such as
# the OCR evaluation harness (ocr_eval/) can apply them without importing
# Tesseract.
CHAR_WHITELISTS = {
    FieldType.NUMBER: "0123456789",
    FieldType.TIME: "0123456789:.",
}
GENERAL_CHAR_WHITELIST = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ .,;:!?-_()[]{}<>@#$%^&*+=|\\~`\"'"

# "When blank" (the box setting clear_value): what the outputs get once the box
# has been blank (or, with CLEAR_BLANK_OR_ZERO, has shown zero: 0, 00, 0:00)
# for CLEAR_AFTER_SECONDS: its last value (CLEAR_NEVER) or an empty value. In
# the order of comboBox_clearValue's items.
CLEAR_NEVER = "never"
CLEAR_BLANK = "blank"
CLEAR_BLANK_OR_ZERO = "blank_or_zero"
CLEAR_VALUES = (CLEAR_NEVER, CLEAR_BLANK, CLEAR_BLANK_OR_ZERO)
CLEAR_AFTER_SECONDS = 2.0


# 0 - Time MM:ss, MM:ss.d and ss.m
# 1 - Time MM:ss
# 2 - Time ss.m
# 3 - Time 0-59
# 4 - Shotclock 0-39, or one tenth (4.9 / 0.5)
# 5 - Score 1dd
# 6 - Score ddd
# 7 - Period 1-4
# 8 - Period d
# 9 - Alphanumeric
# 10 - Any text
# 11 - Any number
# 12 - Custom
# 24秒 / shot clock: whole seconds, or one tenth. A comma or a colon that OCR
# used for the dot is normalized to '.' before this runs. Score presets stay
# integers.
SHOT_CLOCK_FORMAT = r"^(?:[0-3]?\d|[0-3]?\d\.\d)$"
format_prefixes = [
    "^(?:(?:[0-5]?\d:[0-5]\d(?:\.\d)?)|(?:[0-5]?\d\.\d))$",  # Time MM:ss, MM:ss.d, ss.m
    "^[0-5]?\\d:[0-5]\\d$",  # Time MM:ss
    "^[0-5]?\\d\\.\\d$",  # Time ss.m
    # one or two digits: clocks show 9, not 09 (up to 0.3.0 these took exactly
    # two; storage.migrate_boxes updates the boxes that stored those)
    "^[0-5]?\\d$",  # Time 0-59
    SHOT_CLOCK_FORMAT,  # Shotclock 0-39, with a tenth in the last seconds
    "^1?\\d{1,2}$",  # Score 1dd
    "^\\d{1,3}$",  # Score ddd
    "^[1-4]$",  # Period 1-4 (cn_reading normalizes Chinese/Qn/Nth -> N)
    "^\\d{1}$",  # Period d
    "^[A-Za-z0-9]*$",  # Alphanumeric
    "^.*$",  # Any text
    "^\\d*$",  # Any number
    "^.*$",  # Custom
]


# Default values for the scoreboard
default_boxes = [
    {
        "name": "主队得分",
        "type": FieldType.NUMBER,
        "x": 0,
        "y": 0,
        "width": 120,
        "height": 100,
        "obs_source_name": "Home score",
        "format_regex": format_prefixes[5],
        "ordinal_indicator": False,
        "is_custom": False,
    },
    {
        "name": "客队得分",
        "type": FieldType.NUMBER,
        "x": 0,
        "y": 0,
        "width": 120,
        "height": 100,
        "obs_source_name": "Away score",
        "format_regex": format_prefixes[5],
        "ordinal_indicator": False,
        "is_custom": False,
    },
    {
        "name": "比赛时间",
        "type": FieldType.TIME,
        "x": 0,
        "y": 0,
        "width": 170,
        "height": 100,
        "obs_source_name": "Clock",
        "format_regex": format_prefixes[0],
        "ordinal_indicator": False,
        "is_custom": False,
    },
    {
        "name": "节次",
        "type": FieldType.NUMBER,
        "x": 0,
        "y": 0,
        "width": 50,
        "height": 80,
        "obs_source_name": "Period",
        "format_regex": format_prefixes[7],
        "ordinal_indicator": False,
        "is_custom": False,
    },
    {
        "name": "主队犯规",
        "type": FieldType.NUMBER,
        "x": 0,
        "y": 0,
        "width": 80,
        "height": 80,
        "obs_source_name": "#Home Fouls",
        "format_regex": format_prefixes[11],
        "ordinal_indicator": False,
        "is_custom": False,
    },
    {
        "name": "客队犯规",
        "type": FieldType.NUMBER,
        "x": 0,
        "y": 0,
        "width": 80,
        "height": 80,
        "obs_source_name": "#Away Fouls",
        "format_regex": format_prefixes[11],
        "ordinal_indicator": False,
        "is_custom": False,
    },
    {
        "name": "24秒",
        "type": FieldType.NUMBER,
        "x": 0,
        "y": 0,
        "width": 150,
        "height": 100,
        "obs_source_name": "shotclock",
        "format_regex": format_prefixes[4],
        "ordinal_indicator": False,
        "is_custom": False,
    },
]
default_custom_box_info = {
    "type": FieldType.NUMBER,
    "x": 0,
    "y": 0,
    "width": 150,
    "height": 100,
    "obs_source_name": "",
    "format_regex": format_prefixes[11],
    "ordinal_indicator": False,
    "is_custom": True,
}


def is_clock_like_field(name, field_type=None) -> bool:
    """True for game clock / shot clock boxes (must stay zero-latency).

    Score and foul boxes keep LED stack/debounce; countdown clocks must not,
    or the published value lags the live board by 1–2 seconds.
    """
    if field_type == FieldType.TIME:
        return True
    try:
        if int(field_type) == int(FieldType.TIME):
            return True
    except (TypeError, ValueError):
        pass
    raw = (name or "").strip()
    if not raw:
        return False
    # Chinese markers (substring — covers 「比赛时间」「进攻24秒」等)
    for marker in ("比赛时间", "24秒", "计时"):
        if marker in raw:
            return True
    # 「总计」is the game clock on some boards. Exact name only, so a box
    # called 得分总计 is not treated as a countdown.
    if raw in ("总计", "总时间"):
        return True
    n = raw.lower()
    # exact / common English names; avoid bare "time" matching "timeout"
    if n in {
        "time",
        "clock",
        "game clock",
        "game_clock",
        "gameclock",
        "shot clock",
        "shot_clock",
        "shotclock",
        "shot",
    }:
        return True
    if "shot" in n and "clock" in n:
        return True
    if "game" in n and "clock" in n:
        return True
    # "clock" alone as word, but not "timeout"
    if "timeout" in n or "time out" in n or "暂停" in raw:
        return False
    if n == "clock" or n.endswith(" clock") or n.startswith("clock "):
        return True
    return False


def default_led_stack_frames(name=None, field_type=None) -> int:
    """New boxes: clocks 0 (off), scores/fouls 4."""
    return 0 if is_clock_like_field(name, field_type) else 4


def default_led_stable_ms(name=None, field_type=None) -> int:
    """New boxes: clocks 0 (publish immediately), scores/fouls 200ms."""
    return 0 if is_clock_like_field(name, field_type) else 200


def force_clock_zero_latency(settings, name, field_type=None) -> bool:
    """Force clock/shot-clock settings to zero LED hold. Returns True if changed."""
    if not settings or not is_clock_like_field(
        name, field_type if field_type is not None else settings.get("type")
    ):
        return False
    changed = False
    if int(settings.get("led_stack_frames") or 0) != 0:
        settings["led_stack_frames"] = 0
        changed = True
    if int(settings.get("led_stable_ms") or 0) != 0:
        settings["led_stable_ms"] = 0
        changed = True
    return changed


def default_info_for_box_name(name):
    # Get the info for a box name
    for box in default_boxes:
        if box["name"] == name:
            return box
    return default_custom_box_info


# Shotclock 0-39 before tenths: one or two digits, no decimal point.
_OLD_SHOT_CLOCK_FORMAT = r"^[0-3]?\d$"


def _current_format_regex(settings, box_info):
    """The box's format, with the old digits-only shot-clock preset widened.

    Saved setups keep ``^[0-3]?\\d$`` in the settings file. That preset now
    also allows one tenth (``4.9``). Other expressions, including score
    formats, are left as stored.
    """
    if settings and "format_regex" in settings:
        regex = settings["format_regex"]
    else:
        regex = box_info["format_regex"]
    if regex == _OLD_SHOT_CLOCK_FORMAT:
        return SHOT_CLOCK_FORMAT
    return regex


def normalize_settings_dict(settings, box_info):
    # Normalize the settings dict with default values if they are not present
    if not settings:
        settings = {}
    if not box_info:
        box_info = {
            "obs_source_name": "",
            "format_regex": format_prefixes[11],
            "type": FieldType.NUMBER,
            "ordinal_indicator": False,
            "is_custom": True,
        }
    return {
        "is_custom": (
            settings["is_custom"] if "is_custom" in settings else box_info["is_custom"]
        ),
        "obs_source_name": (
            settings["obs_source_name"]
            if "obs_source_name" in settings
            else box_info["obs_source_name"]
        ),
        "format_regex": _current_format_regex(settings, box_info),
        "type": (settings["type"] if "type" in settings else box_info["type"]),
        "smoothing": (settings["smoothing"] if "smoothing" in settings else False),
        "clear_value": (
            settings["clear_value"]
            if settings.get("clear_value") in CLEAR_VALUES
            else CLEAR_NEVER
        ),
        # minimum Tesseract confidence, as a fraction (slider % / 100); 0 = off
        "conf_thresh": (settings["conf_thresh"] if "conf_thresh" in settings else 0.0),
        "cleanup_thresh": (
            settings["cleanup_thresh"] if "cleanup_thresh" in settings else 0
        ),
        "dilate": (settings["dilate"] if "dilate" in settings else 1),
        "skew": (settings["skew"] if "skew" in settings else 0),
        "vscale": (settings["vscale"] if "vscale" in settings else 10),
        "autocrop": (settings["autocrop"] if "autocrop" in settings else False),
        "skip_similar_image": (
            settings["skip_similar_image"]
            if "skip_similar_image" in settings
            else False
        ),
        "remove_leading_zeros": (
            settings["remove_leading_zeros"]
            if "remove_leading_zeros" in settings
            else False
        ),
        "rescale_patch": (
            settings["rescale_patch"] if "rescale_patch" in settings else True
        ),
        "normalize_wh_ratio": (
            settings["normalize_wh_ratio"]
            if "normalize_wh_ratio" in settings
            else False
        ),
        "invert_patch": (
            settings["invert_patch"] if "invert_patch" in settings else False
        ),
        "dot_detector": (
            settings["dot_detector"] if "dot_detector" in settings else False
        ),
        "binarization_method": (
            settings["binarization_method"] if "binarization_method" in settings else 0
        ),
        "ordinal_indicator": (
            settings["ordinal_indicator"]
            if "ordinal_indicator" in settings
            else box_info["ordinal_indicator"]
        ),
        "templatefield": (
            settings["templatefield"] if "templatefield" in settings else False
        ),
        "templatefield_text": (
            settings["templatefield_text"] if "templatefield_text" in settings else ""
        ),
        "composite_box": (
            settings["composite_box"] if "composite_box" in settings else False
        ),
        # 光为 OCR: scores/fouls keep LED stack 4 / 200ms; clocks default 0/0
        # (countdown must publish immediately — debounce looks like jump-seconds)
        "led_stack_frames": (
            int(settings["led_stack_frames"])
            if "led_stack_frames" in settings
            else default_led_stack_frames(
                (box_info or {}).get("name"),
                settings["type"]
                if "type" in settings
                else (box_info or {}).get("type"),
            )
        ),
        "led_stack_mode": (
            settings["led_stack_mode"]
            if settings.get("led_stack_mode") in ("max", "median")
            else "max"
        ),
        "led_stable_ms": (
            int(settings["led_stable_ms"])
            if "led_stable_ms" in settings
            else default_led_stable_ms(
                (box_info or {}).get("name"),
                settings["type"]
                if "type" in settings
                else (box_info or {}).get("type"),
            )
        ),
    }

"""When a shot-clock crop is actually blank, publish "24".

The 24-second lamp is often dark between possessions. That dark box should
read as a reset 24 on the list and on every output (JSON, vMix, the HTTP
API), not as the last second it showed.

A crop counts as blank only when the picture itself has nothing to read
(``tesseract.display_is_blank``: no contrast on the PP-OCR crop, or a
uniform Tesseract patch). A low-confidence read, a format rejection, a
model that is not loaded yet, a box that misses the frame, and a capture
that stopped producing frames are not blank. Those keep the last good value.

One blank sample is not enough. LED panels scan, so a single frame can
catch the digits off while the clock still shows 4.9. The fallback waits
for ``CONFIRM_BLANKS`` blank samples in a row. The next real digit
(``0``, ``0.0``, ``4.9``, ``14``, …) is published on that same sample.
Game clocks, scores and the other fields never enter this path.

The switch is the ``scoresight.json`` key ``SHOT_CLOCK_BLANK_SETTING``.
A missing key — a config saved before the switch existed — means on.
"""

from cn_reading import is_shot_clock_box
from text_detection_target import TextDetectionTargetWithResult

ResultState = TextDetectionTargetWithResult.ResultState

# scoresight.json. Absent (older configs) means on.
SHOT_CLOCK_BLANK_SETTING = "shot_clock_blank_as_24"
FALLBACK_TEXT = "24"
# One LED-scan frame of darkness must not replace a live countdown.
CONFIRM_BLANKS = 2
# Marked on the published result so a plausibility hold cannot swallow it.
FALLBACK_EXTRA = "shot_clock_blank_fallback"

_OFF_VALUES = frozenset({"0", "false", "off", "no", "否"})


def setting_enabled(value) -> bool:
    """Whether the switch is on. None (key not stored yet) is on."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in _OFF_VALUES
    return bool(value)


def is_blank_fallback(result) -> bool:
    """True when this published result is the blank-board fallback, not an OCR 24."""
    extras = getattr(result, "extras", None) or {}
    return bool(extras.get(FALLBACK_EXTRA))


def plausibility_skip_names(results) -> set[str]:
    """Box names a plausibility hold must not replace.

    The shot clock is not judged today. This still keeps a blank-board 24
    on the outputs if a later rule starts holding shot-clock jumps.
    """
    return {result.name for result in results if is_blank_fallback(result)}


def is_visual_blank_result(result) -> bool:
    """True only when the crop itself is dark, not when OCR failed.

    ``display_blank`` is set in ``tesseract`` when the gray crop has no
    contrast (PP-OCR) or the binarized patch is uniform (Tesseract). A
    low-confidence digit, a format rejection, an empty string from a
    still-visible crop, skip-similar, and "model not ready" leave that
    flag unset. Those must keep the last second, including 4.9.
    """
    if result is None or bool(getattr(result, "unchanged_image", False)):
        return False
    if not bool(getattr(result, "display_blank", False)):
        return False
    state = getattr(result, "state", None)
    if state is not None and state != ResultState.Empty:
        return False
    return not str(getattr(result, "text", "") or "").strip()


def _reset_streak(target) -> None:
    target.shot_clock_blank_streak = 0


def _forget_previous_image(target) -> None:
    """Drop the image and the smoothers of the value we are replacing.

    Otherwise skip-similar would treat the digits coming back as "the same
    picture" and republish 24, and a smoother could vote the new digit down.
    """
    target.last_image = None
    smoother = getattr(target, "ocrResultPerCharacterSmoother", None)
    if smoother is not None:
        smoother.clear()
    stabilizer = getattr(target, "led_value_stabilizer", None)
    if stabilizer is not None:
        stabilizer.clear()
    target.blank_since = None


def apply_shot_clock_blank(target, result, enabled: bool):
    """``(text, state)`` to publish instead of ``result``, or None.

    None leaves the capture thread on its normal path (including "When
    blank"). A pair is the fallback ``24``: Success the first time the
    blank run is long enough, then SameNoChange while the crop stays blank.
    """
    name = getattr(target, "name", None)
    settings = getattr(target, "settings", None)
    if not enabled or not is_shot_clock_box(name, settings):
        _reset_streak(target)
        return None

    if not is_visual_blank_result(result):
        # A real digit, a rejected read, or an empty read that is not a dark
        # crop. None of those are the lamp being off.
        _reset_streak(target)
        return None

    streak = int(getattr(target, "shot_clock_blank_streak", 0) or 0) + 1
    target.shot_clock_blank_streak = streak
    if streak < CONFIRM_BLANKS:
        return None

    _forget_previous_image(target)
    previous = target.last_text
    target.last_text = FALLBACK_TEXT
    state = (
        ResultState.SameNoChange if previous == FALLBACK_TEXT else ResultState.Success
    )
    return FALLBACK_TEXT, state

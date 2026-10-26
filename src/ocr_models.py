"""The OCR models a user can choose (Inspector > All boxes > OCR model), and how
the choice is stored ("ocr_model" in the settings file).

Stored values:
  "ppocr:auto"   PP-OCR (automatic): the PP-OCR engine (ppocr_engine.py) with
                 its accurate model while it keeps up with Detections / s, and
                 its fast model when it doesn't
  "ppocr:small"  PP-OCR (accurate), never switched
  "ppocr:tiny"   PP-OCR (fast)
  0, 1, 2, 3     the bundled Tesseract models, by their index since 0.1
                 (tesseract.TextDetector.OcrModelIndex): Daktronics, General
                 Scoreboard, General Fonts (English), General Scoreboard Large
  other strings  the path of a Tesseract .traineddata file (Load external
                 model...)

The Tesseract values keep their 0.1/0.2 meaning, so a setup made with an older
version keeps its model (and an older version reading a PP-OCR id falls back
to General Scoreboard). Every version stored the value when a source was
opened, so a settings file without one is a new setup: it gets DEFAULT_MODEL,
PP-OCR (automatic), which is stored when its first source opens. Where PP-OCR
can't be used (from source without scripts/fetch_models.py, or without
onnxruntime) the app reads with FALLBACK_MODEL instead, without storing it, so
PP-OCR takes over once it is installed.
"""

from dataclasses import dataclass

import ppocr_engine
from storage import SETTINGS_FILE, fetch_data

SETTING = "ocr_model"

PPOCR_AUTO = ppocr_engine.AUTO
PPOCR_ACCURATE = ppocr_engine.SMALL
PPOCR_FAST = ppocr_engine.TINY
# tesseract.TextDetector.OcrModelIndex
DAKTRONICS = 0
SCOREBOARD_GENERAL = 1
GENERAL_ENGLISH = 2
SCOREBOARD_GENERAL_LARGE = 3

DEFAULT_MODEL = PPOCR_AUTO
FALLBACK_MODEL = SCOREBOARD_GENERAL


@dataclass(frozen=True)
class Choice:
    """An item of the OCR model list: its stored value and English name (the
    window translates the PP-OCR names; the Tesseract models' names are names)."""

    value: int | str
    name: str


# In the order of the list; Load external model... comes last.
CHOICES = (
    Choice(PPOCR_AUTO, "PP-OCR (automatic)"),
    Choice(PPOCR_ACCURATE, "PP-OCR (accurate)"),
    Choice(PPOCR_FAST, "PP-OCR (fast)"),
    Choice(DAKTRONICS, "Tesseract: Daktronics"),
    Choice(SCOREBOARD_GENERAL, "Tesseract: General Scoreboard"),
    Choice(GENERAL_ENGLISH, "Tesseract: General Fonts (English)"),
    Choice(SCOREBOARD_GENERAL_LARGE, "Tesseract: General Scoreboard Large"),
)


def is_ppocr(model) -> bool:
    return ppocr_engine.is_ppocr(model)


def is_builtin_tesseract(model) -> bool:
    return (
        isinstance(model, int)
        and not isinstance(model, bool)
        and DAKTRONICS <= model <= SCOREBOARD_GENERAL_LARGE
    )


def is_external(model) -> bool:
    """A stored .traineddata path."""
    return isinstance(model, str) and bool(model) and not is_ppocr(model)


def stored_model():
    """The stored choice, or DEFAULT_MODEL for a new setup (nothing stored)."""
    value = fetch_data(SETTINGS_FILE, SETTING, None)
    return DEFAULT_MODEL if value is None else value


def is_stored() -> bool:
    return fetch_data(SETTINGS_FILE, SETTING, None) is not None


def unavailable_reason(model) -> str | None:
    """Why a model can't be used right now, or None (Tesseract models and
    paths are checked when they load)."""
    if is_ppocr(model):
        return ppocr_engine.unavailable_reason(model)
    return None


def effective_model(model):
    """The model the boxes are read with for a stored choice: FALLBACK_MODEL
    instead of a PP-OCR model that can't be used."""
    if is_ppocr(model) and unavailable_reason(model) is not None:
        return FALLBACK_MODEL
    return model


def choice_name(model) -> str | None:
    for choice in CHOICES:
        if choice.value == model and type(choice.value) is type(model):
            return choice.name
    return None

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

The Tesseract indexes keep their 0.1/0.2 meaning (an older version reading a
PP-OCR id falls back to General Scoreboard). Every version stored the value
when a source was opened, so a settings file without one is a new setup: it
gets DEFAULT_MODEL, PP-OCR (automatic), which is stored when its first source
opens. Where PP-OCR can't be used (from source without scripts/fetch_models.py,
or without onnxruntime) the app reads with FALLBACK_MODEL instead, without
storing it, so PP-OCR takes over once it is installed.

光为 OCR also rewrites an already-stored Tesseract General Scoreboard (1) or
General Scoreboard Large (3) to PP-OCR (automatic), once, when the weights can
be used (migrate_legacy_general_scoreboard). A source open on older builds
stored those two for people who never opened the model list, and they miss
stylized broadcast digits (CBA / red score bugs). Daktronics (0), General
English (2), an external .traineddata and a PP-OCR choice are not rewritten.
The flag ocr_model_general_migrated records that this was decided, so choosing
Tesseract again afterwards is kept.
"""

from dataclasses import dataclass

import ppocr_engine
from sc_logging import logger
from storage import SETTINGS_FILE, fetch_data, store_data

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
# Set once migrate_legacy_general_scoreboard has decided. A later switch back
# to Tesseract General Scoreboard is then left alone.
MIGRATED_SETTING = "ocr_model_general_migrated"


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


def is_legacy_general_scoreboard(model) -> bool:
    """Tesseract General Scoreboard (1) or General Scoreboard Large (3).

    Older builds stored one of these when a source opened, including for
    people who never opened the OCR model list. Daktronics, General English,
    a bool (True == 1) and anything that isn't an int are not these models.
    """
    return (
        isinstance(model, int)
        and not isinstance(model, bool)
        and model in (SCOREBOARD_GENERAL, SCOREBOARD_GENERAL_LARGE)
    )


def migrate_legacy_general_scoreboard() -> bool:
    """Move a stored General Scoreboard or Large model to PP-OCR (automatic).

    Runs once. Returns True when ``ocr_model`` was rewritten.

    When those two models are stored and PP-OCR can be used (weights and
    onnxruntime), they become DEFAULT_MODEL and MIGRATED_SETTING is set.
    Daktronics, General English, an external path, an existing PP-OCR choice
    and a new setup (nothing stored) are not rewritten; the flag is set so a
    later deliberate Tesseract choice is kept. When General Scoreboard or
    Large is stored but PP-OCR can't be used yet, nothing is written and the
    next start tries again.
    """
    if fetch_data(SETTINGS_FILE, MIGRATED_SETTING, False):
        return False
    stored = fetch_data(SETTINGS_FILE, SETTING, None)
    if is_legacy_general_scoreboard(stored):
        reason = unavailable_reason(PPOCR_AUTO)
        if reason is not None:
            logger.info(
                "OCR model %s left unchanged; PP-OCR is not usable yet (%s). "
                "It will move to PP-OCR (automatic) once the models can be used.",
                choice_name(stored) or stored,
                reason,
            )
            return False
        store_data(SETTINGS_FILE, SETTING, DEFAULT_MODEL)
        store_data(SETTINGS_FILE, MIGRATED_SETTING, True)
        logger.info(
            "OCR model changed from %s to PP-OCR (automatic). General "
            "Scoreboard and General Scoreboard Large were stored when a source "
            "opened on older builds and miss stylized broadcast digits. A later "
            "choice of Tesseract is kept.",
            choice_name(stored) or stored,
        )
        return True
    store_data(SETTINGS_FILE, MIGRATED_SETTING, True)
    return False

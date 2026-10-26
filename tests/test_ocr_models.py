"""Real OCR through TextDetector with each bundled model on synthetic images.

The assertions are calibrated to what each model reliably reads on these
renderers (Tesseract 5.5): the models were trained on real scoreboards, so e.g.
daktronics is only checked for loading and whitelist behavior. Boxes use the
same normalized default settings and global Otsu binarization as the app.
"""

import numpy as np
import pytest

from defaults import FieldType, default_info_for_box_name, normalize_settings_dict
from synthetic_images import binarize, hershey, seven_segment
from text_detection_target import TextDetectionTarget, TextDetectionTargetWithResult

pytestmark = pytest.mark.ocr
pytest.importorskip("tesserocr")

from tesseract import TextDetector  # noqa: E402

Model = TextDetector.OcrModelIndex

SUCCESS = TextDetectionTargetWithResult.ResultState.Success
EMPTY = TextDetectionTargetWithResult.ResultState.Empty

SCORES = ["0", "3", "7", "12", "45", "88", "105", "199"]
TIMES = ["12:34", "0:59", "9.5", "45.1"]


@pytest.fixture(scope="module")
def detector_for():
    """detector_for(model_index) -> a TextDetector with that model loaded."""
    detectors = {}

    def _get(model_index):
        if model_index not in detectors:
            detector = TextDetector(model_index)
            detector.setOcrModel(model_index)
            detectors[model_index] = detector
        return detectors[model_index]

    yield _get
    for detector in detectors.values():
        if detector.api is not None:
            detector.api.End()


def read(detector, image, box_name, **settings):
    target = TextDetectionTarget(
        0,
        0,
        image.shape[1],
        image.shape[0],
        box_name,
        normalize_settings_dict(settings, default_info_for_box_name(box_name)),
        [],
    )
    [result] = detector.detect_multi_text(binarize(image), image, [target])
    return result


ALL_MODELS = [
    "DAKTRONICS",
    "SCOREBOARD_GENERAL",
    "GENERAL_ENGLISH",
    "SCOREBOARD_GENERAL_LARGE",
]


@pytest.mark.parametrize("model", ALL_MODELS)
def test_model_loads_and_runs(detector_for, model):
    detector = detector_for(getattr(Model, model))
    assert detector.api is not None
    for image in (np.zeros((60, 120), np.uint8), seven_segment("45")):
        result = read(detector, image, "Home Score")
        assert isinstance(result.text, str)
        assert isinstance(result.state, TextDetectionTargetWithResult.ResultState)


def test_eng_blank_box_is_empty(detector_for):
    # Note: the scoreboard_* models hallucinate digits (e.g. "34") on an
    # all-background box, so this only holds for eng.
    detector = detector_for(Model.GENERAL_ENGLISH)
    result = read(detector, np.zeros((60, 120), np.uint8), "Home Score")
    assert (result.text, result.state) == ("", EMPTY)


def test_default_detector_uses_scoreboard_general(text_detector):
    result = read(text_detector, seven_segment("12"), "Home Score")
    assert (result.text, result.state) == ("12", SUCCESS)


# --- scoreboard_general: 7-segment displays -------------------------------------


@pytest.mark.parametrize("value", ["3", "12", "45"])
def test_scoreboard_general_reads_required_scores(detector_for, value):
    detector = detector_for(Model.SCOREBOARD_GENERAL)
    result = read(detector, seven_segment(value), "Home Score")
    assert (result.text, result.state) == (value, SUCCESS)


def test_scoreboard_general_score_accuracy(detector_for):
    detector = detector_for(Model.SCOREBOARD_GENERAL)
    reads = {v: read(detector, seven_segment(v), "Home Score").text for v in SCORES}
    correct = [v for v, text in reads.items() if text == v]
    # all 8 read correctly on Tesseract 5.5.2; leave headroom for other builds
    assert len(correct) >= 6, reads


def test_scoreboard_general_reads_clock(detector_for):
    detector = detector_for(Model.SCOREBOARD_GENERAL)
    result = read(detector, seven_segment("12:34"), "Time")
    assert (result.text, result.state) == ("12:34", SUCCESS)


# --- scoreboard_general_large --------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "box"),
    [
        ("12", "Home Score"),
        ("45", "Home Score"),
        ("88", "Away Score"),
        ("12:34", "Time"),
    ],
)
def test_scoreboard_general_large_reads(detector_for, value, box):
    detector = detector_for(Model.SCOREBOARD_GENERAL_LARGE)
    result = read(detector, seven_segment(value), box)
    assert (result.text, result.state) == (value, SUCCESS)


# --- eng: regular fonts ----------------------------------------------------------------


@pytest.mark.parametrize("value", SCORES)
def test_eng_reads_scores(detector_for, value):
    detector = detector_for(Model.GENERAL_ENGLISH)
    result = read(detector, hershey(value), "Home Score")
    assert (result.text, result.state) == (value, SUCCESS)


@pytest.mark.parametrize("value", TIMES)
def test_eng_reads_times(detector_for, value):
    detector = detector_for(Model.GENERAL_ENGLISH)
    result = read(detector, hershey(value), "Time")
    assert (result.text, result.state) == (value, SUCCESS)


# --- field type -> character whitelist ---------------------------------------------------


@pytest.mark.parametrize("word", ["HOME", "GUEST"])
def test_text_field_type_reads_letters(detector_for, word):
    detector = detector_for(Model.GENERAL_ENGLISH)
    result = read(
        detector, hershey(word), "Team", type=FieldType.TEXT, format_regex="^.*$"
    )
    assert (result.text, result.state) == (word, SUCCESS)


@pytest.mark.parametrize("model", ALL_MODELS)
def test_number_and_time_whitelists(detector_for, model):
    detector = detector_for(getattr(Model, model))
    for image in (hershey("HOME"), seven_segment("45"), hershey("12:34")):
        number = read(detector, image, "Team", format_regex="^.*$").text
        assert set(number) <= set("0123456789 "), number
        clock = read(detector, image, "Time").text
        assert set(clock) <= set("0123456789:. "), clock

"""ocr_eval's Tesseract adapter: ScoreSight's real pipeline on dataset crops."""

import pytest

pytestmark = pytest.mark.ocr
pytest.importorskip("tesserocr")

from ocr_eval.dataset import load_dataset  # noqa: E402
from ocr_eval.engines.tesseract_scoresight import (  # noqa: E402
    TUNED,
    ScoreSightTesseract,
    dark_on_light,
    scale_to_height,
)
from ocr_eval.fields import scoresight_postfilter  # noqa: E402
from ocr_eval.runner import RunOptions, load_images, run_engine  # noqa: E402


def test_variants_cover_every_bundled_model_with_and_without_binarization():
    assert set(ScoreSightTesseract.variants) == {
        f"{model}{suffix}"
        for model in (
            "scoreboard_general",
            "scoreboard_general_large",
            "daktronics",
            "eng",
        )
        for suffix in ("", "-nobin")
    } | {"tuned"}
    engine = ScoreSightTesseract("eng-nobin")
    assert engine.model == "eng"
    assert engine.overrides == {"binarization_method": 1}
    assert (engine.height, engine.polarity) == (None, False)
    assert ScoreSightTesseract.unavailable_reason() is None


def test_tuned_variant_uses_the_settings_chosen_on_dev():
    engine = ScoreSightTesseract("tuned")
    assert engine.model == TUNED["model"]
    assert engine.overrides == TUNED["overrides"]
    assert (engine.height, engine.polarity) == (TUNED["height"], TUNED["polarity"])
    # what tune_tesseract.py chose (ocr_eval/results/2026-10-11/tesseract_tuning.md)
    assert TUNED == {
        "model": "eng",
        "height": 24,
        "polarity": True,
        "overrides": {"binarization_method": 1, "dilate": 0, "rescale_patch": False},
    }


def test_dark_on_light_and_scale_to_height():
    import numpy as np

    light_text = np.zeros((10, 30), np.uint8)
    light_text[3:7, 5:25:4] = 255  # a few bright strokes on a dark board
    assert (dark_on_light(light_text) == 255 - light_text).all()
    dark_text = 255 - light_text
    assert dark_on_light(dark_text) is dark_text
    assert scale_to_height(dark_text, 24).shape == (24, 72)
    assert scale_to_height(dark_text, 5).shape == (5, 15)
    assert scale_to_height(dark_text, 10) is dark_text


@pytest.fixture(scope="module")
def synthetic():
    dataset = load_dataset("synthetic_7seg")
    return dataset, load_images(dataset)


def test_postfilter_scoring_matches_the_pipelines_own_filter(synthetic):
    dataset, images = synthetic
    record = run_engine(
        ScoreSightTesseract,
        "scoreboard_general",
        dataset,
        images,
        RunOptions(),
        log=None,
    )
    assert record["status"] == "ok", record.get("traceback")
    by_id = {s.id: s for s in dataset.samples}
    for prediction in record["predictions"]:
        sample = by_id[prediction["id"]]
        state = prediction["info"]["state"]
        pipeline_output = prediction["text"] if state == "Success" else ""
        assert (
            scoresight_postfilter(prediction["text"], sample.field_type)
            == pipeline_output
        )
        if prediction["text"]:
            assert 0.0 <= prediction["conf"] <= 1.0
    # scoreboard_general reads most rendered 7-segment displays (calibrated on
    # Tesseract 5.5; see test_ocr_models.py)
    assert record["scores"]["raw"]["exact"]["value"] > 0.4
    assert record["engine_info"]["model"] == "scoreboard_general"
    assert record["latency"]["single"]["n"] == len(dataset)


def test_reads_crops_of_any_size_and_colour(synthetic):
    import numpy as np

    engine = ScoreSightTesseract("scoreboard_general-nobin")
    engine.load()
    try:
        crops = [np.zeros((1, 1, 3), np.uint8), np.full((5, 300, 3), 255, np.uint8)]
        for crop in crops:
            crop.flags.writeable = False
        results = engine.recognize(crops, ["score", "text"])
        assert len(results) == 2
        assert all(isinstance(r.text, str) for r in results)
    finally:
        engine.close()

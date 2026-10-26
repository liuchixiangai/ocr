"""ocr_eval's ppocr-app adapter: the app's own PP-OCR engine through its
pipeline, which must read exactly as the evaluation's PP-OCR adapter."""

import gzip
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("tesserocr")  # the pipeline imports it

from ocr_eval.engines import ppocr as eval_ppocr  # noqa: E402
from ocr_eval.engines.ppocr_scoresight import (  # noqa: E402
    GAP,
    MATCHES,
    MODEL_IDS,
    ScoreSightPPOCR,
    frame_of,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINES = REPO_ROOT / "ocr_eval" / "baselines"


def _load(path: Path) -> dict:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _runs(results: dict) -> dict:
    return {(run["dataset"], run["key"]): run for run in results["runs"]}


# Confidences are stored with 4 decimals, and onnxruntime's kernels differ
# slightly between CPUs: a value at a rounding edge (0.99445) comes out 0.9944
# on one runner and 0.9945 on another.
CONFIDENCE_TOLERANCE = 2e-4
# On a Mac (arm64), onnxruntime's kernels give results that depend on the
# batch: the app reads a frame's crops together, the evaluation in its own
# batches. A crop's confidence can differ (0.8861 against 0.9998 for one
# synthetic clock), and a borderline crop can read differently ('02:37' against
# '02:30' at 0.81; '0' at 0.17 against nothing). There the confidences aren't
# compared and up to 1 crop in 50 may read differently: a change in how the app
# reads would change many.
MAC = sys.platform == "darwin"


def _same_predictions(
    app_run: dict, eval_run: dict, tolerance: float | None = CONFIDENCE_TOLERANCE
) -> list[str]:
    """The crops the two runs read differently (the app strips its text), each
    with both readings, so a failure on another machine says what it read."""
    app = {p["id"]: p for p in app_run["predictions"]}
    other = {p["id"]: p for p in eval_run["predictions"]}
    differences = sorted(set(app) ^ set(other))
    for crop_id in sorted(set(app) & set(other)):
        a, b = app[crop_id], other[crop_id]
        if (a["text"] or "") != (b["text"] or "").strip() or (
            a["text"]
            and tolerance is not None
            and abs(a["conf"] - b["conf"]) > tolerance
        ):
            differences.append(
                f"{crop_id}: app {a['text']!r} ({a['conf']}, "
                f"{(a.get('info') or {}).get('state')}), "
                f"eval {b['text']!r} ({b['conf']})"
            )
    return differences


def test_the_variants_are_the_apps_models():
    import ppocr_engine

    assert ScoreSightPPOCR.variants == ("accurate", "fast")
    assert MODEL_IDS == {"accurate": ppocr_engine.SMALL, "fast": ppocr_engine.TINY}
    # the same pinned models as the evaluation's adapter
    spec = importlib.util.spec_from_file_location(
        "fetch_models", REPO_ROOT / "scripts" / "fetch_models.py"
    )
    fetch_models = importlib.util.module_from_spec(spec)
    sys.modules["fetch_models"] = fetch_models
    spec.loader.exec_module(fetch_models)
    pins = {model.repo: model.revision for model in fetch_models.MODELS}
    for variant, eval_variant in MATCHES.items():
        model = eval_ppocr.MODELS[eval_variant.removesuffix("-wl")]
        assert pins[model.repo] == model.revision, variant
        folder = ppocr_engine.MODELS[MODEL_IDS[variant]].folder
        assert model.repo.split("/")[1].startswith(folder)


def test_a_batch_is_one_frame_of_crops():
    crops = [
        np.full((10, 20, 3), 1, np.uint8),
        np.full((30, 5, 3), 2, np.uint8),
        np.full((1, 1, 3), 3, np.uint8),
    ]
    frame, rects = frame_of(crops)
    assert frame.shape == (30 + 2 * GAP, 26 + 4 * GAP, 3)
    for crop, (x, y, w, h) in zip(crops, rects):
        assert (w, h) == crop.shape[1::-1]
        assert np.array_equal(frame[y : y + h, x : x + w], crop)
    # nothing else: the crops don't touch
    assert frame.sum() == sum(int(c.sum()) for c in crops)


def test_the_app_baseline_matches_the_evaluations():
    """ppocr-app-ci.json (CI's baseline for the app's engine) has the same
    predictions as ppocr-ci.json (the evaluation adapter's) on every crop."""
    app = _runs(_load(BASELINES / "ppocr-app-ci.json"))
    other = _runs(_load(BASELINES / "ppocr-ci.json"))
    assert {dataset for dataset, _ in app} == {
        "textocr_scoreboards",
        "commons_scoreboards",
        "synthetic_7seg",
    }
    for (dataset, key), run in app.items():
        variant = key.split(":")[1]
        eval_run = other[(dataset, f"ppocr:{MATCHES[variant]}")]
        assert _same_predictions(run, eval_run) == [], (dataset, key)
        for scoring in ("raw", "postfilter"):
            assert (
                run["scores"][scoring]["exact"]["k"]
                == eval_run["scores"][scoring]["exact"]["k"]
            )


@pytest.mark.ppocr
@pytest.mark.parametrize("variant", ["accurate", "fast"])
def test_the_app_reads_like_the_evaluation(variant):
    from ocr_eval.dataset import load_dataset
    from ocr_eval.runner import RunOptions, load_images, run_engine

    baseline = _runs(_load(BASELINES / "ppocr-ci.json"))
    for name, limit in (("synthetic_7seg", None), ("commons_scoreboards", 160)):
        dataset = load_dataset(name)
        if limit is not None:
            dataset.samples = dataset.samples[:limit]
        record = run_engine(
            ScoreSightPPOCR,
            variant,
            dataset,
            load_images(dataset),
            RunOptions(single_latency_samples=2, warmup=1),
            log=None,
        )
        assert record["status"] == "ok", record.get("traceback")
        eval_run = dict(baseline[(name, f"ppocr:{MATCHES[variant]}")])
        ids = {sample.id for sample in dataset.samples}
        eval_run["predictions"] = [p for p in eval_run["predictions"] if p["id"] in ids]
        if MAC:
            differences = _same_predictions(record, eval_run, None)
            assert len(differences) <= max(1, len(dataset.samples) // 50), (
                name,
                differences,
            )
        else:
            assert _same_predictions(record, eval_run) == [], name
        for prediction in record["predictions"]:
            assert prediction["info"]["state"] in ("Success", "FailedFilter", "Empty")

"""ocr_eval's OnnxTR adapter (docTR recognizers): variants, the decoders and
whitelist restriction with a stand-in model, and ScoreSight's preprocessing.
Needs neither OnnxTR nor a model download; the test against the real library
runs only when OnnxTR and the small CRNN model are already installed."""

import os
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from ocr_eval.engines import EngineUnavailable, discover  # noqa: E402
from ocr_eval.engines import onnxtr as adapter  # noqa: E402
from ocr_eval.engines.onnxtr import (  # noqa: E402
    MODELS,
    OnnxTR,
    allowed_classes,
    decode_attention,
    decode_ctc,
    split_variant,
)
from ocr_eval.fields import whitelist  # noqa: E402

VOCAB = "0123456789IOl:.ab"
END = len(VOCAB)  # the CTC blank / <eos> class


def one_hot(rows, confident=8.0, runner_up=None):
    """Logits (len(rows) x classes) with each row's class most likely, and an
    optional runner-up per row (another class, a little less likely)."""
    logits = np.zeros((len(rows), len(VOCAB) + 1), dtype=np.float32)
    for i, index in enumerate(rows):
        logits[i, index] = confident
        if runner_up and runner_up[i] is not None:
            logits[i, runner_up[i]] = confident - 1.0
    return logits


def idx(char):
    return VOCAB.index(char)


def test_variants_and_discovery():
    assert len(OnnxTR.variants) == len(set(OnnxTR.variants)) == 4 * len(MODELS)
    for variant in OnnxTR.variants:
        model, suffix = split_variant(variant)
        assert model in MODELS and model + suffix == variant
        engine = OnnxTR(variant)
        assert engine.binarize == ("-bin" in suffix)
        assert engine.restrict == suffix.endswith("-wl")
        assert engine.model is MODELS[model]
    assert split_variant("parseq-int8-bin-wl") == ("parseq-int8", "-bin-wl")
    assert split_variant("viptr_tiny-wl") == ("viptr_tiny", "-wl")
    # the quantized models are opt-in
    assert set(OnnxTR.default_variants) == {
        v for v in OnnxTR.variants if not MODELS[split_variant(v)[0]].int8
    }
    assert {"parseq", "crnn_mobilenet_v3_small", "viptr_tiny", "vitstr_small"} <= {
        split_variant(v)[0] for v in OnnxTR.default_variants
    }
    registry = discover()
    assert "ocr_eval.engines.onnxtr" not in registry.broken
    assert registry.get("onnxtr") is OnnxTR
    assert any(r.startswith("onnxtr[cpu-headless]==") for r in OnnxTR.requirements)


def test_unavailable_without_onnxtr(monkeypatch):
    real_find_spec = adapter.importlib.util.find_spec

    def find_spec(name, *args):
        return None if name in ("onnxtr", "onnxruntime") else real_find_spec(name)

    monkeypatch.setattr(adapter.importlib.util, "find_spec", find_spec)
    reason = OnnxTR.unavailable_reason()
    assert "onnxtr" in reason and "pip install onnxtr[cpu-headless]" in reason

    for module in ("onnxruntime", "onnxtr", "onnxtr.models"):
        monkeypatch.setitem(sys.modules, module, None)
    with pytest.raises(EngineUnavailable, match="OnnxTR"):
        OnnxTR("crnn_mobilenet_v3_small").load()


def test_thread_setting(monkeypatch):
    monkeypatch.delenv(adapter.THREADS_ENV, raising=False)
    assert adapter.onnx_threads() == 0
    monkeypatch.setenv(adapter.THREADS_ENV, "2")
    assert adapter.onnx_threads() == 2
    monkeypatch.setenv(adapter.THREADS_ENV, "many")
    with pytest.raises(EngineUnavailable):
        adapter.onnx_threads()


def test_allowed_classes_keep_the_end_token():
    allowed = allowed_classes(VOCAB, "0123456789")
    assert allowed.shape == (len(VOCAB) + 1,)
    assert allowed[END]
    assert [VOCAB[i] for i in np.flatnonzero(allowed[:END])] == list("0123456789")
    # characters outside the vocabulary are simply unavailable
    assert not allowed_classes(VOCAB, " xyz")[:END].any()


def test_ctc_decoding_collapses_repeats_and_drops_blanks():
    frames = [idx("1"), idx("1"), END, idx("1"), idx("2"), END, END]
    text, confidence = decode_ctc(one_hot(frames), VOCAB)
    assert text == "112"
    assert 0.99 < confidence <= 1.0
    assert decode_ctc(one_hot([END, END]), VOCAB) == ("", pytest.approx(1.0, abs=0.01))
    assert decode_ctc(np.zeros((0, END + 1), np.float32), VOCAB) == ("", 0.0)


def test_ctc_whitelist_takes_the_best_allowed_class():
    # "I" then "O" are most likely, "1" and "0" the runners-up
    frames = [idx("I"), END, idx("O")]
    logits = one_hot(frames, runner_up=[idx("1"), None, idx("0")])
    assert decode_ctc(logits, VOCAB)[0] == "IO"
    text, confidence = decode_ctc(logits, VOCAB, allowed_classes(VOCAB, "0123456789"))
    assert text == "10"
    # the confidence is the chosen class's probability among all classes
    assert confidence < decode_ctc(logits, VOCAB)[1]


def test_attention_decoding_stops_at_eos():
    positions = [idx("1"), idx("2"), END, idx("3"), END]
    text, confidence = decode_attention(one_hot(positions), VOCAB)
    assert text == "12"
    assert 0.99 < confidence <= 1.0
    assert decode_attention(one_hot([END, idx("5")]), VOCAB) == ("", 0.0)


def test_attention_whitelist():
    positions = [idx("l"), idx(":"), idx("O"), idx("a"), END]
    logits = one_hot(positions, runner_up=[idx("1"), None, idx("0"), END, None])
    assert decode_attention(logits, VOCAB)[0] == "l:Oa"
    clock = allowed_classes(VOCAB, whitelist("clock"))
    # "a" is not allowed: the runner-up <eos> ends the reading
    assert decode_attention(logits, VOCAB, clock)[0] == "1:0"


class FakeModel:
    """Stands in for an OnnxTR recognition model: the logits of a crop are
    looked up by its (uniform) pixel value."""

    vocab = VOCAB

    def __init__(self, logits_by_value, decoder):
        self.logits_by_value = logits_by_value
        self.decode = decode_ctc if decoder == "ctc" else decode_attention
        self.calls = []

    def __call__(self, batch, return_model_output=False):
        self.calls.append((len(batch), return_model_output))
        logits = np.stack(
            [self.logits_by_value[int(image[0, 0, 0])] for image in batch]
        )
        out = {"preds": [self.decode(row, self.vocab) for row in logits]}
        if return_model_output:
            out["out_map"] = logits
        return out


class FakePredictor:
    split_wide_crops = False

    def __init__(self, model):
        self.model = model

    def pre_processor(self, crops):
        # batches of two, the images unchanged
        return [crops[i : i + 2] for i in range(0, len(crops), 2)]


def crop(value, height=20, width=40):
    image = np.full((height, width, 3), value, np.uint8)
    image.flags.writeable = False
    return image


@pytest.mark.parametrize("model", ["crnn_mobilenet_v3_small", "parseq"])
def test_recognize_restricts_each_crop_to_its_field_type(model):
    decoder = MODELS[model].decoder
    # value 10: "IO" (runners-up "1", "0"); value 20: "1:3O" (runner-up "0"),
    # over the same number of frames / positions
    if decoder == "ctc":
        io = one_hot(
            [idx("I"), END, idx("O"), END], runner_up=[idx("1"), None, idx("0"), None]
        )
        clock = one_hot(
            [idx("1"), idx(":"), idx("3"), idx("O")],
            runner_up=[None, None, None, idx("0")],
        )
    else:
        io = one_hot(
            [idx("I"), idx("O"), END, END, END],
            runner_up=[idx("1"), idx("0"), None, None, None],
        )
        clock = one_hot(
            [idx("1"), idx(":"), idx("3"), idx("O"), END],
            runner_up=[None, None, None, idx("0"), None],
        )
    fake = FakeModel({10: io, 20: clock}, decoder)

    free = OnnxTR(model)
    free.use_predictor(FakePredictor(fake))
    crops = [crop(10), crop(10), crop(20), crop(10), crop(20)]
    types = ["score", "text", "clock", "number", "text"]
    assert [r.text for r in free.recognize(crops, types)] == [
        "IO",
        "IO",
        "1:3O",
        "IO",
        "1:3O",
    ]
    assert all(call == (2, False) or call == (1, False) for call in fake.calls)

    restricted = OnnxTR(f"{model}-wl")
    restricted.use_predictor(FakePredictor(fake))
    results = restricted.recognize(crops, types)
    assert [r.text for r in results] == ["10", "IO", "1:30", "10", "1:3O"]
    assert results[0].info == {"unrestricted": "IO"}
    assert results[1].info is None
    assert all(0.0 <= r.confidence <= 1.0 for r in results)
    assert fake.calls[-1][1] is True


def test_info_without_a_model():
    info = OnnxTR("vitstr_small-bin-wl").info()
    assert info["model"] == "vitstr_small"
    assert info["preprocessing"].startswith("ScoreSight")
    assert info["whitelist_decoding"] is True
    assert info["decoder"] == "attention"
    assert info["size"] == "21.4M parameters"
    assert info["batch_size"] == OnnxTR.batch_size


# --- ScoreSight's preprocessing (the -bin variants): needs src/tesseract.py --


@pytest.mark.ocr
def test_binarized_patch_is_what_scoresight_gives_tesseract(monkeypatch):
    pytest.importorskip("tesserocr")
    from ocr_eval.dataset import load_dataset
    from ocr_eval.engines.tesseract_scoresight import ScoreSightTesseract

    tess = ScoreSightTesseract("scoreboard_general")
    tess.load()
    import tesseract

    seen = []
    real_fromarray = tesseract.Image.fromarray

    def record(array, *args, **kwargs):
        seen.append(np.array(array))
        return real_fromarray(array, *args, **kwargs)

    monkeypatch.setattr(tesseract.Image, "fromarray", record)
    try:
        crops, types = [], []
        for name, limit in (("synthetic_7seg", 12), ("textocr_scoreboards", 30)):
            dataset = load_dataset(name, limit=limit)
            for sample in dataset.samples:
                crops.append(dataset.load_image(sample))
                types.append(sample.field_type)
        crops.append(np.full((30, 60, 3), 90, np.uint8))  # blank
        types.append("score")
        for image, field_type in zip(crops, types):
            seen.clear()
            tess.recognize([image], [field_type])
            patch = adapter.scoresight_patch(tesseract, image, field_type)
            if not seen:
                assert patch is None
            else:
                assert patch is not None
                np.testing.assert_array_equal(patch, seen[-1])
        assert patch is None  # the blank crop
    finally:
        tess.close()


@pytest.mark.ocr
def test_binarized_variant_skips_blank_patches():
    pytest.importorskip("tesserocr")
    digit = one_hot([idx("7")])
    engine = OnnxTR("crnn_mobilenet_v3_small-bin")

    class AnyImage(FakeModel):
        def __call__(self, batch, return_model_output=False):
            self.calls.append(len(batch))
            for image in batch:
                assert image.ndim == 3 and image.shape[2] == 3
                assert image.shape[0] == 35  # rescaled like ScoreSight's patch
            return {"preds": [decode_ctc(digit, VOCAB)] * len(batch)}

    fake = AnyImage({}, "ctc")
    engine.use_predictor(FakePredictor(fake))
    from ocr_eval._src import isolate_app_state

    isolate_app_state()
    import tesseract

    engine._tesseract = tesseract
    text_crop = np.full((40, 30, 3), 30, np.uint8)
    cv2.putText(text_crop, "7", (5, 33), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (230,) * 3, 3)
    results = engine.recognize([crop(128), text_crop], ["score", "score"])
    assert results[0].text == "" and results[0].info == {"state": "Empty"}
    assert results[1].text == "7"
    assert fake.calls == [1]


# --- the real library (only if OnnxTR and the small CRNN are already here) ---


def _cached_crnn() -> bool:
    try:
        from onnxtr.models.recognition.models.crnn import default_cfgs
    except ImportError:
        return False
    url = default_cfgs["crnn_mobilenet_v3_small"]["url"]
    cache = os.environ.get(
        "ONNXTR_CACHE_DIR", os.path.join(os.path.expanduser("~"), ".cache", "onnxtr")
    )
    return (Path(cache) / "models" / url.rpartition("/")[2]).is_file()


@pytest.mark.skipif(not _cached_crnn(), reason="OnnxTR or its CRNN model missing")
def test_matches_onnxtr_recognition_predictor():
    """Without -wl the adapter reads exactly what OnnxTR's predictor reads
    (including very wide crops, which it splits), and its CTC decoder agrees
    with OnnxTR's post-processor."""
    from ocr_eval.dataset import load_dataset

    dataset = load_dataset("synthetic_7seg", limit=20)
    crops = [dataset.load_image(s) for s in dataset.samples]
    crops += [np.full((5, 300, 3), 255, np.uint8), np.zeros((1, 1, 3), np.uint8)]
    types = [s.field_type for s in dataset.samples] + ["text", "score"]
    for image in crops:
        image.flags.writeable = False

    engine = OnnxTR("crnn_mobilenet_v3_small")
    engine.load()
    try:
        predictor = engine._predictor
        expected = predictor([cv2.cvtColor(c, cv2.COLOR_BGR2RGB) for c in crops])
        results = []
        for start in range(0, len(crops), engine.batch_size):
            results += engine.recognize(
                crops[start : start + engine.batch_size],
                types[start : start + engine.batch_size],
            )
        assert [r.text for r in results] == [text for text, _ in expected]
        assert [r.confidence for r in results] == pytest.approx(
            [float(c) for _, c in expected], abs=1e-4
        )
        batch = predictor.pre_processor([cv2.cvtColor(crops[0], cv2.COLOR_BGR2RGB)])[0]
        out = predictor.model(batch, return_model_output=True)
        text, confidence = decode_ctc(out["out_map"][0], predictor.model.vocab)
        assert (text, confidence) == (
            out["preds"][0][0],
            pytest.approx(out["preds"][0][1], abs=1e-5),
        )
        info = engine.info()
        assert "MB" in info["size"] and info["vocab_size"] == len(predictor.model.vocab)
    finally:
        engine.close()

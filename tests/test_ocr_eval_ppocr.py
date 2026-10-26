"""ocr_eval's PaddleOCR adapter (ocr_eval/engines/ppocr.py): variants,
PaddleOCR's preprocessing, CTC decoding with and without the box's whitelist,
and recognize() with a fake onnxruntime session. Only the last test needs the
real model (onnxruntime, huggingface_hub, PyYAML and a cached download)."""

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from ocr_eval.engines import discover  # noqa: E402
from ocr_eval.engines import ppocr  # noqa: E402
from ocr_eval.engines.ppocr import (  # noqa: E402
    MODELS,
    REQUIREMENTS,
    PaddleOCRRec,
    as_probabilities,
    class_groups,
    ctc_greedy,
    padded_width,
    read_character_dict,
    read_input_config,
    resize_normalize,
    split_variant,
)
from ocr_eval.fields import whitelist  # noqa: E402


def _readonly(image):
    image = np.ascontiguousarray(image)
    image.flags.writeable = False
    return image


# -- variants and discovery


def test_variants_cover_every_model_preprocessing_and_decoding():
    # the colour crop, -bin and -gray, each with and without -wl
    assert len(PaddleOCRRec.variants) == len(MODELS) * 6
    assert len(set(PaddleOCRRec.variants)) == len(PaddleOCRRec.variants)
    for variant in PaddleOCRRec.variants:
        model, preprocessing, constrained = split_variant(variant)
        assert model in MODELS
        engine = PaddleOCRRec(variant)
        assert (engine.model_key, engine.preprocessing, engine.constrained) == (
            model,
            preprocessing,
            constrained,
        )
        assert engine.binarize == (preprocessing == "-bin")
        assert engine.gray == (preprocessing == "-gray")
    assert split_variant("v6-tiny-bin-wl") == ("v6-tiny", "-bin", True)
    assert split_variant("v6-small-gray") == ("v6-small", "-gray", False)
    assert split_variant("v5-en-mobile") == ("v5-en-mobile", "", False)
    # the defaults read the raw crop, with and without the whitelist, with
    # every model but the slow server one
    assert set(PaddleOCRRec.default_variants) == {
        f"{m}{s}" for m in MODELS for s in ("", "-wl") if m != "v5-server"
    }
    with pytest.raises(ValueError):
        PaddleOCRRec("v6-huge")


def test_models_are_pinned_to_a_revision():
    for model in MODELS.values():
        assert model.repo.startswith("PaddlePaddle/") and model.repo.endswith("_onnx")
        assert len(model.revision) == 40
        int(model.revision, 16)


def test_discovered_with_its_requirements():
    registry = discover()
    assert registry.engines["ppocr"] is PaddleOCRRec
    assert PaddleOCRRec.requirements == REQUIREMENTS
    assert any(r.startswith("onnxruntime==") for r in REQUIREMENTS)
    assert not any(k.endswith(".ppocr") for k in registry.broken)


def test_unavailable_without_onnxruntime(monkeypatch):
    monkeypatch.setattr(PaddleOCRRec, "imports", ("no_such_module_for_ppocr",))
    reason = PaddleOCRRec.unavailable_reason()
    assert "no_such_module_for_ppocr" in reason and "onnxruntime==" in reason


def test_thread_setting(monkeypatch):
    monkeypatch.delenv(ppocr.THREADS_ENV, raising=False)
    assert PaddleOCRRec("v6-tiny")._threads == 1
    monkeypatch.setenv(ppocr.THREADS_ENV, "0")
    assert PaddleOCRRec("v6-tiny")._threads == 0
    monkeypatch.setenv(ppocr.THREADS_ENV, "four")
    with pytest.raises(ValueError):
        PaddleOCRRec("v6-tiny")


# -- preprocessing


def test_resize_normalize_matches_paddleocr():
    # a 20 x 10 crop (2:1) becomes 48 x 96 and is padded to 320 with zeros
    crop = np.full((10, 20, 3), 255, np.uint8)
    crop[:, :10] = 0
    assert padded_width(10, 20, 48, 320) == 320
    tensor = resize_normalize(crop, 48, 320)
    assert tensor.shape == (3, 48, 320) and tensor.dtype == np.float32
    assert tensor[:, :, 0].min() == -1.0  # black -> -1
    assert tensor[:, :, 95].max() == 1.0  # white -> 1
    assert not tensor[:, :, 96:].any()  # padding
    # a crop wider than 320/48 keeps its aspect ratio: no padding
    wide = np.full((10, 100, 3), 128, np.uint8)
    width = padded_width(10, 100, 48, 320)
    assert width == 480
    tensor = resize_normalize(wide, 48, width)
    assert tensor.shape == (3, 48, 480)
    assert np.allclose(tensor, 128 / 255.0 * 2 - 1, atol=1e-6)
    # and a tiny crop works
    assert resize_normalize(np.zeros((1, 1, 3), np.uint8), 48, 320).shape == (
        3,
        48,
        320,
    )


def test_read_model_config():
    config = {
        "PreProcess": {
            "transform_ops": [
                {"DecodeImage": {"channel_first": False, "img_mode": "BGR"}},
                {"MultiLabelEncode": {"gtc_encode": "NRTRLabelEncode"}},
                {"RecResizeImg": {"image_shape": [3, 48, 320]}},
            ]
        },
        "PostProcess": {"name": "CTCLabelDecode", "character_dict": ["0", "1", "a"]},
    }
    assert read_input_config(config) == (48, 320, "BGR")
    assert read_character_dict(config) == ["", "0", "1", "a", " "]
    with pytest.raises(ValueError):
        read_character_dict({"PostProcess": {"name": "AttnLabelDecode"}})
    with pytest.raises(ValueError):
        read_character_dict({"PostProcess": {"name": "CTCLabelDecode"}})


# -- decoding


def _onehot(steps, classes, confidence=0.9):
    probs = np.full((len(steps), classes), (1 - confidence) / (classes - 1))
    for t, c in enumerate(steps):
        probs[t, c] = confidence
    return probs.astype(np.float32)


def test_ctc_greedy_merges_repeats_and_drops_blanks():
    labels = ["", "1", "2"]
    text, confidence, kept = ctc_greedy(_onehot([0, 1, 1, 0, 1, 2, 2], 3), labels)
    assert text == "112"
    assert list(kept) == [1, 1, 2]
    assert confidence == pytest.approx(0.9)
    text, confidence, kept = ctc_greedy(_onehot([0, 0, 0], 3), labels)
    assert (text, confidence, kept.size) == ("", None, 0)


def test_as_probabilities():
    probs = _onehot([1, 2], 3)
    assert as_probabilities(probs) is probs
    logits = np.array([[2.0, 0.0, -1.0]], np.float32)
    softmax = as_probabilities(logits)
    assert softmax.sum() == pytest.approx(1.0)
    assert softmax.argmax() == 0


def test_whitelist_groups_merge_nfkc_forms_and_drop_the_rest():
    # blank, 0, O, full-width 0, 1, colon, full-width colon, a, circled 1, space
    characters = ["", "0", "O", "\uff10", "1", ":", "\uff1a", "a", "\u2460", " "]
    groups = class_groups(characters, "0123456789:")
    assert groups.labels == ["", "0", "1", ":"]
    # each form with its full-width or circled variants; no letters, no space
    assert [sorted(g) for g in np.split(groups.columns, groups.starts[1:])] == [
        [1, 3],
        [4, 8],
        [5, 6],
    ]
    probs = np.zeros((1, len(characters)), np.float32)
    probs[0, [0, 1, 2, 3, 8]] = [0.1, 0.2, 0.4, 0.15, 0.15]
    reduced = groups.reduce(probs)
    assert reduced.tolist()[0] == pytest.approx([0.1, 0.35, 0.15, 0.0])
    # the space is allowed when the whitelist has it
    assert " " in class_groups(characters, whitelist("text")).labels
    with pytest.raises(ValueError):
        class_groups(characters, "#")


# -- recognize() with a fake model

FULLWIDTH_ONE = "\uff11"


class FakeSession:
    """A stand-in for the onnxruntime session. For each crop of a batch it
    "reads" the width of the crop's non-padding part, e.g. 480 for a 10 x 100
    crop, with every 0 read as the letter O (and 0 as the runner-up) and every
    1 as a full-width one."""

    characters = ["", *"0123456789", "O", FULLWIDTH_ONE]  # [0] is the blank

    class _Arg:
        def __init__(self, name, shape):
            self.name, self.shape = name, shape

    def __init__(self):
        self.widths = []

    def get_inputs(self):
        return [self._Arg("x", ["N", 3, 48, "W"])]

    def get_outputs(self):
        # the dictionary, the blank and the space
        return [self._Arg("out", ["N", "T", len(self.characters) + 1])]

    def run(self, output_names, feeds):
        batch = feeds["x"]
        assert batch.dtype == np.float32 and batch.shape[1:3] == (3, 48)
        self.widths.append(batch.shape[3])
        labels = self.characters + [" "]
        outputs = []
        for image in batch:
            resized = np.flatnonzero(np.abs(image).sum(axis=(0, 1)))[-1] + 1
            steps = []
            for digit in str(resized):
                if digit == "0":
                    steps.append({"O": 0.6, "0": 0.3})
                elif digit == "1":
                    steps.append({FULLWIDTH_ONE: 0.6, "1": 0.3})
                else:
                    steps.append({digit: 0.9})
                steps.append({"": 1.0})
            steps += [{"": 1.0}] * (8 - len(steps))  # same length per batch
            probs = np.zeros((len(steps), len(labels)), np.float32)
            for t, step in enumerate(steps):
                for label, p in step.items():
                    probs[t, labels.index(label)] = p
                probs[t, 0] += 1.0 - probs[t].sum()
            outputs.append(probs)
        return [np.stack(outputs)]


@pytest.fixture
def fake_engine(monkeypatch):
    session = FakeSession()
    config = {
        "PostProcess": {
            "name": "CTCLabelDecode",
            "character_dict": FakeSession.characters[1:],
        }
    }
    monkeypatch.setattr(PaddleOCRRec, "model_files", lambda self: ("m.onnx", "m.yml"))
    monkeypatch.setattr(PaddleOCRRec, "read_config", staticmethod(lambda _: config))
    monkeypatch.setattr(PaddleOCRRec, "create_session", lambda self, _: session)

    def make(variant):
        engine = PaddleOCRRec(variant)
        engine.load()
        return engine

    make.session = session
    return make


def _crop(height, width):
    return _readonly(np.full((height, width, 3), 200, np.uint8))


# crops whose resized widths (48 px high) are 96, 480, 48, 960 and 102, padded
# to 320, 480, 320, 960 and 320
CROPS = [(10, 20), (10, 100), (10, 10), (5, 100), (19, 40)]
WIDTHS = ["96", "480", "48", "960", "102"]


def test_recognize_reads_crops_by_padded_width_in_order(fake_engine):
    engine = fake_engine("v6-tiny")
    crops = [_crop(*size) for size in CROPS]
    results = engine.recognize(crops, ["number"] * len(crops))
    assert [r.text for r in results] == [
        w.replace("0", "O").replace("1", FULLWIDTH_ONE) for w in WIDTHS
    ]
    # one call per padded width
    assert sorted(fake_engine.session.widths) == [320, 480, 960]
    assert results[0].confidence == pytest.approx(0.9)
    assert results[1].confidence == pytest.approx((0.9 + 0.9 + 0.6) / 3)
    assert results[0].info is None
    # a crop reads the same alone as in a batch
    assert engine.recognize([crops[1]], ["number"]) == [results[1]]


def test_whitelist_variant_reads_the_boxs_characters(fake_engine):
    engine = fake_engine("v6-tiny-wl")
    crops = [_crop(*size) for size in CROPS]
    results = engine.recognize(crops, ["score", "number", "clock", "period", "number"])
    assert [r.text for r in results] == WIDTHS
    assert results[0].info is None
    assert results[1].info == {"free": "48O"}
    # "0" competes with the blank only; the two forms of 1 count as one
    assert results[1].confidence == pytest.approx((0.9 + 0.9 + 0.3) / 3)
    assert results[4].confidence == pytest.approx((0.9 + 0.3 + 0.9) / 3)
    # a text box allows the letter O but not the full-width one
    [text] = engine.recognize([crops[4]], ["text"])
    assert text.text == "1O2"
    info = engine.info()
    assert info["model"] == MODELS["v6-tiny"].repo
    assert info["revision"] == MODELS["v6-tiny"].revision
    assert info["decoding"] == "field type's whitelist"


def test_runs_in_the_harness(fake_engine, monkeypatch):
    from ocr_eval.dataset import load_dataset
    from ocr_eval.runner import RunOptions, load_images, run_engine

    monkeypatch.setattr(PaddleOCRRec, "imports", ())
    dataset = load_dataset("synthetic_7seg", limit=20)
    record = run_engine(
        PaddleOCRRec,
        "v5-en-mobile-wl",
        dataset,
        load_images(dataset),
        RunOptions(single_latency_samples=3),
        log=None,
    )
    assert record["status"] == "ok", record.get("traceback")
    assert len(record["predictions"]) == 20
    assert record["latency"]["batch_size"] == PaddleOCRRec.batch_size
    assert record["latency"]["single"]["n"] == 3


def test_gray_variant_reads_the_crop_in_gray(fake_engine):
    engine = fake_engine("v6-tiny-gray-wl")
    crop = np.zeros((20, 30, 3), np.uint8)
    crop[5:15, 10:20] = (40, 200, 230)
    prepared = engine._prepare(_readonly(crop), "score")
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    assert prepared.shape == (20, 30, 3)
    for channel in range(3):
        assert np.array_equal(prepared[:, :, channel], gray)
    assert engine.info()["preprocessing"].startswith("the crop in gray")


@pytest.mark.ocr
def test_bin_variant_reads_scoresights_binarized_patch(fake_engine):
    engine = fake_engine("v6-tiny-bin")
    crop = np.zeros((20, 30, 3), np.uint8)
    crop[5:15, 10:20] = (40, 200, 230)
    prepared = engine._prepare(_readonly(crop), "score")
    assert prepared.shape == (20, 30, 3)
    assert set(np.unique(prepared)) <= {0, 255}
    # Otsu, then dilated once (3 x 3): the bright square grows by a pixel
    assert prepared[4:16, 9:21].min() == 255 and prepared[0:3].max() == 0


# -- the real model


def test_real_model_reads_synthetic_seven_segment_digits():
    pytest.importorskip("onnxruntime")
    pytest.importorskip("yaml")
    hub = pytest.importorskip("huggingface_hub")
    model = MODELS["v6-tiny"]
    for name in ("inference.onnx", "inference.yml"):
        cached = hub.try_to_load_from_cache(model.repo, name, revision=model.revision)
        if not isinstance(cached, str):
            pytest.skip(f"{model.repo} isn't in the Hugging Face cache")

    from ocr_eval.dataset import load_dataset
    from ocr_eval.runner import RunOptions, load_images, run_engine

    dataset = load_dataset("synthetic_7seg")
    record = run_engine(
        PaddleOCRRec,
        "v6-tiny-wl",
        dataset,
        load_images(dataset),
        RunOptions(single_latency_samples=5),
        log=None,
    )
    assert record["status"] == "ok", record.get("traceback")
    assert record["scores"]["raw"]["exact"]["value"] > 0.5
    assert record["engine_info"]["revision"] == model.revision

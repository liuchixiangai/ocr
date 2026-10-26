"""ocr_eval's VLM adapters (ocr_eval/engines/vlm.py): prompts, answer parsing,
constrained decoding and confidences, without downloading a model. The tests of
recognize() use a fake model and processor and need torch and transformers
(the eval extras); the rest needs neither."""

import json
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")

from ocr_eval.engines import EngineUnavailable, discover  # noqa: E402
from ocr_eval.engines.vlm import (  # noqa: E402
    FRAME_ANSWER_PREFIX,
    MODELS,
    REQUIREMENTS,
    Florence2,
    QwenVL,
    SmolVLM,
    allowed_token_ids,
    assign_regions,
    clean_answer,
    crop_prompt,
    frame_prompt,
    letterbox,
    parse_frame_answer,
    relative_box,
    terminator_token_ids,
)
from ocr_eval.fields import FIELD_TYPES  # noqa: E402
from ocr_eval.frames import FrameField  # noqa: E402


def test_engines_are_discovered_with_their_requirements():
    registry = discover()
    assert not registry.broken
    for name in ("florence2", "smolvlm", "qwen-vl"):
        engine = registry.get(name)
        assert engine.requirements == REQUIREMENTS
        assert set(engine.default_variants) <= set(engine.variants)
    # every variant names a pinned model
    for engine in (Florence2, SmolVLM, QwenVL):
        for variant in engine.variants:
            instance = engine(variant)
            assert instance.model_key in MODELS
            assert len(MODELS[instance.model_key].revision) == 40
    assert all("==" in requirement for requirement in REQUIREMENTS)


def test_unavailable_without_torch():
    engine = discover().get("qwen-vl")
    reason = engine.unavailable_reason()
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        assert reason and "torch" in reason and "pip install" in reason
    else:
        assert reason is None


def test_variants_flags_and_batch_size(monkeypatch):
    engine = Florence2("florence2-large-sq-wl")
    assert engine.model_key == "florence2-large" and engine.flags == {"sq", "wl"}
    assert Florence2("florence2-base").flags == set()
    assert Florence2.batch_size == 1
    qwen = QwenVL("qwen3.5-0.8b-wl")
    assert qwen.model_key == "qwen3.5-0.8b" and qwen.flags == {"wl"}
    assert qwen.batch_size == 8
    assert QwenVL("qwen3-vl-2b").model_key == "qwen3-vl-2b"
    monkeypatch.setenv("OCR_EVAL_VLM_BATCH", "3")
    assert SmolVLM("smolvlm-256m").batch_size == 3
    with pytest.raises(ValueError):
        QwenVL("qwen9")
    info = qwen.info()
    assert info["model"] == "Qwen/Qwen3.5-0.8B" and info["license"] == "Apache-2.0"
    assert info["options"] == ["wl"]
    json.dumps(info)


def test_crop_prompt_names_the_field_type():
    for field_type in FIELD_TYPES:
        prompt = crop_prompt(field_type)
        assert "only the characters" in prompt
    assert "game clock" in crop_prompt("clock")
    assert "score" in crop_prompt("score")


@pytest.mark.parametrize(
    "answer, text",
    [
        ("12", "12"),
        (" 12\n", "12"),
        ("12.", "12"),
        ("12:34", "12:34"),
        ("9.5", "9.5"),
        ("`12`", "12"),
        ('"HOME"', "HOME"),
        ("\n\n 7 \nThe score is 7", "7"),
        ("", ""),
        (None, ""),
        ("...", "..."),
    ],
)
def test_clean_answer(answer, text):
    assert clean_answer(answer) == text


def test_allowed_token_ids():
    tokens = ["1", " 12", "O", "", " ", "1:2", "<|end|>", "12a", "  3", "\n"]
    assert allowed_token_ids(tokens, "0123456789") == [0, 1, 4, 8]
    assert allowed_token_ids(tokens, "0123456789:") == [0, 1, 4, 5, 8]


def test_terminator_token_ids():
    tokens = [".", " .", "\n", ".\n\n", " ", "1.", "..", "!", "", "。"]
    assert terminator_token_ids(tokens) == [0, 1, 2, 3, 6, 9]


def test_letterbox_keeps_the_crop_centred_on_a_square():
    crop = np.zeros((10, 30, 3), np.uint8)
    crop[:, :, 2] = 200
    square = letterbox(crop)
    assert square.shape == (30, 30, 3)
    assert (square[10:20, :, 2] == 200).all()
    # the padding is the crop's mean colour
    assert (square[0, 0] == crop.reshape(-1, 3).mean(axis=0).astype(np.uint8)).all()
    assert letterbox(np.zeros((5, 5, 3), np.uint8)).shape == (5, 5, 3)


def _fields():
    return [
        FrameField("a", (100, 50, 100, 40), "score"),
        FrameField("b", (500, 300, 200, 100), "clock", "game clock"),
    ]


def test_relative_box_and_frame_prompt():
    assert relative_box((100, 50, 100, 40), 1000, 500) == [100, 100, 200, 180]
    assert relative_box((-5, 0, 2000, 10), 1000, 500) == [0, 0, 1000, 20]
    prompt = frame_prompt(_fields(), 1000, 500, "boxes")
    assert "1: [100, 100, 200, 180] (the score (a number))" in prompt
    assert "2: [500, 600, 700, 800] (game clock: the game clock" in prompt
    assert '{"1": "...", "2": "..."}' in prompt
    marks = frame_prompt(_fields(), 1000, 500, "marks")
    assert "magenta" in marks and "[100" not in marks


def test_parse_frame_answer():
    fields = _fields()
    assert parse_frame_answer('{"1": "12", "2": "4:05"}', fields, 1000, 500) == (
        ["12", "4:05"],
        None,
    )
    fenced = '```json\n{"1": "12", "2": null}\n```'
    assert parse_frame_answer(fenced, fields, 1000, 500) == (["12", ""], None)
    # the prefix the engine writes for the model, then a broken object
    texts, error = parse_frame_answer(
        FRAME_ANSWER_PREFIX + '7", "2": "1:00", "3', fields, 1000, 500
    )
    assert texts == ["7", "1:00"] and error
    # a grounding list instead: assigned by position (0-1000 coordinates)
    grounding = json.dumps(
        [
            {"bbox_2d": [100, 100, 200, 180], "text_content": "12"},
            {"bbox_2d": [520, 620, 680, 780], "label": "4:05"},
            {"bbox_2d": [0, 0, 10, 10], "label": "far away"},
        ]
    )
    texts, error = parse_frame_answer(grounding, fields, 1000, 500)
    assert texts == ["12", "4:05"] and error
    assert parse_frame_answer("no idea", fields, 1000, 500)[0] == ["", ""]


def test_assign_regions_splits_merged_words():
    fields = [
        FrameField("clock", (370, 455, 80, 45), "clock"),
        FrameField("word", (560, 455, 100, 45), "text"),
        FrameField("none", (0, 0, 10, 10), "text"),
    ]
    regions = [
        # "08:30 REGIONAL" spotted as one line over both boxes
        ([376, 461, 679, 461, 679, 497, 376, 497], "08:30 REGIONAL"),
        ([380, 470, 400, 470, 400, 480, 380, 480], ""),
    ]
    assert assign_regions(fields, regions) == ["08:30", "REGIONAL", ""]


# --- recognize() with a fake model (needs torch and transformers) ---------------


VOCAB = ["<eos>", "O", "0", "1", " 1", "a", "<pad>", "\n"]


class FakeTokenizer:
    eos_token_id = 0
    bos_token_id = None
    pad_token_id = 6
    padding_side = "right"

    def __len__(self):
        return len(VOCAB)

    def batch_decode(self, sequences, skip_special_tokens=False, **kwargs):
        out = []
        for sequence in sequences:
            ids = [int(i) for i in sequence]
            if skip_special_tokens:
                ids = [i for i in ids if i not in (0, 6)]
            out.append("".join(VOCAB[i] for i in ids))
        return out


class FakeProcessor:
    def __init__(self, torch):
        self.torch = torch
        self.tokenizer = FakeTokenizer()
        self.calls = []

    def apply_chat_template(self, messages, add_generation_prompt, **kwargs):
        assert add_generation_prompt
        return messages[0]["content"][1]["text"]

    def __call__(self, text, images, padding=False, return_tensors=None, **kwargs):
        from transformers import BatchFeature

        self.calls.append((text, images, kwargs))
        batch = len(text)
        return BatchFeature(
            {
                "input_ids": self.torch.full((batch, 3), 5),
                "pixel_values": self.torch.zeros((batch, 3, 4, 4)),
            }
        )


class FakeModel:
    """Greedy over a fixed schedule of logits: wants "O", then "1", then the end."""

    def __init__(self, torch):
        self.torch = torch
        self.config = SimpleNamespace(is_encoder_decoder=False)
        self.generation_config = SimpleNamespace(
            eos_token_id=[0], decoder_start_token_id=None
        )
        schedule = torch.full((3, len(VOCAB) + 2), -5.0)
        schedule[0, 1], schedule[0, 2] = 5.0, 4.0  # "O" over "0"
        schedule[1, 3] = 5.0  # "1"
        schedule[2, 0] = 5.0  # the end
        self.schedule = schedule
        self.kwargs = None

    def get_output_embeddings(self):
        return SimpleNamespace(weight=self.torch.zeros(len(VOCAB) + 2, 1))

    def to(self, device):
        return self

    def eval(self):
        return self

    def generate(self, input_ids, pixel_values, logits_processor=None, **kwargs):
        torch = self.torch
        self.kwargs = kwargs
        batch = input_ids.shape[0]
        sequences = input_ids
        scores = []
        for step in range(min(kwargs["max_new_tokens"], len(self.schedule))):
            logits = self.schedule[step].repeat(batch, 1)
            if logits_processor:
                logits = logits_processor(sequences, logits)
            scores.append(logits)
            sequences = torch.cat([sequences, logits.argmax(-1, keepdim=True)], dim=1)
        return SimpleNamespace(sequences=sequences, scores=tuple(scores))


@pytest.fixture
def fake_engine(monkeypatch):
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")

    def make(variant):
        engine = QwenVL(variant)
        monkeypatch.setattr(
            engine,
            "_load_model",
            lambda transformers, torch: (FakeModel(torch), FakeProcessor(torch)),
        )
        engine.load()
        assert engine._transformers is transformers
        return engine

    return make, torch


def test_recognize_with_a_fake_model(fake_engine):
    make, torch = fake_engine
    engine = make("qwen3.5-0.8b")
    crops = [np.zeros((8, 12, 3), np.uint8), np.zeros((9, 9, 3), np.uint8)]
    results = engine.recognize(crops, ["score", "text"])
    assert [r.text for r in results] == ["O1", "O1"]
    # greedy, temperature 0, no repetition penalty
    kwargs = engine.model.kwargs
    assert kwargs["do_sample"] is False and kwargs["repetition_penalty"] == 1.0
    # the sequence probability: product of the chosen tokens' probabilities
    expected = 1.0
    for step, token in enumerate((1, 3, 0)):
        expected *= float(torch.softmax(engine.model.schedule[step], -1)[token])
    assert results[0].confidence == pytest.approx(expected, abs=1e-4)
    text, images, kwargs = engine.processor.calls[0]
    assert "score" in text[0] and "word" in text[1]
    assert images[0].size == (12, 8)  # PIL, RGB
    assert engine.processor.tokenizer.padding_side == "left"
    info = engine.info()
    assert "threads" in info and info["device"] == "cpu"


def test_whitelist_constrains_decoding(fake_engine):
    make, _ = fake_engine
    engine = make("qwen3.5-0.8b-wl")
    # "O" isn't a digit: the digit "0" is chosen instead; text allows letters
    results = engine.recognize([np.zeros((8, 8, 3), np.uint8)] * 2, ["score", "text"])
    assert [r.text for r in results] == ["01", "O1"]
    assert 0 < results[0].confidence <= 1


def test_masked_terminators_end_the_answer(fake_engine):
    make, torch = fake_engine
    engine = make("qwen3.5-0.8b-wl")
    # wants "1", then a newline before the end; the digit "1" comes next
    schedule = torch.full((3, len(VOCAB) + 2), -5.0)
    schedule[0, 3] = 5.0
    schedule[1, 7], schedule[1, 0], schedule[1, 3] = 5.0, 4.0, 4.5
    schedule[2, 0] = 5.0
    engine.model.schedule = schedule
    [result] = engine.recognize([np.zeros((8, 8, 3), np.uint8)], ["score"])
    # the newline's probability went to the end of the answer: "1", not "11"
    assert result.text == "1"
    # unconstrained, the newline is read and cleaned off
    plain = make("qwen3.5-0.8b")
    plain.model.schedule = schedule
    [result] = plain.recognize([np.zeros((8, 8, 3), np.uint8)], ["score"])
    assert result.text == "1"


def test_load_failure_is_unavailable(fake_engine, monkeypatch):
    pytest.importorskip("torch")
    engine = QwenVL("qwen3.5-0.8b")

    def fail(transformers, torch):
        raise OSError("not in the cache")

    monkeypatch.setattr(engine, "_load_model", fail)
    with pytest.raises(EngineUnavailable, match="not in the cache"):
        engine.load()

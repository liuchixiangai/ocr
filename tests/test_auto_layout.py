"""The layout model's helpers (src/auto_layout.py): its input, its boxes, the
crop and the fields made from them, and its download."""

import hashlib
import io
import os
import ssl

import numpy as np
import pytest

import auto_layout
from auto_layout import Detection, board_crop, decode, field_boxes


def test_frames_become_the_models_rgb_input():
    bgr = np.zeros((90, 160, 3), np.uint8)
    bgr[..., 0] = 255  # blue
    x = auto_layout.to_model_input(bgr)
    assert x.shape == (1, 3, 640, 640) and x.dtype == np.float32
    # RGB order, 0-1
    assert x[0, 2].min() == 1.0 and x[0, 0].max() == 0.0
    bgra = np.dstack([bgr, np.full((90, 160), 255, np.uint8)])
    assert np.array_equal(auto_layout.to_model_input(bgra), x)
    gray = np.full((90, 160), 128, np.uint8)
    assert auto_layout.to_model_input(gray).shape == (1, 3, 640, 640)


def test_the_models_outputs_are_decoded_inside_the_picture_best_first():
    labels = np.array([0, 5, 2, 4, 9])
    boxes = np.array(
        [
            [10, 10, 50, 40],
            [-20, 5, 700, 300],  # clamped to the 640x360 picture
            [100, 100, 100.5, 120],  # thinner than a pixel
            [200, 50, 260, 80],
            [0, 0, 10, 10],  # an unknown class
        ],
        np.float32,
    )
    scores = np.array([0.4, 0.9, 0.8, 0.2, 0.99], np.float32)
    found = decode(labels, boxes, scores, 640, 360)
    assert [d.kind for d in found] == ["board", "home"]
    assert found[0].rect == (0.0, 5.0, 640.0, 295.0)
    assert found[1].rect == (10.0, 10.0, 40.0, 30.0)
    assert [d.kind for d in decode(labels, boxes, scores, 640, 360, 0.1)] == [
        "board",
        "home",
        "other",
    ]


def test_auto_crop_keeps_a_margin_around_the_best_scoreboard():
    found = [
        Detection("board", (100, 50, 200, 100), 0.5),
        Detection("board", (400, 300, 100, 50), 0.4),
        Detection("home", (120, 60, 20, 20), 0.9),
    ]
    assert board_crop(found, 1280, 720) == (80, 40, 240, 120)
    # within the picture
    assert board_crop(found, 1280, 720, margin=1.0) == (0, 0, 500, 250)
    assert board_crop([Detection("home", (1, 1, 5, 5), 0.9)], 100, 100) is None


def test_auto_layout_names_each_roles_best_box_once():
    found = sorted(
        [
            Detection("home", (100, 100, 40, 50), 0.8),
            Detection("home", (300, 100, 40, 50), 0.6),
            # the same digits as the better home box
            Detection("away", (102, 101, 40, 50), 0.7),
            Detection("away", (500, 100, 40, 50), 0.5),
            Detection("clock", (200, 20, 100, 40), 0.9),
            Detection("other", (0, 0, 10, 10), 0.95),
            Detection("board", (0, 0, 640, 360), 0.95),
        ],
        key=lambda d: -d.score,
    )
    fields = field_boxes(found, 640, 360)
    # padded by a tenth of the height on each side
    assert fields == {
        "Time": (196, 16, 108, 48),
        "Home Score": (95, 95, 50, 60),
        "Away Score": (495, 95, 50, 60),
    }


def test_field_boxes_stay_inside_the_picture():
    fields = field_boxes([Detection("period", (0, 0, 30, 40), 0.9)], 32, 42)
    assert fields == {"Period": (0, 0, 32, 42)}


# --- the download -------------------------------------------------------------------


class FakeResponse(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def fake_model(monkeypatch):
    """A tiny "model" served by a fake urlopen; returns the requests' URLs."""
    data = b"not really a model" * 1000
    monkeypatch.setattr(auto_layout, "MODEL_SHA256", hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(auto_layout, "MODEL_SIZE", len(data))
    monkeypatch.setattr(auto_layout, "CHUNK", 4096)
    monkeypatch.setattr(auto_layout.time, "sleep", lambda s: None)
    urls = []

    def urlopen(request, timeout, context):
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        urls.append(request.full_url)
        return FakeResponse(data)

    monkeypatch.setattr(auto_layout.urllib.request, "urlopen", urlopen)
    return urls


def test_the_model_is_downloaded_once_at_its_pinned_revision(fake_model, monkeypatch):
    assert not auto_layout.model_available()
    progress = []
    path = auto_layout.download_model(
        progress=lambda done, total: progress.append(done)
    )
    assert auto_layout.model_available() and os.path.isfile(path)
    assert path.startswith(auto_layout.models_dir())
    assert fake_model == [
        f"https://huggingface.co/{auto_layout.MODEL_REPO}/resolve/"
        f"{auto_layout.MODEL_REVISION}/{auto_layout.MODEL_FILE}"
    ]
    assert progress[-1] == auto_layout.MODEL_SIZE and progress == sorted(progress)
    # there already: not downloaded again
    auto_layout.download_model()
    assert len(fake_model) == 1
    # a mirror
    monkeypatch.setenv("HF_ENDPOINT", "https://mirror.example/")
    assert auto_layout.model_url().startswith("https://mirror.example/royshilkrot/")


def test_a_model_that_doesnt_match_its_hash_is_not_kept(fake_model, monkeypatch):
    monkeypatch.setattr(auto_layout, "MODEL_SHA256", "0" * 64)
    with pytest.raises(OSError, match="SHA-256"):
        auto_layout.download_model()
    # retried, then nothing left behind
    assert len(fake_model) == auto_layout.ATTEMPTS
    folder = os.path.dirname(auto_layout.model_path())
    assert os.listdir(folder) == []
    assert not auto_layout.model_available()


def test_the_download_trusts_certifis_certificates():
    # not the Python's own (the macOS app's has none, #140)
    context = auto_layout._tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert len(context.get_ca_certs()) > 50


def test_a_cancelled_download_leaves_nothing(fake_model):
    with pytest.raises(auto_layout.DownloadCancelled):
        auto_layout.download_model(cancelled=lambda: True)
    assert os.listdir(os.path.dirname(auto_layout.model_path())) == []

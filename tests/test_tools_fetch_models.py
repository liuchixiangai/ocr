"""scripts/fetch_models.py: downloads with retries and SHA-256 checks, the
character list and config it writes, idempotence, and --check."""

import hashlib
import http.server
import importlib.util
import json
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "fetch_models", REPO_ROOT / "scripts" / "fetch_models.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_models"] = module
    spec.loader.exec_module(module)
    return module


fetch_models = _load()

ONNX = b"not really an onnx model" * 100
CHARACTERS = ["0", "1", "2", "a", "　"]
YML = yaml.safe_dump(
    {
        "Global": {"model_name": "Test_rec"},
        "PreProcess": {
            "transform_ops": [
                {"DecodeImage": {"channel_first": False, "img_mode": "BGR"}},
                {"RecResizeImg": {"image_shape": [3, 48, 320]}},
            ]
        },
        "PostProcess": {"name": "CTCLabelDecode", "character_dict": CHARACTERS},
    },
    allow_unicode=True,
).encode("utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


MODEL = fetch_models.Model(
    folder="Test_rec",
    repo="test/Test_rec_onnx",
    revision="0123456789abcdef",
    onnx_sha256=sha(ONNX),
    onnx_size=len(ONNX),
    yml_sha256=sha(YML),
    characters=len(CHARACTERS),
    characters_sha256=sha("".join(f"{c}\n" for c in CHARACTERS).encode("utf-8")),
)


@pytest.fixture
def hub(monkeypatch):
    """A local stand-in for the Hugging Face Hub: serves the files, records
    the requests, and fails the first `failures` of them with a 503."""
    state = {"requests": [], "failures": 0, "files": {}}
    base = f"/{MODEL.repo}/resolve/{MODEL.revision}/"
    state["files"] = {base + "inference.onnx": ONNX, base + "inference.yml": YML}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append(self.path)
            if state["failures"] > 0:
                state["failures"] -= 1
                self.send_error(503)
                return
            body = state["files"].get(self.path)
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HF_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(fetch_models.time, "sleep", lambda seconds: None)
    yield state
    server.shutdown()
    server.server_close()


def test_fetch_downloads_verifies_and_writes_the_character_list(hub, tmp_path):
    assert fetch_models.problems(MODEL, tmp_path / MODEL.folder) == [
        "inference.onnx is missing",
        "characters.txt is missing",
        "config.json is missing or out of date",
    ]
    assert fetch_models.fetch(MODEL, tmp_path) is True
    folder = tmp_path / MODEL.folder
    assert (folder / "inference.onnx").read_bytes() == ONNX
    assert (folder / "characters.txt").read_bytes().decode("utf-8").split("\n") == [
        *CHARACTERS,
        "",
    ]
    config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    assert config["image_height"] == 48 and config["min_width"] == 320
    assert config["channel_order"] == "BGR"
    assert config["classes"] == len(CHARACTERS) + 2
    assert config["revision"] == MODEL.revision
    assert fetch_models.problems(MODEL, folder) == []
    assert not list(folder.glob("*.part"))


def test_fetch_is_idempotent(hub, tmp_path):
    assert fetch_models.fetch(MODEL, tmp_path) is True
    requests = len(hub["requests"])
    assert fetch_models.fetch(MODEL, tmp_path) is False
    assert len(hub["requests"]) == requests
    # a damaged model is downloaded again
    (tmp_path / MODEL.folder / "inference.onnx").write_bytes(b"truncated")
    assert fetch_models.fetch(MODEL, tmp_path) is True
    assert fetch_models.problems(MODEL, tmp_path / MODEL.folder) == []


def test_downloads_are_retried(hub, tmp_path):
    hub["failures"] = 2
    assert fetch_models.fetch(MODEL, tmp_path) is True
    assert hub["requests"][:3] == [hub["requests"][0]] * 3


def test_a_file_that_doesnt_match_its_hash_fails_closed(hub, tmp_path):
    bad = replace(MODEL, onnx_sha256="0" * 64)
    with pytest.raises(fetch_models.FetchError, match="SHA-256 mismatch"):
        fetch_models.fetch(bad, tmp_path)
    folder = tmp_path / MODEL.folder
    assert not (folder / "inference.onnx").exists()
    assert not list(folder.glob("*.part"))
    # every attempt was made
    onnx_requests = [r for r in hub["requests"] if r.endswith("inference.onnx")]
    assert len(onnx_requests) == fetch_models.ATTEMPTS


def test_an_unreachable_hub_fails(hub, tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_models, "ATTEMPTS", 2)
    hub["failures"] = 100
    with pytest.raises(fetch_models.FetchError):
        fetch_models.download(
            fetch_models.file_url(MODEL, "inference.onnx"),
            tmp_path / "x",
            MODEL.onnx_sha256,
            attempts=2,
        )


def test_a_changed_character_list_is_refused(hub, tmp_path):
    bad = replace(MODEL, characters_sha256="0" * 64)
    with pytest.raises(fetch_models.FetchError, match="character list"):
        fetch_models.fetch(bad, tmp_path)


def test_the_command(hub, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fetch_models, "MODELS", (MODEL,))
    assert fetch_models.main(["--check", "--dest", str(tmp_path)]) == 1
    assert "inference.onnx is missing" in capsys.readouterr().out
    assert fetch_models.main(["--dest", str(tmp_path)]) == 0
    assert fetch_models.main(["--check", "--dest", str(tmp_path)]) == 0
    assert "Test_rec: ok" in capsys.readouterr().out
    hub["failures"] = 100
    assert fetch_models.main(["--force", "--dest", str(tmp_path)]) == 1
    assert "FAILED" in capsys.readouterr().err


def test_the_pinned_models_are_the_evaluated_revisions():
    import ppocr_engine

    pins = {model.folder: model for model in fetch_models.MODELS}
    assert set(pins) == {info.folder for info in ppocr_engine.MODELS.values()}
    assert pins["PP-OCRv6_small_rec"].revision.startswith("b8f84f0b")
    assert pins["PP-OCRv6_tiny_rec"].revision.startswith("2612ab37")


@pytest.mark.ppocr
def test_the_downloaded_models_match_their_pins():
    for model in fetch_models.MODELS:
        assert (
            fetch_models.problems(model, fetch_models.MODELS_DIR / model.folder) == []
        )

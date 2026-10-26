"""python -m ocr_eval.fetch_models (CI's model download): which errors are
retried, and its exit status, with the Hugging Face Hub's errors faked."""

import pytest

from ocr_eval import fetch_models
from ocr_eval.fetch_models import EXIT_TRANSIENT, is_transient


def _error_class(name, *bases):
    return type(name, bases or (Exception,), {})


# stand-ins with the class hierarchies of huggingface_hub 1.x and httpx
HfHubHTTPError = _error_class("HfHubHTTPError", OSError)
RevisionNotFoundError = _error_class("RevisionNotFoundError", HfHubHTTPError)
RepositoryNotFoundError = _error_class("RepositoryNotFoundError", HfHubHTTPError)
GatedRepoError = _error_class("GatedRepoError", RepositoryNotFoundError)
EntryNotFoundError = _error_class("EntryNotFoundError")
RemoteEntryNotFoundError = _error_class(
    "RemoteEntryNotFoundError", HfHubHTTPError, EntryNotFoundError
)
LocalEntryNotFoundError = _error_class(
    "LocalEntryNotFoundError", FileNotFoundError, EntryNotFoundError
)
OfflineModeIsEnabled = _error_class("OfflineModeIsEnabled", ConnectionError)
TransportError = _error_class("TransportError")
ConnectError = _error_class("ConnectError", TransportError)


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code


def _http_error(status):
    error = HfHubHTTPError(f"HTTP {status}")
    error.response = _Response(status)
    return error


def _wrapped(cause):
    """What hf_hub_download raises when its HEAD call failed and the file isn't
    cached: a LocalEntryNotFoundError from the real error."""
    try:
        raise LocalEntryNotFoundError("cannot find the requested files") from cause
    except LocalEntryNotFoundError as e:
        return e


@pytest.mark.parametrize(
    "error",
    [
        ConnectError("connection refused"),
        _wrapped(ConnectError("connection refused")),
        ConnectionError("reset"),
        TimeoutError("timed out"),
        _http_error(429),
        _http_error(503),
        _wrapped(_http_error(502)),
    ],
    ids=["connect", "wrapped-connect", "connection", "timeout", "429", "503", "502"],
)
def test_network_errors_are_transient(error):
    assert is_transient(error)


@pytest.mark.parametrize(
    "error",
    [
        RevisionNotFoundError("no such revision"),
        _wrapped(RevisionNotFoundError("no such revision")),
        RemoteEntryNotFoundError("no such file"),
        _wrapped(RemoteEntryNotFoundError("no such file")),
        RepositoryNotFoundError("renamed or removed"),
        GatedRepoError("gated"),
        _http_error(401),
        _http_error(404),
        OfflineModeIsEnabled("HF_HUB_OFFLINE is set"),
        ImportError("No module named 'huggingface_hub'"),
        ValueError("anything else"),
        LocalEntryNotFoundError("not cached and outgoing traffic disabled"),
    ],
    ids=[
        "revision",
        "wrapped-revision",
        "file",
        "wrapped-file",
        "repository",
        "gated",
        "401",
        "404",
        "offline",
        "import",
        "other",
        "local-only",
    ],
)
def test_other_errors_are_permanent(error):
    assert not is_transient(error)


class _FakeDownload:
    """Raises the given errors in turn, then returns a path."""

    def __init__(self, *errors):
        self.errors = list(errors)
        self.calls = []

    def __call__(self, repo, filename, revision):
        self.calls.append((repo, revision, filename))
        if self.errors:
            raise self.errors.pop(0)
        return f"/cache/{repo}/{revision}/{filename}"


@pytest.fixture
def no_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr(fetch_models.time, "sleep", waits.append)
    return waits


def test_retries_transient_errors_then_succeeds(no_sleep):
    download = _FakeDownload(ConnectError("down"), _http_error(503))
    path = fetch_models.download_with_retries(
        download, "org/model", "abc123", "inference.onnx", attempts=4, wait=10
    )
    assert path == "/cache/org/model/abc123/inference.onnx"
    assert len(download.calls) == 3
    assert no_sleep == [10, 30]


def test_gives_up_after_the_last_attempt(no_sleep):
    download = _FakeDownload(*[_http_error(429)] * 3)
    with pytest.raises(fetch_models.TransientFailure):
        fetch_models.download_with_retries(
            download, "org/model", "abc123", "inference.onnx", attempts=3, wait=1
        )
    assert len(download.calls) == 3


def test_permanent_errors_are_not_retried(no_sleep):
    download = _FakeDownload(_wrapped(RevisionNotFoundError("gone")))
    with pytest.raises(LocalEntryNotFoundError):
        fetch_models.download_with_retries(
            download, "org/model", "abc123", "inference.onnx", attempts=4, wait=1
        )
    assert len(download.calls) == 1
    assert no_sleep == []


VARIANTS = "ppocr:v6-small-wl,v6-tiny-wl"


def test_pins_lists_each_file_once(capsys):
    # v6-small-wl and v6-small share their files
    assert fetch_models.main(["--pins", VARIANTS, "ppocr:v6-small"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 4
    assert all("@" in line for line in lines)
    assert "PaddlePaddle/PP-OCRv6_small_rec_onnx@" in lines[0]
    assert lines[0].endswith(" inference.onnx")


@pytest.mark.parametrize(
    "errors, status",
    [
        ([], 0),
        ([ConnectError("down")], 0),
        ([ConnectError("down")] * 2, EXIT_TRANSIENT),
        ([_wrapped(RevisionNotFoundError("gone"))], 1),
        ([GatedRepoError("gated")], 1),
    ],
    ids=["ok", "retried", "network", "revision", "gated"],
)
def test_exit_status(no_sleep, errors, status):
    download = _FakeDownload(*errors)
    assert fetch_models.main([VARIANTS, "--attempts", "2"], download=download) == (
        status
    )
    if status == 0:
        assert len({call[0] for call in download.calls}) == 2  # both models


def test_unknown_variant_fails(capsys):
    assert fetch_models.main(["ppocr:v6-huge"], download=_FakeDownload()) == 1
    assert "v6-huge" in capsys.readouterr().err

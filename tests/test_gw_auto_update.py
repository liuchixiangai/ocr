"""光为 OCR auto-update: mirrors, version compare, sha256, dev vs frozen."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
import requests

import gw_auto_update
from gw_auto_update import (
    CHECK_FAILED_TEXT,
    DOWNLOAD_ATTEMPTS,
    OPTIONAL_LATER,
    QUIT_TO_APPLY,
    REQUIRED_LATER,
    UPDATE_NOW,
    VERSION_FETCH_ATTEMPTS,
    CancelToken,
    DownloadCancelled,
    GwReleaseInfo,
    GwUpdateResult,
    check_gw_update,
    cleanup_stage,
    describe_check,
    download_and_stage,
    fetch_version_json,
    is_guangwei_channel,
    is_newer,
    parse_version_payload,
    schedule_apply_on_quit,
    show_check_failed_dialog,
    show_up_to_date_dialog,
    show_update_dialog,
    staged_payload_for,
    up_to_date_text,
    update_is_required,
    write_apply_bat,
)
from product_brand import (
    APP_DISPLAY_NAME,
    GW_DOWNLOAD_PAGE,
    GW_RELEASE,
    GW_VERSION_JSON_URLS,
    about_version_text,
    channel_window_title,
)


@pytest.fixture(autouse=True)
def _reset_apply_hook():
    gw_auto_update.reset_apply_hook_for_tests()
    yield
    gw_auto_update.reset_apply_hook_for_tests()


@pytest.fixture
def portable(tmp_path, monkeypatch):
    """A frozen portable root that is not the git checkout."""
    monkeypatch.setattr(gw_auto_update, "is_frozen_portable", lambda: True)
    monkeypatch.setattr(gw_auto_update, "_portable_root", lambda: tmp_path)
    monkeypatch.setattr(gw_auto_update.time, "sleep", lambda _s: None)
    return tmp_path


class FakeResponse:
    def __init__(self, status, payload=None, json_error=False):
        self.status_code = status
        self._payload = payload
        self._json_error = json_error
        self.closed = False

    def json(self):
        if self._json_error:
            raise ValueError("not JSON")
        return self._payload

    def close(self):
        self.closed = True


class FakeStream:
    def __init__(self, body, status=200, headers=None, fail_after=None, chunk=100):
        self.status_code = status
        self.headers = {"Content-Length": str(len(body))}
        if headers:
            self.headers.update(headers)
        self._body = body
        self._fail_after = fail_after
        self._chunk = chunk
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, chunk_size=1024):
        sent = 0
        step = self._chunk
        while sent < len(self._body):
            if self._fail_after is not None and sent >= self._fail_after:
                raise requests.ConnectionError("reset")
            part = self._body[sent : sent + step]
            sent += len(part)
            yield part

    def close(self):
        self.closed = True


def manifest(version="2026.10.26.1", sha="ab" * 32, **extra):
    data = {
        "version": version,
        "zip_url": "https://ocr.4018.cn/GuangWeiOCR-Portable.zip",
        "sha256": sha,
        "notes": "fix",
        "released_at": "2026-10-26",
    }
    data.update(extra)
    return data


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def release_for(blob: bytes, version="2026.10.26.1") -> GwReleaseInfo:
    return GwReleaseInfo(
        version=version,
        zip_url="https://ocr.4018.cn/GuangWeiOCR-Portable.zip",
        sha256=hashlib.sha256(blob).hexdigest(),
        notes="",
        released_at="",
    )


# --- versions ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "candidate", "newer"),
    [
        ("2026.10.25.1", "2026.10.25.1", False),
        ("2026.10.25.1", "2026.10.25.2", True),
        ("2026.10.25.2", "2026.10.25.1", False),
        ("2026.10.25.9", "2026.10.25.10", True),
        ("2026.10.25.1", "2026.10.26.1", True),
        ("2026.10.26.1", "2026.10.26.2", True),
        ("2026.10.26.2", "2026.10.26.1", False),
        ("2026.10.26.2", "2026.10.26.2", False),
        ("2026.10.1", "2026.9.30", False),
        ("2026.9.30", "2026.10.1", True),
        ("2026.10.25.1", "v2026.10.25.2", True),
        ("2026.10.25.1", "", False),
        ("2026.10.25.1", "  2026.10.25.1  ", False),
    ],
)
def test_channel_version_compare(current, candidate, newer):
    assert is_newer(candidate, current) is newer


def test_shipped_channel_is_newer_than_the_flip_portable():
    """Flip and 25 fps shipped as 2026.10.26.1; the hardened updater is .2;
    the PP-OCR default migration is .3; the clock hold fix is .4;
    the smooth preview ship is .5; clock tenths are .6;
    the vMix title-field scan is 2026.10.27.1; hand-picked vMix fields
    and Chinese team names are 2026.10.27.2; OBS Virtual Camera and the
    channel window title are 2026.10.27.3; the two-button Output chooser
    (JSON recommended, advanced vMix API) is 2026.10.27.4; UTF-8 portable
    zip filenames are 2026.10.27.5; the Authenticode-signed republish of
    those same features (Certum OV) is 2026.11.03.1; 24-second blank-as-24
    plus the mandatory-update protocol for this client is 2026.11.07.1."""
    assert GW_RELEASE == "2026.11.07.1"
    assert is_newer(GW_RELEASE, "2026.10.26.1")
    assert is_newer(GW_RELEASE, "2026.10.26.2")
    assert is_newer(GW_RELEASE, "2026.10.26.3")
    assert is_newer(GW_RELEASE, "2026.10.26.4")
    assert is_newer(GW_RELEASE, "2026.10.26.5")
    assert is_newer(GW_RELEASE, "2026.10.26.6")
    assert is_newer(GW_RELEASE, "2026.10.27.1")
    assert is_newer(GW_RELEASE, "2026.10.27.2")
    assert is_newer(GW_RELEASE, "2026.10.27.3")
    assert is_newer(GW_RELEASE, "2026.10.27.4")
    assert is_newer(GW_RELEASE, "2026.10.27.5")
    assert is_newer(GW_RELEASE, "2026.11.03.1")
    assert not is_newer("2026.11.03.1", GW_RELEASE)
    assert not is_newer(GW_RELEASE, GW_RELEASE)


def test_the_window_title_is_the_channel_release_not_the_upstream_version():
    from version import __version__

    title = channel_window_title()
    assert title == f"{APP_DISPLAY_NAME} · {GW_RELEASE}"
    assert __version__ not in title
    assert channel_window_title(profile="court2") == f"{title} [court2]"
    assert channel_window_title(paused=True) == f"⏸ {title}"
    about = about_version_text(__version__)
    assert about.startswith(f"{APP_DISPLAY_NAME}  {GW_RELEASE}")
    assert f"v{__version__}" in about


def test_same_installed_and_site_version_is_not_an_update(monkeypatch, caplog):
    """The failure mode where GW_RELEASE was never bumped: no update is offered."""
    monkeypatch.setattr(gw_auto_update, "GW_RELEASE", "2026.10.25.1")
    info = parse_version_payload(manifest(version="2026.10.25.1"))
    monkeypatch.setattr(
        gw_auto_update, "fetch_version_json", lambda: (info, None, None)
    )
    with caplog.at_level("INFO"):
        result = check_gw_update()
    assert result.current == "2026.10.25.1"
    assert result.latest == "2026.10.25.1"
    assert not result.update_available
    assert result.error is None
    assert describe_check(result) == "current"
    assert "no update" in caplog.text


def test_newer_site_version_is_an_update(monkeypatch):
    monkeypatch.setattr(gw_auto_update, "GW_RELEASE", "2026.10.25.1")
    info = parse_version_payload(manifest(version="2026.10.26.1"))
    monkeypatch.setattr(
        gw_auto_update, "fetch_version_json", lambda: (info, None, None)
    )
    result = check_gw_update()
    assert result.update_available
    assert result.latest == "2026.10.26.1"
    assert result.error is None
    assert describe_check(result) == "available"


def test_check_does_not_download(monkeypatch):
    monkeypatch.setattr(gw_auto_update, "GW_RELEASE", "2026.10.25.1")
    info = parse_version_payload(manifest())

    def fetch():
        return info, None, None

    def boom(*_a, **_k):
        raise AssertionError("check must not download")

    monkeypatch.setattr(gw_auto_update, "fetch_version_json", fetch)
    monkeypatch.setattr(gw_auto_update, "download_and_stage", boom)
    assert check_gw_update().update_available


def test_channel_off_does_not_fetch(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("not the 光为 channel")

    monkeypatch.setattr(gw_auto_update, "SIMPLIFY_OUTPUT_UI", False)
    monkeypatch.setattr(gw_auto_update, "_http_get", boom)
    assert not is_guangwei_channel()
    result = check_gw_update()
    assert result.error == "not guangwei channel"
    assert describe_check(result) == "ignored"
    monkeypatch.setattr(gw_auto_update, "SIMPLIFY_OUTPUT_UI", True)
    assert is_guangwei_channel()


def test_describe_check_network_failure_is_not_up_to_date():
    result = GwUpdateResult(current=GW_RELEASE, error="network", error_detail="timeout")
    assert describe_check(result) == "failed"
    assert "已是最新" not in CHECK_FAILED_TEXT
    assert "更新检查失败" in CHECK_FAILED_TEXT
    assert up_to_date_text(GW_RELEASE) == f"当前已是最新版本（{GW_RELEASE}）。"


# --- version.json -------------------------------------------------------------


def test_fetch_uses_the_next_mirror_when_the_first_fails(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["headers"]["User-Agent"].startswith("GuangWeiOCR/")
        if url != GW_VERSION_JSON_URLS[1]:
            raise requests.ConnectionError("down")
        return FakeResponse(200, manifest())

    monkeypatch.setattr(gw_auto_update, "_http_get", get)
    info, error, _detail = fetch_version_json()
    assert error is None
    assert info is not None and info.version == "2026.10.26.1"
    assert calls == [GW_VERSION_JSON_URLS[0], GW_VERSION_JSON_URLS[1]]


def test_fetch_retries_every_mirror(monkeypatch):
    calls = []
    slept = []

    def get(url, **_kwargs):
        calls.append(url)
        raise requests.Timeout("slow")

    monkeypatch.setattr(gw_auto_update, "_http_get", get)
    monkeypatch.setattr(
        gw_auto_update.time, "sleep", lambda seconds: slept.append(seconds)
    )
    info, error, detail = fetch_version_json()
    assert info is None
    assert error == "network"
    assert "slow" in detail
    assert calls == list(GW_VERSION_JSON_URLS) * VERSION_FETCH_ATTEMPTS
    assert len(slept) == VERSION_FETCH_ATTEMPTS - 1
    assert VERSION_FETCH_ATTEMPTS >= 3


def test_fetch_skips_404_and_invalid_manifests(monkeypatch):
    calls = []

    def get(url, **_kwargs):
        calls.append(url)
        if url == GW_VERSION_JSON_URLS[0]:
            return FakeResponse(404)
        if url == GW_VERSION_JSON_URLS[1]:
            return FakeResponse(200, {"version": "2026.10.26.1"})  # no sha256
        return FakeResponse(200, manifest())

    monkeypatch.setattr(gw_auto_update, "_http_get", get)
    info, error, _detail = fetch_version_json()
    assert error is None and info.sha256 == "ab" * 32
    assert calls == list(GW_VERSION_JSON_URLS)


def test_fetch_reports_a_bad_manifest(monkeypatch):
    monkeypatch.setattr(
        gw_auto_update,
        "_http_get",
        lambda url, **_kwargs: FakeResponse(
            200, {"version": "1", "zip_url": "https://x"}
        ),
    )
    monkeypatch.setattr(gw_auto_update.time, "sleep", lambda _s: None)
    info, error, detail = fetch_version_json(attempts=1)
    assert info is None and error == "manifest"
    assert "sha256" in detail
    monkeypatch.setattr(gw_auto_update, "GW_RELEASE", GW_RELEASE)
    monkeypatch.setattr(
        gw_auto_update, "fetch_version_json", lambda: (None, "manifest", detail)
    )
    result = check_gw_update()
    assert result.error == "manifest"
    assert not result.update_available
    assert describe_check(result) == "failed"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"version": "2026.10.26.1"},
        {"version": "2026.10.26.1", "zip_url": "ftp://x", "sha256": "ab" * 32},
        {
            "version": "2026.10.26.1",
            "zip_url": "https://ocr.4018.cn/a.zip",
            "sha256": "abc",
        },
    ],
)
def test_parse_rejects_incomplete_manifests(payload):
    with pytest.raises(ValueError):
        parse_version_payload(payload)


# Live 2026.11.03.1 version.json fetched 2026-11-07 from ocr.4018.cn.
# No mandatory / min_version keys. zip_url is under /downloads/.
LIVE_2026_11_03_1 = {
    "version": "2026.11.03.1",
    "zip_url": "https://ocr.4018.cn/downloads/GuangWeiOCR-Portable.zip",
    "sha256": "0a1da0015c098ac97f1533749356e9b18aa6098a94ec53ccb9a57b29ee46658d",
    "notes": (
        "Release 2026.11.03.1 — Authenticode（Certum OV）；"
        "与 2026.10.27.5 同功能，抬通道供自动更新。"
    ),
    "released_at": "2026-11-03T19:52:49+08:00",
}


def parse_like_shipped_2026_11_03_1(data):
    """Byte-equivalent of parse_version_payload in a1be0fd and 2ccb5aa.

    Those builds (portable 2026.10.27.5 and 2026.11.03.1) never read
    mandatory or min_version. Extra JSON keys are ignored, not rejected.
    """
    if not isinstance(data, dict):
        raise ValueError("version.json 不是对象")
    version = str(data.get("version") or "").strip()
    zip_url = str(data.get("zip_url") or "").strip()
    sha = str(data.get("sha256") or "").strip().lower()
    if not version or not zip_url:
        raise ValueError("version.json 缺少 version 或 zip_url")
    if not zip_url.lower().startswith(("https://", "http://")):
        raise ValueError("zip_url 无效")
    if not (len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)):
        raise ValueError("version.json 缺少有效的 sha256")
    return {
        "version": version,
        "zip_url": zip_url,
        "sha256": sha,
        "notes": str(data.get("notes") or ""),
        "released_at": str(data.get("released_at") or ""),
    }


def test_live_2026_11_03_1_manifest_has_no_force_fields():
    assert "mandatory" not in LIVE_2026_11_03_1
    assert "min_version" not in LIVE_2026_11_03_1
    assert "force" not in LIVE_2026_11_03_1
    old = parse_like_shipped_2026_11_03_1(LIVE_2026_11_03_1)
    info = parse_version_payload(LIVE_2026_11_03_1)
    assert old["version"] == info.version == "2026.11.03.1"
    assert info.mandatory is False
    assert info.min_version == ""
    assert not update_is_required(info, "2026.10.27.5")


def test_shipped_parser_ignores_mandatory_and_min_version():
    payload = manifest(
        version="2026.11.07.1",
        mandatory=True,
        min_version="2026.11.07.1",
        force=True,
        future_field="ignored",
    )
    old = parse_like_shipped_2026_11_03_1(payload)
    assert old["version"] == "2026.11.07.1"
    assert "mandatory" not in old
    assert "min_version" not in old
    assert "force" not in old
    info = parse_version_payload(payload)
    assert info.mandatory is True
    assert info.min_version == "2026.11.07.1"
    assert update_is_required(info, "2026.10.27.5")
    assert update_is_required(info, "2026.11.03.1")
    assert not update_is_required(info, "2026.11.07.1")


def test_min_version_requires_only_older_clients():
    info = parse_version_payload(
        manifest(version="2026.11.08.1", min_version="2026.11.07.1")
    )
    assert info.mandatory is False
    assert update_is_required(info, "2026.11.03.1")
    assert not update_is_required(info, "2026.11.07.1")
    assert not update_is_required(info, "2026.11.08.1")
    optional = parse_version_payload(manifest(version="2026.11.07.1"))
    assert not update_is_required(optional, "2026.10.27.5")


def test_check_marks_required_only_when_site_is_newer(monkeypatch):
    monkeypatch.setattr(gw_auto_update, "GW_RELEASE", "2026.11.03.1")
    required_info = parse_version_payload(
        manifest(version="2026.11.07.1", mandatory=True, min_version="2026.11.07.1")
    )
    monkeypatch.setattr(
        gw_auto_update, "fetch_version_json", lambda: (required_info, None, None)
    )
    result = check_gw_update()
    assert result.update_available
    assert result.required
    same = parse_version_payload(
        manifest(version="2026.11.03.1", mandatory=True, min_version="2026.11.07.1")
    )
    monkeypatch.setattr(
        gw_auto_update, "fetch_version_json", lambda: (same, None, None)
    )
    stuck = check_gw_update()
    assert not stuck.update_available
    assert not stuck.required
    monkeypatch.setattr(
        gw_auto_update, "fetch_version_json", lambda: (None, "network", "offline")
    )
    offline = check_gw_update()
    assert describe_check(offline) == "failed"
    assert not offline.required
    assert not offline.update_available


# --- download -----------------------------------------------------------------


def test_dev_run_refuses_to_download(tmp_path, monkeypatch):
    monkeypatch.setattr(gw_auto_update, "is_frozen_portable", lambda: False)
    monkeypatch.setattr(gw_auto_update, "_portable_root", lambda: tmp_path)
    info = release_for(b"zip")
    with pytest.raises(RuntimeError, match="开发版"):
        download_and_stage(info)
    assert not (tmp_path / "_gw_update_stage").exists()
    with pytest.raises(RuntimeError, match="开发版"):
        schedule_apply_on_quit(tmp_path)


def test_download_requires_sha256(portable, monkeypatch):
    called = []
    monkeypatch.setattr(
        gw_auto_update, "_http_get", lambda *a, **k: called.append(1) or None
    )
    info = GwReleaseInfo(
        version="2026.10.26.1",
        zip_url="https://ocr.4018.cn/a.zip",
        sha256="",
        notes="",
        released_at="",
    )
    with pytest.raises(RuntimeError, match="sha256"):
        download_and_stage(info)
    assert called == []


def test_download_stages_a_verified_zip(portable, monkeypatch):
    blob = zip_bytes({"GuangWeiOCR/GuangWeiOCR.exe": b"MZ-new"})
    info = release_for(blob)

    def get(url, **_kwargs):
        assert url == info.zip_url
        return FakeStream(blob)

    monkeypatch.setattr(gw_auto_update, "_http_get", get)
    payload = download_and_stage(info)
    assert payload == portable / "_gw_update_stage" / "payload"
    assert (payload / "GuangWeiOCR.exe").read_bytes() == b"MZ-new"
    ready = (portable / "_gw_update_stage" / "READY.txt").read_text(encoding="utf-8")
    assert info.version in ready
    assert info.sha256 in ready
    assert not (portable / "_gw_update_stage" / "incoming").exists()


def test_download_resumes_after_a_dropped_connection(portable, monkeypatch):
    blob = zip_bytes({"GuangWeiOCR/readme.txt": b"hello-portable" + b"x" * 400})
    info = release_for(blob)
    cut = 64
    ranges = []

    def get(_url, **kwargs):
        header = (kwargs.get("headers") or {}).get("Range")
        ranges.append(header)
        if header is None:
            return FakeStream(blob, fail_after=cut, chunk=32)
        assert header == f"bytes={cut}-"
        rest = blob[cut:]
        return FakeStream(rest, status=206, headers={"Content-Length": str(len(rest))})

    monkeypatch.setattr(gw_auto_update, "_http_get", get)
    payload = download_and_stage(info, attempts=3)
    assert ranges == [None, f"bytes={cut}-"]
    assert (payload / "readme.txt").read_bytes() == b"hello-portable" + b"x" * 400


def test_sha256_mismatch_deletes_the_partial_and_retries(portable, monkeypatch):
    keep = portable / "_gw_update_stage" / "payload"
    keep.mkdir(parents=True)
    (keep / "keep.txt").write_text("keep", encoding="utf-8")
    calls = []

    def get(_url, **_kwargs):
        calls.append(1)
        return FakeStream(b"not-the-real-zip")

    monkeypatch.setattr(gw_auto_update, "_http_get", get)
    info = release_for(b"the-real-zip")
    with pytest.raises(RuntimeError, match="SHA-256"):
        download_and_stage(info, attempts=2)
    assert len(calls) == 2
    assert not (portable / "_gw_update_stage" / "incoming").exists()
    assert (keep / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert DOWNLOAD_ATTEMPTS >= 3


def test_cancel_removes_the_partial_download(portable, monkeypatch):
    keep = portable / "_gw_update_stage" / "payload"
    keep.mkdir(parents=True)
    (keep / "keep.txt").write_text("keep", encoding="utf-8")
    blob = zip_bytes({"GuangWeiOCR/GuangWeiOCR.exe": b"MZ"})
    info = release_for(blob)
    cancel = CancelToken()

    def progress(_done, _total):
        cancel.cancel()

    monkeypatch.setattr(
        gw_auto_update,
        "_http_get",
        lambda *_a, **_k: FakeStream(blob + b"padding" * 20, chunk=20),
    )
    with pytest.raises(DownloadCancelled):
        download_and_stage(info, progress=progress, cancel=cancel)
    assert not (portable / "_gw_update_stage" / "incoming").exists()
    assert (keep / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_zip_slip_is_rejected(portable, monkeypatch):
    blob = zip_bytes({"../outside.txt": b"pwn", "GuangWeiOCR/ok.txt": b"ok"})
    info = release_for(blob)
    monkeypatch.setattr(gw_auto_update, "_http_get", lambda *_a, **_k: FakeStream(blob))
    with pytest.raises(RuntimeError, match="不安全"):
        download_and_stage(info, attempts=1)
    assert not (portable / "outside.txt").exists()
    assert not (portable / "_gw_update_stage" / "outside.txt").exists()


def test_oversized_zip_is_rejected(portable, monkeypatch):
    blob = zip_bytes({"GuangWeiOCR/big.bin": b"0123456789"})
    info = release_for(blob)
    monkeypatch.setattr(gw_auto_update, "_http_get", lambda *_a, **_k: FakeStream(blob))
    monkeypatch.setattr(gw_auto_update, "MAX_UNCOMPRESSED_BYTES", 4)
    with pytest.raises(RuntimeError, match="过大"):
        download_and_stage(info, attempts=1)


def test_cleanup_stage_refuses_other_directories(tmp_path):
    with pytest.raises(RuntimeError, match="refuse"):
        cleanup_stage(tmp_path)


# --- apply bat ----------------------------------------------------------------


def test_apply_bat_waits_kills_and_reports_copy_failure(portable):
    payload = portable / "payload"
    payload.mkdir()
    bat = write_apply_bat(payload, pid=4321)
    raw = bat.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw
    text = raw.decode("utf-8-sig")
    assert text.startswith("@echo off")
    assert 'set "PID=4321"' in text
    assert "taskkill /PID %PID% /F" in text
    assert "taskkill /PID %PID% /T" not in text
    assert "robocopy" in text
    assert "/XO" not in text
    assert "if errorlevel 8 goto copy_fail" in text
    assert "复制更新文件失败" in text
    assert "配置不会被删除" in text
    assert "GuangWeiOCR.exe.pre_update" in text
    assert "scoresight.exe.pre_update" in text
    assert (
        'copy /y "%DST%\\GuangWeiOCR.exe" "%DST%\\GuangWeiOCR.exe.pre_update"' in text
    )
    assert "未找到 GuangWeiOCR.exe" in text
    assert "GuangWeiOCR.exe" in text
    assert "scoresight.exe" in text
    assert GW_DOWNLOAD_PAGE in text
    assert str(payload) in text
    assert "\r\n" in text
    compact = text.replace(" ", "")
    assert 'rmdir/s/q"%DST%"' not in compact
    assert 'rmdir/s/q"%STAGE%"' in compact


def test_apply_hook_is_registered_once(portable):
    hooks = []
    first = portable / "one"
    second = portable / "two"
    first.mkdir()
    second.mkdir()
    schedule_apply_on_quit(first, register=hooks.append)
    schedule_apply_on_quit(second, register=hooks.append)
    assert hooks == [gw_auto_update._spawn_apply]
    text = (portable / "apply_gw_update.bat").read_text(encoding="utf-8-sig")
    assert str(second) in text
    assert str(first) not in text


def test_quit_hook_prefers_qt_and_does_not_also_use_atexit(monkeypatch):
    import atexit

    connected = []

    class Signal:
        def connect(self, fn):
            connected.append(fn)

    class App:
        aboutToQuit = Signal()

    registered = []
    monkeypatch.setattr(gw_auto_update, "_qt_app", lambda: App())
    monkeypatch.setattr(atexit, "register", registered.append)
    assert gw_auto_update._install_quit_hook(lambda: None) == "qt"
    assert len(connected) == 1
    assert registered == []

    monkeypatch.setattr(gw_auto_update, "_qt_app", lambda: None)
    assert gw_auto_update._install_quit_hook(lambda: None) == "atexit"
    assert len(registered) == 1


# --- dialogs ------------------------------------------------------------------


@pytest.mark.qt
def test_up_to_date_and_failure_dialogs_are_distinct(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QWidget

    parent = QWidget()
    seen = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args, **_kwargs: seen.append(args)
    )
    show_up_to_date_dialog(parent, "2026.10.25.1")
    assert seen[0][2] == "当前已是最新版本（2026.10.25.1）。"

    texts = []

    def fake_exec(self):
        texts.append(self.text())
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert show_check_failed_dialog(parent) is False
    assert texts == [CHECK_FAILED_TEXT]
    assert "已是最新" not in texts[0]


@pytest.mark.qt
def test_dev_dialog_opens_the_download_page(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QWidget

    monkeypatch.setattr(gw_auto_update, "is_frozen_portable", lambda: False)
    downloaded = []
    monkeypatch.setattr(
        gw_auto_update,
        "download_and_stage",
        lambda *_a, **_k: downloaded.append(1),
    )
    opened = []
    monkeypatch.setattr(
        gw_auto_update,
        "open_download_page",
        lambda: opened.append(GW_DOWNLOAD_PAGE) or True,
    )
    monkeypatch.setattr(
        QMessageBox, "exec", lambda _self: QMessageBox.StandardButton.Yes
    )
    info = parse_version_payload(manifest())
    result = GwUpdateResult(
        current="2026.10.25.1",
        latest=info.version,
        update_available=True,
        info=info,
    )
    assert show_update_dialog(QWidget(), result) is True
    assert opened == [GW_DOWNLOAD_PAGE]
    assert downloaded == []


@pytest.mark.qt
def test_new_version_dialog_names_both_versions(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QWidget

    texts = []

    def fake_exec(self):
        texts.append(self.text())
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    info = parse_version_payload(manifest(version="2026.10.26.1", notes="更快"))
    result = GwUpdateResult(
        current="2026.10.25.1",
        latest="2026.10.26.1",
        update_available=True,
        info=info,
    )
    assert show_update_dialog(QWidget(), result) is False
    assert "发现新版本 2026.10.26.1" in texts[0]
    assert "当前 2026.10.25.1" in texts[0]
    assert "更快" in texts[0]
    assert "开发版" in texts[0]


@pytest.mark.qt
def test_frozen_dialog_downloads_and_schedules_apply(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QWidget

    monkeypatch.setattr(gw_auto_update, "is_frozen_portable", lambda: True)
    payload = tmp_path / "payload"
    payload.mkdir()
    monkeypatch.setattr(gw_auto_update, "download_and_stage", lambda *_a, **_k: payload)
    scheduled = []
    monkeypatch.setattr(
        gw_auto_update,
        "schedule_apply_on_quit",
        lambda path: scheduled.append(path) or path,
    )
    infos = []
    monkeypatch.setattr(
        QMessageBox, "exec", lambda _self: QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args, **_kwargs: infos.append(args[2])
    )
    info = parse_version_payload(manifest())
    result = GwUpdateResult(
        current="2026.10.25.1",
        latest=info.version,
        update_available=True,
        info=info,
    )
    assert show_update_dialog(QWidget(), result) is True
    assert scheduled == [payload]
    assert any("已就绪" in text for text in infos)


@pytest.mark.qt
def test_frozen_dialog_reports_sha_failure_and_cancel(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QWidget

    monkeypatch.setattr(gw_auto_update, "is_frozen_portable", lambda: True)
    info = parse_version_payload(manifest())
    result = GwUpdateResult(
        current="2026.10.25.1",
        latest=info.version,
        update_available=True,
        info=info,
    )
    texts = []

    def fake_exec(self):
        texts.append(self.text())
        if "发现新版本" in self.text():
            return QMessageBox.StandardButton.Yes
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    def bad_sha(*_a, **_k):
        raise RuntimeError("SHA-256 校验失败（得到 aa，期望 bb）")

    monkeypatch.setattr(gw_auto_update, "download_and_stage", bad_sha)
    assert show_update_dialog(QWidget(), result) is False
    assert any("SHA-256" in text for text in texts)

    def cancel(*_a, **_k):
        raise DownloadCancelled()

    monkeypatch.setattr(gw_auto_update, "download_and_stage", cancel)
    before = len(texts)
    assert show_update_dialog(QWidget(), result) is False
    # Cancel does not add a "下载失败" dialog after the offer.
    assert texts[before:] == [texts[0]]
    assert not any("下载失败" in text for text in texts[before:])


@pytest.mark.qt
def test_required_dialog_decline_does_not_download_or_lock(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QWidget

    monkeypatch.setattr(gw_auto_update, "is_frozen_portable", lambda: True)
    downloaded = []
    monkeypatch.setattr(
        gw_auto_update,
        "download_and_stage",
        lambda *_a, **_k: downloaded.append(1),
    )
    texts = []
    buttons = []

    def fake_exec(self):
        texts.append(self.text())
        buttons.append(
            (
                self.button(QMessageBox.StandardButton.Yes).text(),
                self.button(QMessageBox.StandardButton.No).text(),
            )
        )
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    info = parse_version_payload(
        manifest(version="2026.11.07.1", mandatory=True, notes="24秒灯灭")
    )
    result = GwUpdateResult(
        current="2026.11.03.1",
        latest=info.version,
        update_available=True,
        required=True,
        info=info,
    )
    assert show_update_dialog(QWidget(), result) is False
    assert downloaded == []
    assert "必须更新到 2026.11.07.1" in texts[0]
    assert "24秒灯灭" in texts[0]
    assert OPTIONAL_LATER not in texts[0]
    assert buttons[0] == (UPDATE_NOW, REQUIRED_LATER)


@pytest.mark.qt
def test_required_update_retries_after_sha_failure(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QWidget

    monkeypatch.setattr(gw_auto_update, "is_frozen_portable", lambda: True)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "GuangWeiOCR.exe").write_bytes(b"MZ")
    calls = {"n": 0}

    def download(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("SHA-256 校验失败（得到 aa，期望 bb）")
        return payload

    monkeypatch.setattr(gw_auto_update, "download_and_stage", download)
    scheduled = []
    monkeypatch.setattr(
        gw_auto_update,
        "schedule_apply_on_quit",
        lambda path, **_k: scheduled.append(path) or path,
    )
    closed = []
    monkeypatch.setattr(gw_auto_update, "_quit_to_apply", closed.append)
    texts = []

    def fake_exec(self):
        texts.append(self.text())
        if "SHA-256" in self.text():
            return QMessageBox.StandardButton.Retry
        if "已就绪" in self.text():
            return QMessageBox.StandardButton.No
        if "必须更新到" in self.text():
            return QMessageBox.StandardButton.Yes
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    info = parse_version_payload(manifest(version="2026.11.07.1", mandatory=True))
    result = GwUpdateResult(
        current="2026.11.03.1",
        latest=info.version,
        update_available=True,
        required=True,
        info=info,
    )
    assert show_update_dialog(QWidget(), result) is True
    assert calls["n"] == 2
    assert scheduled == [payload]
    assert closed == []
    assert any("继续使用" in text or "仍可使用" in text for text in texts)


def test_staged_payload_is_reused_when_manifest_matches(portable, monkeypatch):
    blob = zip_bytes({"GuangWeiOCR/GuangWeiOCR.exe": b"MZ-new"})
    info = release_for(blob, version="2026.11.07.1")
    monkeypatch.setattr(gw_auto_update, "_http_get", lambda *_a, **_k: FakeStream(blob))
    payload = download_and_stage(info)
    assert staged_payload_for(info) == payload
    other = GwReleaseInfo(
        version="2026.11.08.1",
        zip_url=info.zip_url,
        sha256=info.sha256,
        notes="",
        released_at="",
    )
    assert staged_payload_for(other) is None
    bad_sha = GwReleaseInfo(
        version=info.version,
        zip_url=info.zip_url,
        sha256="ab" * 32,
        notes="",
        released_at="",
    )
    assert staged_payload_for(bad_sha) is None


@pytest.mark.qt
def test_required_dialog_reuses_staged_payload(qapp, portable, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QWidget

    monkeypatch.setattr(gw_auto_update, "is_frozen_portable", lambda: True)
    blob = zip_bytes({"GuangWeiOCR/GuangWeiOCR.exe": b"MZ-new"})
    info = release_for(blob, version="2026.11.07.1")
    monkeypatch.setattr(gw_auto_update, "_http_get", lambda *_a, **_k: FakeStream(blob))
    payload = download_and_stage(info)
    downloaded = []
    monkeypatch.setattr(
        gw_auto_update,
        "download_and_stage",
        lambda *_a, **_k: downloaded.append(1) or payload,
    )
    scheduled = []
    monkeypatch.setattr(
        gw_auto_update,
        "schedule_apply_on_quit",
        lambda path, **_k: scheduled.append(path) or path,
    )
    closed = []
    monkeypatch.setattr(gw_auto_update, "_quit_to_apply", closed.append)

    def fake_exec(self):
        assert "已下载" in self.text() or "已校验" in self.text()
        assert self.button(QMessageBox.StandardButton.Yes).text() == QUIT_TO_APPLY
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    result = GwUpdateResult(
        current="2026.11.03.1",
        latest=info.version,
        update_available=True,
        required=True,
        info=info,
    )
    assert show_update_dialog(QWidget(), result) is True
    assert downloaded == []
    assert scheduled == [payload]
    assert len(closed) == 1


def test_version_json_example_is_not_publishable_until_signed():
    """The committed example must not be a valid unsigned package manifest."""
    path = Path(__file__).resolve().parent.parent / "packaging" / "version.json.example"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == "2026.11.07.1"
    assert data["zip_url"].startswith("https://ocr.4018.cn/downloads/")
    assert data["mandatory"] is True
    assert data["min_version"] == "2026.11.07.1"
    assert "立即更新" in data["notes"]
    with pytest.raises(ValueError, match="sha256"):
        parse_version_payload(data)


def test_bump_gw_release_script(tmp_path, capsys):
    import importlib.util
    import sys

    path = Path(__file__).resolve().parent.parent / "scripts" / "bump_gw_release.py"
    spec = importlib.util.spec_from_file_location("bump_gw_release", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bump_gw_release"] = module
    spec.loader.exec_module(module)

    brand = tmp_path / "product_brand.py"
    brand.write_text('GW_RELEASE = "2026.10.25.1"\n', encoding="utf-8")
    assert module.main(["2026.10.25.1", "--file", str(brand)]) == 1
    assert module.main(["2026.10.24.9", "--file", str(brand)]) == 1
    assert brand.read_text(encoding="utf-8") == 'GW_RELEASE = "2026.10.25.1"\n'
    assert module.main(["2026.10.26.1", "--dry-run", "--file", str(brand)]) == 0
    assert 'GW_RELEASE = "2026.10.25.1"' in brand.read_text(encoding="utf-8")
    assert "sha256" in capsys.readouterr().out
    assert module.main(["2026.10.26.1", "--file", str(brand)]) == 0
    text = brand.read_text(encoding="utf-8")
    assert text == 'GW_RELEASE = "2026.10.26.1"\n'
    out = capsys.readouterr().out
    assert "version.json" in out
    assert "mandatory" in out
    assert "min_version" in out
    assert "/downloads/GuangWeiOCR-Portable.zip" in out
    assert "忽略 mandatory" in out

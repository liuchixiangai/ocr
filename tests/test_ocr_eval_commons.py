"""ocr_eval: the commons_scoreboards builder (no network: the Commons API is
faked) and the committed dataset."""

import json

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from ocr_eval import DATASETS_DIR  # noqa: E402
from ocr_eval.builders import commons_scoreboards as cs  # noqa: E402
from ocr_eval.dataset import load_dataset, validate_dataset  # noqa: E402

NAME = "commons_scoreboards"


@pytest.mark.parametrize(
    "short, expected",
    [
        ("CC BY-SA 4.0", "CC-BY-SA-4.0"),
        ("CC BY 2.0", "CC-BY-2.0"),
        ("CC BY 3.0", "CC-BY-3.0"),
        ("CC0", "CC0-1.0"),
        ("Public domain", "PDM-1.0"),
        ("CC BY-SA 3.0 de", None),  # ported: not accepted
        ("GFDL", None),
        ("Attribution", None),
        ("Copyrighted free use", None),
        ("", None),
    ],
)
def test_license_id(short, expected):
    assert cs.license_id({"LicenseShortName": short}) == expected


def test_license_url():
    assert cs.license_url("CC-BY-SA-4.0") == (
        "https://creativecommons.org/licenses/by-sa/4.0/"
    )
    assert cs.license_url("CC0-1.0").endswith("/publicdomain/zero/1.0/")


def test_author_of_strips_html_and_falls_back_to_credit():
    meta = {"Artist": '<a href="//commons.wikimedia.org/wiki/User:X">Jane &amp; Co</a>'}
    assert cs.author_of(meta) == "Jane & Co"
    assert cs.author_of({"Artist": "Unknown", "Credit": "<i>Flickr</i> user"}) == (
        "Flickr user"
    )
    assert cs.author_of({}) == "unknown author"


def _labels(pageid=101, **field_overrides):
    field = {
        "role": "home_score",
        "box": [10, 20, 60, 40],
        "text": "54",
        "field_type": "score",
    }
    field.update(field_overrides)
    return {
        "about": "test",
        "photos": {
            "File:Board.jpg": {
                "pageid": pageid,
                "sport": "basketball",
                "display_tech": "led7",
                "size": [200, 100],
                "fields": [
                    field,
                    {
                        "role": "clock",
                        "box": [100, 20, 90, 40],
                        "text": "4:32",
                        "field_type": "clock",
                        "display_tech": "dotmatrix",
                    },
                ],
            }
        },
        "rejected": {"File:Blurry.jpg": "illegible"},
    }


def test_dump_labels_round_trips_and_validates(tmp_path):
    labels = _labels()
    path = tmp_path / "labels.json"
    cs.save_labels(path, labels)
    assert json.loads(path.read_text(encoding="utf-8")) == labels
    assert b"\r\n" not in path.read_bytes()
    assert cs.load_labels(path)["photos"]["File:Board.jpg"]["pageid"] == 101


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"field_type": "speed"}, "field type"),
        ({"role": "mascot"}, "role"),
        ({"box": [1, 2, 0, 4]}, "box"),
        ({"text": " 54"}, "text"),
        ({"display_tech": "neon"}, "display tech"),
        ({"role": "clock"}, "duplicate"),
    ],
)
def test_load_labels_rejects_bad_fields(tmp_path, overrides, message):
    path = tmp_path / "labels.json"
    cs.save_labels(path, _labels(**overrides))
    with pytest.raises(ValueError, match=message):
        cs.load_labels(path)


def _fake_info(pageid=101, license="CC BY-SA 4.0", size=(200, 100)):
    return {
        "title": "File:Board.jpg",
        "pageid": pageid,
        "width": size[0] * 4,
        "height": size[1] * 4,
        "mime": "image/jpeg",
        "sha1": "0" * 40,
        "url": "https://upload.wikimedia.org/board.jpg",
        "page_url": "https://commons.wikimedia.org/wiki/File:Board.jpg",
        "rendition_url": "https://upload.wikimedia.org/thumb/board.jpg",
        "rendition_size": list(size),
        "extmetadata": {
            "LicenseShortName": license,
            "Artist": "<b>Jane Doe</b>",
        },
    }


@pytest.fixture
def offline_commons(tmp_path, monkeypatch):
    """A cache with the photo already downloaded, and a fake API."""
    cache = tmp_path / "cache"
    photo = np.zeros((100, 200, 3), np.uint8)
    photo[20:60, 10:70] = (0, 0, 255)  # the home score panel
    photo[20:60, 100:190] = (0, 255, 0)  # the clock panel
    (cache / "photos").mkdir(parents=True)
    ok, buffer = cv2.imencode(".png", photo)  # lossless, despite the name
    (cache / "photos" / "101.jpg").write_bytes(buffer.tobytes())
    infos = {"File:Board.jpg": _fake_info()}

    def fake_file_infos(client, titles, width=cs.REFERENCE_WIDTH):
        return {title: infos.get(title) for title in titles}

    def no_network(self, url, data=None, attempts=6):
        raise AssertionError(f"unexpected request to {url}")

    monkeypatch.setattr(cs, "file_infos", fake_file_infos)
    monkeypatch.setattr(cs.CommonsClient, "get", no_network)
    return cache, infos


def test_build_writes_a_valid_dataset(tmp_path, offline_commons):
    cache, _ = offline_commons
    labels_path = tmp_path / "labels.json"
    cs.save_labels(labels_path, _labels())
    out = tmp_path / "out"
    samples = cs.build(cache, out, labels_path, frames=True, log=None)

    assert [s.id for s in samples] == ["cm101_clock", "cm101_home_score"]
    report = validate_dataset(out, strict_licenses=True)
    assert report.ok, report.errors
    dataset = load_dataset(out)
    score = next(s for s in dataset.samples if s.extra["role"] == "home_score")
    assert score.text == "54" and score.field_type == "score"
    assert score.display_tech == "led7"  # the photo's
    assert score.license == "CC-BY-SA-4.0"
    assert score.author == "Jane Doe"
    assert score.label_source == "model-assisted-unverified"
    assert score.source_box == (10, 20, 60, 40)
    assert score.source_url == "https://commons.wikimedia.org/wiki/File:Board.jpg"
    assert "CC-BY-SA-4.0" in score.attribution and "Jane Doe" in score.attribution
    crop = dataset.load_image(score)
    assert crop.shape == (40, 60, 3)
    assert (crop[..., 2] > 200).all()  # only the red panel
    clock = next(s for s in dataset.samples if s.extra["role"] == "clock")
    assert clock.display_tech == "dotmatrix"  # the field's own
    # no group: the photo is the crops' cluster
    assert score.extra["group"] is None
    assert score.cluster == clock.cluster == "source_image:commons:101"

    frames = [
        json.loads(line)
        for line in (out / "frames.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(frames) == 1
    assert frames[0]["frame"] == "frames/cm101.jpg"
    assert (out / frames[0]["frame"]).is_file()
    assert {f["id"] for f in frames[0]["fields"]} == {s.id for s in samples}
    attribution = (out / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "Jane Doe" in attribution and "File:Board.jpg" in attribution
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "| illegible | 1 |" in readme


def test_build_records_the_photo_group(tmp_path, offline_commons):
    cache, _ = offline_commons
    labels = _labels()
    labels["photos"]["File:Board.jpg"]["group"] = "board-a"
    labels_path = tmp_path / "labels.json"
    cs.save_labels(labels_path, labels)
    out = tmp_path / "out"
    cs.build(cache, out, labels_path, log=None)
    samples = load_dataset(out).samples
    assert {s.extra["group"] for s in samples} == {"board-a"}
    assert {s.cluster for s in samples} == {"group:board-a"}


def test_build_scales_boxes_to_the_rendition(tmp_path, offline_commons):
    cache, _ = offline_commons
    labels = _labels()
    photo = labels["photos"]["File:Board.jpg"]
    photo["size"] = [400, 200]  # labelled on a rendition twice as large
    for field in photo["fields"]:
        field["box"] = [v * 2 for v in field["box"]]
    labels_path = tmp_path / "labels.json"
    cs.save_labels(labels_path, labels)
    samples = cs.build(cache, tmp_path / "out", labels_path, log=None)
    score = next(s for s in samples if s.extra["role"] == "home_score")
    assert score.source_box == [10, 20, 60, 40]


def test_build_skips_photos_whose_license_changed(tmp_path, offline_commons):
    cache, infos = offline_commons
    infos["File:Board.jpg"] = _fake_info(license="GFDL")
    labels_path = tmp_path / "labels.json"
    cs.save_labels(labels_path, _labels())
    messages = []
    samples = cs.build(cache, tmp_path / "out", labels_path, log=messages.append)
    assert samples == []
    assert any("license is now 'GFDL'" in m for m in messages)


def _review_export(path, decisions, dataset=NAME):
    path.write_text(
        json.dumps(
            {
                "schema": "scoresight-ocr-eval-review/1",
                "dataset": dataset,
                "manifest_sha256": "x",
                "reviewer": "A Person",
                "decisions": decisions,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_apply_review_verifies_fixes_and_drops(tmp_path, offline_commons):
    cache, _ = offline_commons
    labels_path = tmp_path / "labels.json"
    cs.save_labels(labels_path, _labels())
    review = _review_export(
        tmp_path / "review.json",
        {
            "cm101_home_score": {"verdict": "fix", "text": "64"},
            "cm101_clock": {"verdict": "drop", "reason": "illegible"},
            "cm999_other": {"verdict": "ok"},
        },
    )
    counts = cs.apply_review(labels_path, review, log=None)
    assert counts == {"fix": 1, "drop": 1, "unknown id": 1}
    labels = cs.load_labels(labels_path)
    (field,) = labels["photos"]["File:Board.jpg"]["fields"]
    assert field["text"] == "64"
    assert field["corrected_from"] == "54"
    assert field["verified_by"] == "A Person"
    assert "illegible" in labels["rejected_fields"]["cm101_clock"]

    samples = cs.build(cache, tmp_path / "out", labels_path, log=None)
    assert [(s.id, s.text, s.label_source) for s in samples] == [
        ("cm101_home_score", "64", "human")
    ]


def test_apply_review_refuses_another_dataset(tmp_path):
    labels_path = tmp_path / "labels.json"
    cs.save_labels(labels_path, _labels())
    review = _review_export(tmp_path / "review.json", {}, dataset="other")
    with pytest.raises(ValueError, match="other"):
        cs.apply_review(labels_path, review, log=None)


def test_discover_keeps_usable_licenses_and_caches_listings(tmp_path, monkeypatch):
    listed = []

    def fake_category_files(client, category, depth):
        listed.append(category)
        if category == "Category:Basketball scoreboards":
            return {"File:A.jpg": 1, "File:B.jpg": 2, "File:C.svg": 3}
        return {"File:A.jpg": 1, "File:D.jpg": 4}

    infos = {
        "File:A.jpg": {**_fake_info(1, "CC BY 4.0"), "title": "File:A.jpg"},
        "File:B.jpg": {**_fake_info(2, "CC BY-SA 3.0 de"), "title": "File:B.jpg"},
        "File:C.svg": {**_fake_info(3), "mime": "image/svg+xml"},
        "File:D.jpg": {**_fake_info(4, "CC0"), "width": 400, "height": 300},
    }
    downloads = []
    monkeypatch.setattr(cs, "category_files", fake_category_files)
    monkeypatch.setattr(
        cs, "file_infos", lambda client, titles, width: {t: infos[t] for t in titles}
    )
    monkeypatch.setattr(
        cs.CommonsClient, "download", lambda self, url, dest: downloads.append(dest)
    )
    candidates = cs.discover(cs.CommonsClient(log=None), tmp_path, log=lambda m: None)
    assert [(c["title"], c["license"], c["sport"]) for c in candidates] == [
        ("File:A.jpg", "CC-BY-4.0", "basketball")
    ]
    assert len(candidates[0]["categories"]) == len(cs.CATEGORIES)
    assert downloads == [tmp_path / "triage" / "1.jpg"]
    saved = json.loads((tmp_path / "candidates.json").read_text(encoding="utf-8"))
    assert saved[0]["title"] == "File:A.jpg"
    # the category listings are cached
    calls = len(listed)
    cs.discover(cs.CommonsClient(log=None), tmp_path, log=lambda m: None)
    assert len(listed) == calls


def test_client_retries_rate_limits(monkeypatch):
    import io
    import urllib.error

    calls = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        calls.append(request.get_header("User-agent"))
        assert request.get_method() == "POST"
        assert b"cmtitle=Category%3AX" in request.data
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "Too Many Requests", {"Retry-After": "0"}, None
            )
        return Response(b'{"query": {"categorymembers": []}}')

    monkeypatch.setattr(cs.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(cs.time, "sleep", lambda seconds: None)
    client = cs.CommonsClient(min_interval=0, log=None)
    assert cs.category_members(client, "Category:X", "file") == []
    assert len(calls) == 2
    assert all("ScoreSight" in agent for agent in calls)


# --- the committed dataset -------------------------------------------------------------


@pytest.fixture(scope="module")
def committed():
    if not (DATASETS_DIR / NAME / "manifest.jsonl").is_file():
        pytest.skip(f"{NAME} is not built")
    return load_dataset(NAME)


def test_committed_dataset_is_valid_and_attributed(committed):
    report = validate_dataset(committed.path, strict_licenses=True)
    assert report.ok, report.errors[:10]
    assert len(committed) >= 300
    for sample in committed.samples:
        assert sample.source_url.startswith("https://commons.wikimedia.org/wiki/File:")
        assert sample.author and sample.attribution, sample.id
        assert sample.license in sample.attribution, sample.id
        assert sample.extra["role"] and sample.extra["source_image"], sample.id
        assert sample.label_source in ("human", "model-assisted-unverified")
    attribution = (committed.path / "ATTRIBUTION.md").read_text(encoding="utf-8")
    for url in {s.source_url for s in committed.samples}:
        assert url in attribution
    total = sum(p.stat().st_size for p in committed.path.rglob("*") if p.is_file())
    assert total < 25e6


def test_committed_frames_match_the_manifest(committed):
    rows = [
        json.loads(line)
        for line in (committed.path / "frames.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    by_id = {s.id: s for s in committed.samples}
    seen = set()
    for row in rows:
        assert row["license"] and row["author"] and row["source_url"]
        if row.get("frame"):
            assert (committed.path / row["frame"]).is_file()
        for field in row["fields"]:
            sample = by_id[field["id"]]
            assert sample.text == field["text"]
            assert list(sample.source_box) == field["box"]
            assert sample.split == row["split"]
            seen.add(field["id"])
    assert seen == set(by_id)


def test_committed_labels_file_matches(committed):
    labels = cs.load_labels(cs.LABELS_FILE)
    fields = {
        cs.sample_id(photo["pageid"], field["role"]): field["text"]
        for photo in labels["photos"].values()
        for field in photo["fields"]
    }
    assert fields == {s.id: s.text for s in committed.samples}

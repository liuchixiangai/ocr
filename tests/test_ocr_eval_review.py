"""ocr_eval review: the HTML contact sheet for checking labels, and its exports."""

import json

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from ocr_eval.cli import main as cli_main  # noqa: E402
from ocr_eval.dataset import load_dataset  # noqa: E402
from ocr_eval.review import (  # noqa: E402
    REVIEW_SCHEMA,
    read_decisions,
    render_review,
)


def _dataset(tmp_path, with_frames=False):
    folder = tmp_path / "mini"
    (folder / "images").mkdir(parents=True)
    rows = []
    for index, (text, source) in enumerate(
        [("12", "photo:a"), ("4:32", "photo:a"), ("<7>", "photo:b")]
    ):
        image = np.full((20, 30, 3), 40 * index, np.uint8)
        (folder / "images" / f"c{index}.png").write_bytes(
            cv2.imencode(".png", image)[1].tobytes()
        )
        rows.append(
            {
                "id": f"c{index}",
                "image": f"images/c{index}.png",
                "text": text,
                "field_type": "clock" if ":" in text else "score",
                "display_tech": "led7",
                "split": "test",
                "label_source": "model-assisted-unverified",
                "license": "CC-BY-4.0",
                "source_url": f"https://example.org/{source}",
                "author": "Jane",
                "attribution": "x",
                "source_image": source,
                "role": "home_score",
            }
        )
    (folder / "manifest.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    (folder / "LICENSE").write_text("CC BY 4.0", encoding="utf-8")
    (folder / "ATTRIBUTION.md").write_text("Jane", encoding="utf-8")
    if with_frames:
        (folder / "frames").mkdir()
        frame = np.zeros((50, 100, 3), np.uint8)
        (folder / "frames" / "a.jpg").write_bytes(
            cv2.imencode(".jpg", frame)[1].tobytes()
        )
        row = {
            "id": "a",
            "source_image": "photo:a",
            "title": "File:A.jpg",
            "size": [200, 100],
            "frame": "frames/a.jpg",
            "frame_size": [100, 50],
            "fields": [
                {
                    "id": "c0",
                    "role": "home_score",
                    "box": [10, 10, 40, 20],
                    "text": "12",
                }
            ],
        }
        (folder / "frames.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    return load_dataset(folder)


def _results(dataset):
    return {
        "schema": "scoresight-ocr-eval/1",
        "runs": [
            {
                "dataset": dataset.name,
                "key": "tesseract:eng",
                "status": "ok",
                "predictions": [
                    {"id": "c0", "text": "12", "exact": True},
                    {"id": "c1", "text": "4.32", "exact": False},
                ],
            },
            {"dataset": "other", "key": "x:y", "status": "ok", "predictions": []},
        ],
    }


def test_page_shows_every_crop_escaped_and_grouped(tmp_path):
    dataset = _dataset(tmp_path)
    page = render_review(dataset, _results(dataset))
    assert page.count('<article class="tile"') == 3
    assert page.count('<section class="src">') == 2  # photo:a and photo:b
    assert "&lt;7&gt;" in page and "<7>" not in page
    assert "data:image/png;base64," in page
    assert 'data-hits="1" data-runs="1"' in page  # c0 read by the one run
    assert 'data-hits="0" data-runs="1"' in page  # c1 misread
    assert "<code>4.32</code>" in page
    assert "only crops no engine read" in page
    config = json.loads(page.split('id="config">', 1)[1].split("</script>", 1)[0])
    assert config["dataset"] == "mini"
    assert config["manifest_sha256"] == dataset.manifest_sha256
    assert config["runs"] == ["tesseract:eng"]
    assert config["schema"] == REVIEW_SCHEMA


def test_page_draws_the_frames_boxes(tmp_path):
    dataset = _dataset(tmp_path, with_frames=True)
    page = render_review(dataset)
    assert "data:image/jpeg;base64," in page
    # the box is in the 200x100 labelling coordinates, the frame is 100x50
    assert '<rect x="5.0" y="5.0" width="20.0" height="10.0"/>' in page
    assert "Photo with all boxes" in page
    assert "<svg" not in render_review(dataset, include_frames=False)


def test_review_command_writes_the_page(tmp_path, capsys):
    dataset = _dataset(tmp_path)
    results = tmp_path / "results.json"
    results.write_text(json.dumps(_results(dataset)), encoding="utf-8")
    out = tmp_path / "review.html"
    assert (
        cli_main(
            ["review", str(dataset.path), "--out", str(out), "--results", str(results)]
        )
        == 0
    )
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert cli_main(["review", str(tmp_path / "missing")]) == 2


def test_read_decisions_checks_the_export(tmp_path):
    path = tmp_path / "review.json"
    export = {
        "schema": REVIEW_SCHEMA,
        "dataset": "mini",
        "reviewer": "A Person",
        "decisions": {
            "c0": {"verdict": "ok"},
            "c1": {"verdict": "fix", "text": "4:33"},
        },
    }
    path.write_text(json.dumps(export), encoding="utf-8")
    assert read_decisions(path)["decisions"]["c1"]["text"] == "4:33"
    for broken, message in (
        ({"schema": "other"}, "not a review export"),
        ({"reviewer": " "}, "reviewer"),
        ({"decisions": {"c0": {"verdict": "maybe"}}}, "verdict"),
        ({"decisions": {"c0": {"verdict": "fix", "text": ""}}}, "correct text"),
    ):
        path.write_text(json.dumps({**export, **broken}), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            read_decisions(path)

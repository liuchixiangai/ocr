"""ocr_eval's whole-frame experiment (ocr_eval/frames.py) and head-to-head
comparison (ocr_eval/pairwise.py), with a fake engine."""

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from ocr_eval import frames as frames_module  # noqa: E402
from ocr_eval import pairwise  # noqa: E402
from ocr_eval.dataset import load_dataset  # noqa: E402
from ocr_eval.engines import Engine, Recognition, Registry  # noqa: E402
from ocr_eval.frames import (  # noqa: E402
    FrameField,
    find_frames,
    run_frames,
    select_frames,
)
from ocr_eval.report import read_results  # noqa: E402
from test_ocr_eval_dataset import _row, make_dataset  # noqa: E402


class FrameOracle(Engine):
    """Reads each field's truth; "bad" gets the clock wrong."""

    name = "frameoracle"
    variants = ("good", "bad")
    frame_modes = ("boxes",)
    truths: dict = {}

    def read_frame(self, frame, fields, mode):
        assert not frame.flags.writeable
        assert frame.shape == (100, 200, 3)
        assert all(isinstance(f, FrameField) for f in fields)
        texts = [self.truths[f.id] for f in fields]
        if self.variant == "bad":
            texts = [
                "9:99" if f.field_type == "clock" else t for f, t in zip(fields, texts)
            ]
        return [Recognition(t, 0.5) for t in texts], {"answer": "{...}"}


class CropOnly(Engine):
    name = "croponly"


@pytest.fixture
def frame_dataset(tmp_path):
    rows = [
        _row(id="f1a", image="images/f1a.png", text="12", source_image="pic:f1"),
        _row(
            id="f1b",
            image="images/f1b.png",
            text="1:05",
            field_type="clock",
            source_image="pic:f1",
            role="game clock",
        ),
        _row(id="f2a", image="images/f2a.png", text="3", source_image="pic:f2"),
        # no frame image for f3, no frame key for x, no box for y
        _row(id="f3a", image="images/f3a.png", text="4", source_image="pic:f3"),
        _row(id="x", image="images/x.png", text="5"),
        _row(
            id="y",
            image="images/y.png",
            text="6",
            source_image="pic:f2",
            source_box=None,
        ),
    ]
    folder = make_dataset(tmp_path / "ds", rows)
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for name in ("f1", "f2"):
        cv2.imwrite(str(frames_dir / f"{name}.png"), np.zeros((100, 200, 3), np.uint8))
    FrameOracle.truths = {row["id"]: row["text"] for row in rows}
    return load_dataset(folder), frames_dir


def test_find_and_select_frames(frame_dataset):
    dataset, frames_dir = frame_dataset
    frames, skipped = find_frames(dataset, [frames_dir])
    assert [f.key for f in frames] == ["pic:f1", "pic:f2"]
    assert [s.id for s in frames[0].samples] == ["f1a", "f1b"]
    assert frames[0].fields()[1] == FrameField(
        "f1b", (1, 2, 30, 20), "clock", "game clock"
    )
    assert skipped == {
        "frame image not found": 1,
        "no frame key": 1,
        "no source_box": 1,
    }
    # without a frames folder nothing is found
    assert find_frames(dataset)[0] == []
    assert select_frames(frames, None) == frames
    one = select_frames(frames, 1, seed=0)
    assert len(one) == 1 and one == select_frames(frames, 1, seed=0)
    assert {f.key for s in range(20) for f in select_frames(frames, 1, seed=s)} == {
        "pic:f1",
        "pic:f2",
    }


def test_frame_image_shipped_with_the_dataset(tmp_path):
    rows = [_row(id="a", image="images/a.png", frame="f", frame_image="frames/f.png")]
    folder = make_dataset(tmp_path / "ds", rows)
    (folder / "frames").mkdir()
    cv2.imwrite(str(folder / "frames" / "f.png"), np.zeros((4, 4, 3), np.uint8))
    frames, skipped = find_frames(load_dataset(folder))
    assert [f.image_path.name for f in frames] == ["f.png"] and not skipped


def test_run_frames_scores_each_field(frame_dataset):
    dataset, frames_dir = frame_dataset
    frames, _ = find_frames(dataset, [frames_dir])
    good = run_frames(FrameOracle, "good", "boxes", dataset, frames, log=None)
    assert good["status"] == "ok", good.get("traceback")
    assert good["key"] == "frameoracle:good@frames-boxes"
    assert good["scores"]["raw"]["exact"]["value"] == 1.0
    assert good["scores"]["raw"]["n"] == 3
    assert [f["fields"] for f in good["frames"]] == [2, 1]
    assert good["latency"]["frame"]["n"] == 2 and good["latency"]["batch"]["n"] == 3
    assert {p["frame"] for p in good["predictions"]} == {"pic:f1", "pic:f2"}
    bad = run_frames(FrameOracle, "bad", "boxes", dataset, frames, log=None)
    assert bad["scores"]["raw"]["exact"]["k"] == 2
    assert bad["scores"]["raw"]["by_field_type"]["clock"]["exact"]["value"] == 0.0
    skipped = run_frames(FrameOracle, "good", "marks", dataset, frames, log=None)
    assert skipped["status"] == "skipped" and "marks" in skipped["reason"]
    assert (
        run_frames(CropOnly, "default", "boxes", dataset, frames, log=None)["status"]
        == "skipped"
    )


def test_frames_command_line(frame_dataset, tmp_path, monkeypatch):
    dataset, frames_dir = frame_dataset
    monkeypatch.setattr(
        "ocr_eval.engines.discover",
        lambda: Registry({"frameoracle": FrameOracle}),
    )
    out = tmp_path / "out"
    args = [
        "--dataset",
        str(dataset.path),
        "--frames-dir",
        str(frames_dir),
        "--engine",
        "frameoracle:*",
        "--mode",
        "boxes",
        "--out",
        str(out),
        "--quiet",
    ]
    assert frames_module.main(args) == 0
    results = read_results(out / "results.json")
    assert [r["key"] for r in results["runs"]] == [
        "frameoracle:good@frames-boxes",
        "frameoracle:bad@frames-boxes",
    ]
    subset = results["datasets"]["ds"]["subset"]
    assert subset["frames"] == 2 and subset["n"] == 3 and subset["manifest_n"] == 6
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "## Whole frames" in report and "frameoracle:bad@frames-boxes" in report
    assert (
        "Subset: the crops of 2 of 2 frames (seed 0): 3 of the manifest's 6 crops."
        in report
    )
    one = tmp_path / "one"
    assert frames_module.main(args[:-3] + ["--frames", "1", "--out", str(one)]) == 0
    assert read_results(one / "results.json")["datasets"]["ds"]["subset"]["frames"] == 1
    unknown = ["--engine", "nobody", "--mode", "x", "--out", str(one)]
    assert frames_module.main(args[:4] + unknown) == 2
    # no frames found
    assert frames_module.main(args[:2] + args[4:]) == 2

    # head to head: the good frame run against the bad one, crop by crop
    pairs = pairwise.pair_runs(results, results, "*good*", "*bad*")
    [pair] = pairs
    assert (pair.n, pair.a_right, pair.b_right, pair.a_only, pair.b_only) == (
        3,
        3,
        2,
        1,
        0,
    )
    assert pair.delta == pytest.approx(-1 / 3)
    assert pair.clusters is None  # no datasets given
    # with the dataset: its 3 crops come from 2 photos, and the one crop the
    # runs disagree on is one photo's difference, which a sign flip can't
    # tell from chance
    clusters = pairwise.dataset_clusters(results)
    assert clusters["ds"]["f1a"] == clusters["ds"]["f1b"] == "source_image:pic:f1"
    [pair] = pairwise.pair_runs(results, results, "*good*", "*bad*", clusters=clusters)
    assert (pair.clusters, pair.cluster_p) == (2, 1.0)
    low, high = pair.delta_ci
    assert low <= pair.delta <= high <= 0
    markdown = tmp_path / "pairs.md"
    assert (
        pairwise.main(
            [
                str(out / "results.json"),
                str(out / "results.json"),
                "--a-key",
                "*good*",
                "--markdown",
                str(markdown),
            ]
        )
        == 0
    )
    table = markdown.read_text(encoding="utf-8")
    assert "frameoracle:good@frames-boxes | frameoracle:bad@frames-boxes | 3" in table
    assert "| 1 | 0 | 1 | 2 | " in table  # McNemar p 1, then 2 photos
    assert (
        pairwise.main([str(tmp_path / "missing.json"), str(out / "results.json")]) == 2
    )

"""ocr_eval: dataset manifests, validation, loading, and the committed datasets."""

import json

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from ocr_eval import DATASETS_DIR  # noqa: E402
from ocr_eval.dataset import (  # noqa: E402
    DatasetError,
    assign_split,
    list_datasets,
    Sample,
    load_dataset,
    read_image,
    resolve_dataset,
    stratified_sample,
    validate_dataset,
)

COMMITTED = ["synthetic_7seg", "textocr_scoreboards"]


def _row(**overrides):
    row = {
        "id": "a1",
        "image": "images/a1.png",
        "text": "12",
        "field_type": "score",
        "display_tech": "led7",
        "split": "test",
        "label_source": "human",
        "license": "CC0-1.0",
        "source_url": None,
        "author": "someone",
        "attribution": "someone (CC0-1.0)",
        "source_box": [1, 2, 30, 20],
        "notes": "",
    }
    row.update(overrides)
    return row


def make_dataset(folder, rows, image=None, license_files=True):
    (folder / "images").mkdir(parents=True, exist_ok=True)
    if image is None:
        image = np.zeros((20, 30), np.uint8)
    for row in rows:
        path = folder / row["image"]
        if path.suffix == ".png" and ".." not in row["image"]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(cv2.imencode(".png", image)[1].tobytes())
    (folder / "manifest.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    if license_files:
        (folder / "LICENSE").write_text("CC0", encoding="utf-8")
        (folder / "ATTRIBUTION.md").write_text("none", encoding="utf-8")
    return folder


@pytest.mark.parametrize("name", COMMITTED)
def test_committed_datasets_are_valid_and_redistributable(name):
    report = validate_dataset(DATASETS_DIR / name, strict_licenses=True)
    assert report.ok, report.errors[:10]
    dataset = load_dataset(name)
    assert len(dataset) > 0
    for sample in dataset.samples:
        assert sample.attribution, sample.id
        assert sample.author, sample.id


def test_committed_real_dataset_is_human_labelled_and_attributed():
    dataset = load_dataset("textocr_scoreboards")
    assert len(dataset) >= 300
    sources = {s.label_source for s in dataset.samples}
    assert "synthetic" not in sources
    for sample in dataset.samples:
        assert sample.source_url and sample.source_url.startswith("https://"), sample.id
        assert sample.source_box is not None
    total = sum((dataset.path / s.image).stat().st_size for s in dataset.samples)
    assert total < 15e6


def test_list_datasets_includes_committed():
    names = {path.name for path in list_datasets()}
    assert set(COMMITTED) <= names


def test_valid_dataset_loads(tmp_path):
    folder = make_dataset(
        tmp_path / "ds", [_row(), _row(id="a2", image="images/a2.png", split="dev")]
    )
    dataset = load_dataset(folder)
    assert dataset.name == "ds"
    assert [s.id for s in dataset.samples] == ["a1", "a2"]
    assert dataset.samples[0].source_box == (1, 2, 30, 20)
    assert len(load_dataset(folder, splits=["dev"])) == 1
    assert len(load_dataset(folder, limit=1)) == 1
    # by manifest path, and by name through OCR_EVAL_DATASETS
    assert resolve_dataset(folder / "manifest.jsonl") == folder.resolve()


def test_dataset_by_name_from_environment(tmp_path, monkeypatch):
    make_dataset(tmp_path / "private_set", [_row()])
    monkeypatch.setenv("OCR_EVAL_DATASETS", str(tmp_path))
    assert resolve_dataset("private_set") == (tmp_path / "private_set").resolve()
    assert "private_set" in {p.name for p in list_datasets()}


def test_unknown_dataset(tmp_path):
    with pytest.raises(DatasetError):
        resolve_dataset(str(tmp_path / "nope"))


def test_extra_keys_are_kept(tmp_path):
    folder = make_dataset(tmp_path / "ds", [_row(word_box=[1, 1, 5, 5])])
    [sample] = load_dataset(folder).samples
    assert sample.extra == {"word_box": [1, 1, 5, 5]}
    assert sample.to_dict()["word_box"] == [1, 1, 5, 5]


def test_cluster_is_the_group_then_the_photo_then_the_crop(tmp_path):
    rows = [
        _row(id="a", image="images/a.png", source_image="pic:1", group="board-x"),
        _row(id="b", image="images/b.png", source_image="pic:2", group=None),
        _row(id="c", image="images/c.png", frame="frames/3.jpg"),
        _row(id="d", image="images/d.png"),
    ]
    samples = load_dataset(make_dataset(tmp_path / "ds", rows)).samples
    assert [s.cluster for s in samples] == [
        "group:board-x",
        "source_image:pic:2",
        "frame:frames/3.jpg",
        "id:d",
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"field_type": "scores"}, "field_type"),
        ({"display_tech": "neon"}, "display_tech"),
        ({"split": "val"}, "split"),
        ({"label_source": "me"}, "label_source"),
        ({"text": ""}, "empty text"),
        ({"text": " 12"}, "whitespace"),
        ({"id": "a b"}, "invalid id"),
        ({"image": "../x.png"}, "relative path"),
        ({"image": "images/a1.jpg"}, ".png"),
        ({"source_box": [1, 2, 0, 3]}, "source_box"),
        ({"license": ""}, "empty license"),
        ({"text": 12}, "must be a string"),
    ],
)
def test_invalid_rows(tmp_path, overrides, message):
    folder = make_dataset(tmp_path / "ds", [_row(**overrides)])
    report = validate_dataset(folder)
    assert not report.ok
    assert any(message in error for error in report.errors), report.errors
    with pytest.raises(DatasetError):
        load_dataset(folder)


def test_missing_field_and_duplicate_id(tmp_path):
    row = _row()
    del row["split"]
    folder = make_dataset(tmp_path / "ds", [row, _row(), _row()])
    errors = validate_dataset(folder).errors
    assert any("missing 'split'" in e for e in errors)
    assert any("duplicate id" in e for e in errors)


def test_missing_image_and_license_files(tmp_path):
    folder = make_dataset(tmp_path / "ds", [_row()], license_files=False)
    (folder / "images" / "a1.png").unlink()
    errors = validate_dataset(folder).errors
    assert any("missing image" in e for e in errors)
    assert any("LICENSE" in e for e in errors)
    assert any("ATTRIBUTION" in e for e in errors)


def test_non_redistributable_license(tmp_path):
    folder = make_dataset(tmp_path / "ds", [_row(license="LicenseRef-private")])
    report = validate_dataset(folder)
    assert report.ok and report.warnings
    assert not validate_dataset(folder, strict_licenses=True).ok


def test_read_image_always_gives_bgr_uint8(tmp_path):
    gray = np.arange(200, dtype=np.uint8).reshape(10, 20)
    cv2.imwrite(str(tmp_path / "g.png"), gray)
    image = read_image(tmp_path / "g.png")
    assert image.shape == (10, 20, 3) and image.dtype == np.uint8
    rgba = np.zeros((4, 5, 4), np.uint8)
    cv2.imwrite(str(tmp_path / "a.png"), rgba)
    assert read_image(tmp_path / "a.png").shape == (4, 5, 3)
    deep = (np.arange(20, dtype=np.uint16) * 1000).reshape(4, 5)
    cv2.imwrite(str(tmp_path / "d.png"), deep)
    assert read_image(tmp_path / "d.png").dtype == np.uint8


def test_assign_split_is_stable():
    assert assign_split("photo1") == assign_split("photo1")
    splits = {assign_split(f"photo{i}") for i in range(100)}
    assert splits == {"dev", "test"}


def _samples(counts):
    samples = []
    for field_type, count in counts.items():
        for i in range(count):
            samples.append(
                Sample(
                    id=f"{field_type}{i}",
                    image=f"images/{field_type}{i}.png",
                    text="1",
                    field_type=field_type,
                    display_tech="led7",
                    split="test",
                    label_source="human",
                    license="CC0-1.0",
                )
            )
    return samples


def test_stratified_sample_is_proportional_reproducible_and_nested():
    samples = _samples({"number": 948, "text": 185, "clock": 139, "other": 4})
    subset = stratified_sample(samples, 200, seed=0)
    assert len(subset) == 200
    counts = {}
    for s in subset:
        counts[s.field_type] = counts.get(s.field_type, 0) + 1
    # 200 x each share, largest remainder; the 4 "other" crops still get one
    assert counts == {"number": 148, "text": 29, "clock": 22, "other": 1}
    # in manifest order, reproducible, independent of the input order
    ids = [s.id for s in subset]
    assert ids == [s.id for s in samples if s.id in set(ids)]
    assert stratified_sample(samples, 200, seed=0) == subset
    shuffled = list(reversed(samples))
    assert {s.id for s in stratified_sample(shuffled, 200)} == set(ids)
    assert stratified_sample(samples, 200, seed=1) != subset
    # a larger sample keeps the smaller one's crops (when no quota shrinks)
    assert set(ids) <= {s.id for s in stratified_sample(samples, 400)}
    # edge cases
    assert stratified_sample(samples, 0) == []
    assert stratified_sample(samples, 10_000) == samples
    # fewer than one per stratum: largest remainders only
    assert {s.field_type for s in stratified_sample(samples, 2)} == {"number"}
    assert {s.field_type for s in stratified_sample(samples, 4)} == set(counts)


def test_load_dataset_records_the_subset(tmp_path):
    rows = [
        _row(id=f"a{i}", image=f"images/a{i}.png", field_type=ft, split=split)
        for i, (ft, split) in enumerate(
            [("score", "dev"), ("score", "test"), ("clock", "test"), ("clock", "dev")]
        )
    ]
    folder = make_dataset(tmp_path / "ds", rows)
    assert load_dataset(folder).subset is None
    dataset = load_dataset(folder, sample=2, seed=3)
    assert {s.field_type for s in dataset.samples} == {"score", "clock"}
    assert dataset.subset == {
        "sample": 2,
        "seed": 3,
        "stratified_by": "field_type",
        "of": 4,
        "n": 2,
        "manifest_n": 4,
    }
    # a sample at least as large as what's left is all of it
    assert load_dataset(folder, sample=400).subset is None
    assert load_dataset(folder, splits=["test"], sample=2).subset == {
        "splits": ["test"],
        "n": 2,
        "manifest_n": 4,
    }
    assert load_dataset(folder, limit=1).subset == {
        "limit": 1,
        "n": 1,
        "manifest_n": 4,
    }

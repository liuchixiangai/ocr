"""The standalone data-preparation tools in scripts/ (no GUI is opened)."""

import importlib.util
import itertools
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
TOOLS = {
    "imageannotator": ["tkinter", "PIL"],
    "imagecropper": ["tkinter", "PIL"],
    "images_pespective_cropper": [],
    "video_cropper": [],
}


def require_modules(modules):
    for module in modules:
        if importlib.util.find_spec(module) is None:
            pytest.skip(f"{module} is not installed")


def load_tool(name):
    """Import scripts/<name>.py as a module (its main() is not run)."""
    require_modules(TOOLS[name])
    spec = importlib.util.spec_from_file_location(
        f"scoresight_tool_{name}", SCRIPTS_DIR / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_help(name):
    require_modules(TOOLS[name])
    # Python 3.14's argparse colours the help when FORCE_COLOR is set (as in CI)
    env = {**os.environ, "PYTHON_COLORS": "0"}
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / f"{name}.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("usage:")


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_missing_folder_is_a_usage_error(name, tmp_path, capsys):
    tool = load_tool(name)
    with pytest.raises(SystemExit) as exit_info:
        tool.main([str(tmp_path / "missing")])
    assert exit_info.value.code == 2
    assert "missing" in capsys.readouterr().err


@pytest.mark.parametrize(
    "name", ["imageannotator", "imagecropper", "images_pespective_cropper"]
)
def test_empty_folder_exits_without_a_window(name, tmp_path, capsys, monkeypatch):
    tool = load_tool(name)

    def no_window(*args, **kwargs):
        raise AssertionError("a window was opened")

    if hasattr(tool, "tk"):
        monkeypatch.setattr(tool.tk, "Tk", no_window)
    else:
        monkeypatch.setattr(tool.cv2, "namedWindow", no_window)
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
    (tmp_path / "subfolder.png").mkdir()

    result = tool.main([str(tmp_path)])

    assert result in (0, 1)
    assert capsys.readouterr().out


def test_annotator_lists_only_unannotated_images(tmp_path):
    tool = load_tool("imageannotator")
    for name in ["a.png", "b.JPG", "c.jpeg", "notes.txt"]:
        (tmp_path / name).write_bytes(b"")
    (tmp_path / "b.gt.txt").write_text("12", encoding="utf-8")

    images = tool.images_to_annotate(str(tmp_path))

    assert sorted(Path(p).name for p in images) == ["a.png", "c.jpeg"]
    assert tool.ground_truth_path(str(tmp_path / "a.png")) == str(tmp_path / "a.gt.txt")


@pytest.mark.parametrize("name", ["images_pespective_cropper", "video_cropper"])
def test_corners_are_ordered_whatever_the_click_order(name):
    tool = load_tool(name)
    corners = [(10, 20), (110, 25), (105, 80), (12, 75)]  # TL, TR, BR, BL
    for clicks in itertools.permutations(corners):
        np.testing.assert_array_equal(tool.order_corners(clicks), corners)


def quadrilateral_image():
    """A gray image with a white, slightly skewed 100x50 quadrilateral."""
    image = np.zeros((120, 160, 3), np.uint8)
    corners = np.array([(20, 30), (120, 34), (118, 84), (22, 80)], np.int32)
    cv2.fillPoly(image, [corners], (255, 255, 255))
    return image, [tuple(int(v) for v in c) for c in corners]


def test_perspective_cropper_saves_a_rectified_crop(tmp_path, monkeypatch):
    tool = load_tool("images_pespective_cropper")
    monkeypatch.setattr(tool.cv2, "imshow", lambda *args: None)
    output = tmp_path / "Données"  # non-ASCII: cv2.imwrite fails on Windows
    output.mkdir()
    image, corners = quadrilateral_image()
    cropper = tool.PerspectiveCropper(str(output))
    cropper.set_image(image)

    for x, y in reversed(corners):
        cropper.on_mouse(cv2.EVENT_LBUTTONDOWN, x, y, 0, None)

    (saved,) = output.iterdir()
    crop = cv2.imdecode(np.fromfile(str(saved), np.uint8), cv2.IMREAD_GRAYSCALE)
    assert crop.shape == (50, 100)
    assert crop.mean() > 200  # the white region fills the crop
    assert cropper.points == []


def test_perspective_cropper_ignores_degenerate_points(tmp_path, monkeypatch):
    tool = load_tool("images_pespective_cropper")
    monkeypatch.setattr(tool.cv2, "imshow", lambda *args: None)
    cropper = tool.PerspectiveCropper(str(tmp_path))
    cropper.set_image(np.zeros((50, 50, 3), np.uint8))

    for _ in range(4):
        cropper.on_mouse(cv2.EVENT_LBUTTONDOWN, 10, 10, 0, None)

    assert list(tmp_path.iterdir()) == []


def test_video_cropper_transform():
    tool = load_tool("video_cropper")
    image, corners = quadrilateral_image()

    matrix, size = tool.rectifying_transform(corners)
    crop = tool.binarize(cv2.warpPerspective(image, matrix, size))

    assert size == (100, 50)
    assert crop.mean() > 200
    assert tool.rectifying_transform([(5, 5)] * 4) is None

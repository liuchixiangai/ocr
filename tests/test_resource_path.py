import os
import sys
from pathlib import Path

import resource_path as resource_path_module
from resource_path import resource_path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_source_root_is_repo_root():
    assert Path(resource_path_module.SOURCE_ROOT) == REPO_ROOT
    assert Path(resource_path()) == REPO_ROOT


def test_resources_resolve_independently_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tessdata = Path(resource_path("tesseract", "tessdata"))
    assert tessdata == REPO_ROOT / "tesseract" / "tessdata"
    # This initial tree does not vendor Tesseract weights. When a local
    # packaging checkout has them, the four ScoreSight model names must be there.
    models = {p.stem for p in tessdata.glob("*.traineddata")}
    if models:
        assert {
            "daktronics",
            "eng",
            "scoreboard_general",
            "scoreboard_general_large",
        } <= models
    assert Path(resource_path("translations", "scoresight_en_US.qm")).is_file()
    assert Path(resource_path("icons", "circle-x.svg")).is_file()
    assert Path(
        resource_path("obs_data", "Scoresight_OBS_scene_collection.json")
    ).is_file()


def test_frozen_app_uses_meipass(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert resource_path() == str(tmp_path)
    assert resource_path("tesseract", "tessdata") == os.path.join(
        str(tmp_path), "tesseract", "tessdata"
    )

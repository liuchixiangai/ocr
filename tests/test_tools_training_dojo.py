"""The Training Dojo annotation dialog (src/training_dojo.py), headless."""

import json
import os

import cv2
import numpy as np
import pytest

pytestmark = pytest.mark.qt

SAMPLES = {"a": "11", "b": "22", "c": "33", "d": "44"}


def write_sample(folder, base_name, text):
    ok, png = cv2.imencode(".png", np.full((10, 20), 255, np.uint8))
    assert ok
    (folder / f"{base_name}.png").write_bytes(png.tobytes())
    (folder / f"{base_name}_gray.png").write_bytes(png.tobytes())
    (folder / f"{base_name}.txt").write_text(text, encoding="utf-8")


@pytest.fixture
def samples_folder(tmp_path):
    folder = tmp_path / "training"
    folder.mkdir()
    for base_name, text in SAMPLES.items():
        write_sample(folder, base_name, text)
    return folder


@pytest.fixture
def make_dojo(qapp):
    from training_dojo import TrainingDojo

    dojos = []

    def _make(folder):
        dojo = TrainingDojo(str(folder))
        dojo.show()
        qapp.processEvents()
        dojos.append(dojo)
        return dojo

    yield _make
    for dojo in dojos:
        dojo.close()
        dojo.deleteLater()
    qapp.processEvents()


def key(widget, qt_key, modifiers=None):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.keyClick(widget, qt_key, modifiers or Qt.KeyboardModifier.NoModifier)


def current(dojo):
    return dojo.image_files[dojo.current_index]


def test_letters_are_typed_not_shortcuts(make_dojo, samples_folder):
    # N, B and U used to navigate / remove the approval even while typing.
    from PySide6.QtTest import QTest

    dojo = make_dojo(samples_folder)
    dojo.approve_annotation()
    edit = dojo.ui.lineEdit_text
    edit.clear()

    QTest.keyClicks(edit, "NBU nbu")

    assert edit.text() == "NBU nbu"
    assert current(dojo) == "a"
    assert "a" in dojo.approved_annotations


def test_ctrl_letter_shortcuts(make_dojo, samples_folder):
    from PySide6.QtCore import Qt

    dojo = make_dojo(samples_folder)
    edit = dojo.ui.lineEdit_text
    ctrl = Qt.KeyboardModifier.ControlModifier

    key(edit, Qt.Key.Key_N, ctrl)
    assert current(dojo) == "b"
    assert edit.text() == "22"
    key(edit, Qt.Key.Key_B, ctrl)
    assert current(dojo) == "a"

    key(edit, Qt.Key.Key_Return)  # approve and go to the next image
    assert dojo.approved_annotations == {"a": "11"}
    assert current(dojo) == "b"
    key(edit, Qt.Key.Key_B, ctrl)
    key(edit, Qt.Key.Key_U, ctrl)
    assert dojo.approved_annotations == {}
    assert current(dojo) == "a"
    approved_file = samples_folder / "approved_annotations.json"
    assert json.loads(approved_file.read_text(encoding="utf-8")) == {}


def test_altgr_letters_are_not_shortcuts(make_dojo, samples_folder):
    # AltGr arrives as Ctrl+Alt on Windows and types e.g. "ń" on Polish layouts
    from PySide6.QtCore import Qt

    dojo = make_dojo(samples_folder)
    altgr = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier

    key(dojo.ui.lineEdit_text, Qt.Key.Key_N, altgr)

    assert current(dojo) == "a"


def test_enter_with_only_undone_does_not_skip_images(make_dojo, samples_folder):
    # Approving with "Only Undone" on moved on twice, skipping an image.
    from PySide6.QtCore import Qt

    dojo = make_dojo(samples_folder)
    dojo.ui.pushButton_onlyUndone.click()
    assert dojo.show_only_undone

    key(dojo.ui.lineEdit_text, Qt.Key.Key_Return)
    assert current(dojo) == "b"
    key(dojo.ui.lineEdit_text, Qt.Key.Key_Return)
    assert current(dojo) == "c"

    assert set(dojo.approved_annotations) == {"a", "b"}
    listed = [
        dojo.ui.listWidget_files.item(row).data(Qt.ItemDataRole.UserRole)
        for row in range(dojo.ui.listWidget_files.count())
    ]
    assert listed == ["c", "d"]


def test_clicking_a_filtered_list_opens_that_item(make_dojo, samples_folder):
    # List rows were used as image indices, which differ while filtering.
    dojo = make_dojo(samples_folder)
    dojo.approve_annotation()  # "a"
    dojo.ui.pushButton_onlyUndone.click()
    list_widget = dojo.ui.listWidget_files
    assert list_widget.count() == 3

    dojo.load_selected_item(list_widget.item(1))

    assert current(dojo) == "c"
    assert dojo.ui.lineEdit_text.text() == "33"


def test_unapproving_restores_the_right_list_item(make_dojo, samples_folder):
    from PySide6.QtCore import Qt

    dojo = make_dojo(samples_folder)
    dojo.current_index = 2
    dojo.load_current_item()
    dojo.approve_annotation()  # "c"
    item = dojo.find_list_item("c")
    assert item.text() == "✓ c"

    dojo.unapprove_annotation()

    assert item.text() == "c"
    assert item.data(Qt.ItemDataRole.ForegroundRole) is None
    assert dojo.find_list_item("a").text() == "a"


def test_edits_are_saved_as_utf8_when_navigating(make_dojo, samples_folder):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    dojo = make_dojo(samples_folder)
    edit = dojo.ui.lineEdit_text
    edit.clear()
    QTest.keyClicks(edit, "1ª")
    key(edit, Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier)

    assert current(dojo) == "b"
    assert (samples_folder / "a.txt").read_text(encoding="utf-8") == "1ª"


@pytest.mark.parametrize("folder_exists", [True, False])
def test_no_samples(make_dojo, tmp_path, folder_exists):
    from PySide6.QtCore import Qt

    folder = tmp_path / "empty"
    if folder_exists:
        folder.mkdir()
    dojo = make_dojo(folder)
    edit = dojo.ui.lineEdit_text
    ctrl = Qt.KeyboardModifier.ControlModifier

    # used to raise ZeroDivisionError / IndexError
    for qt_key, modifiers in [
        (Qt.Key.Key_Return, None),
        (Qt.Key.Key_Down, None),
        (Qt.Key.Key_Up, None),
        (Qt.Key.Key_Down, ctrl),
        (Qt.Key.Key_Up, ctrl),
        (Qt.Key.Key_N, ctrl),
        (Qt.Key.Key_U, ctrl),
    ]:
        key(edit, qt_key, modifiers)
    dojo.ui.pushButton_next.click()
    dojo.ui.pushButton_onlyUndone.click()

    assert dojo.current_index == -1
    assert dojo.approved_annotations == {}


def test_corrupt_approved_annotations_file(make_dojo, samples_folder):
    (samples_folder / "approved_annotations.json").write_text(
        "{not json", encoding="utf-8"
    )

    dojo = make_dojo(samples_folder)

    assert dojo.approved_annotations == {}
    dojo.approve_annotation()
    approved = json.loads(
        (samples_folder / "approved_annotations.json").read_text(encoding="utf-8")
    )
    assert approved == {"a": "11"}


def test_missing_text_file(make_dojo, samples_folder):
    dojo = make_dojo(samples_folder)
    os.remove(samples_folder / "b.txt")

    dojo.next_image()

    assert current(dojo) == "b"
    assert dojo.ui.lineEdit_text.text() == ""

"""OCR training data collection (src/ocr_training_data.py): the settings dialog
and the save path the OCR thread calls on every detection cycle."""

import logging
import os
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from PySide6.QtWidgets import QDialog

import ocr_training_data
from ocr_training_data import (
    FOLDER_KEY,
    LEGACY_FOLDER_KEY,
    MAX_SIZE_KEY,
    RETRY_INTERVAL_SECONDS,
    SAVE_KEY,
    SETTINGS_FILE,
    OCRTrainingDataOptions,
)
from storage import fetch_data, store_data


@pytest.fixture
def frame():
    """A synthetic binary and gray frame (120x200) with distinct pixel values."""
    gray = (np.arange(120 * 200, dtype=np.uint32) % 251).astype(np.uint8)
    gray = gray.reshape(120, 200)
    _, binary = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
    return binary, gray


@pytest.fixture
def clock(monkeypatch):
    """Controls ocr_training_data's time.monotonic(): clock.now += seconds."""
    fake = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(
        ocr_training_data, "time", SimpleNamespace(monotonic=lambda: fake.now)
    )
    return fake


@pytest.fixture
def options(tmp_path):
    """Fresh options (subscribed in this test's settings registry), saving on,
    into tmp_path/training."""
    opts = OCRTrainingDataOptions()
    opts.ocr_training_data_folder = str(tmp_path / "training")
    opts.set_save_ocr_training_data(True)
    return opts


def samples_in(folder):
    """{uuid: label text} of the complete samples (png, gray png and txt)."""
    if not os.path.isdir(folder):
        return {}
    names = set(os.listdir(folder))
    samples = {}
    for name in names:
        if name.endswith(".txt"):
            base = name[: -len(".txt")]
            assert f"{base}.png" in names and f"{base}_gray.png" in names
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                samples[base] = f.read()
    return samples


def read_png(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)


# --- settings: issue #52 ---------------------------------------------------


@pytest.mark.qt
def test_dialog_folder_is_used_by_the_saver_and_after_restart(
    qapp, monkeypatch, tmp_path
):
    # Regression for #52: the dialog stored "ocr_training_data_save_folder" but
    # the saver read "ocr_training_data_folder", so the choice was ignored.
    opts = OCRTrainingDataOptions()
    monkeypatch.setattr(ocr_training_data, "ocr_training_data_options", opts)
    chosen = str(tmp_path / "my training")

    dialog = ocr_training_data.OCRTrainingDataDialog()
    dialog.ui.lineEdit_saveFolder.setText(chosen)
    dialog.ui.spinBox_maxSize.setValue(42)
    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted.value
    assert fetch_data(SETTINGS_FILE, FOLDER_KEY) == chosen
    assert opts.ocr_training_data_folder == chosen  # live, via the subscriber
    assert opts.ocr_training_data_max_size == 42
    assert os.path.isdir(chosen)
    # and after a restart, which reads the settings file again
    restarted = OCRTrainingDataOptions()
    assert restarted.ocr_training_data_folder == chosen
    reopened = ocr_training_data.OCRTrainingDataDialog()
    assert reopened.ui.lineEdit_saveFolder.text() == chosen


def test_legacy_folder_key_is_honored(tmp_path):
    # 0.1.x configs (and users who edited the JSON by hand, as in #52) only
    # have the key the old dialog wrote.
    legacy = str(tmp_path / "legacy")
    store_data(SETTINGS_FILE, LEGACY_FOLDER_KEY, legacy)
    assert OCRTrainingDataOptions().ocr_training_data_folder == legacy

    current = str(tmp_path / "current")
    store_data(SETTINGS_FILE, FOLDER_KEY, current)
    assert OCRTrainingDataOptions().ocr_training_data_folder == current


def test_default_folder_is_in_the_data_dir(data_dir):
    opts = OCRTrainingDataOptions()
    expected = os.path.join(str(data_dir), "ocr_training_data")
    assert opts.ocr_training_data_folder == expected
    opts.ocr_training_data_folder = ""
    assert opts.ocr_training_data_folder == expected


@pytest.mark.qt
def test_dialog_stores_the_default_folder_as_empty(qapp, monkeypatch):
    opts = OCRTrainingDataOptions()
    monkeypatch.setattr(ocr_training_data, "ocr_training_data_options", opts)
    dialog = ocr_training_data.OCRTrainingDataDialog()
    assert dialog.ui.lineEdit_saveFolder.text() == opts.ocr_training_data_folder

    dialog.accept()

    # "" keeps following the data dir (SCORESIGHT_DATA_DIR)
    assert fetch_data(SETTINGS_FILE, FOLDER_KEY) == ""


@pytest.mark.qt
def test_dialog_rejects_a_folder_that_cannot_be_created(qapp, monkeypatch, tmp_path):
    opts = OCRTrainingDataOptions()
    monkeypatch.setattr(ocr_training_data, "ocr_training_data_options", opts)
    warnings = []
    monkeypatch.setattr(
        ocr_training_data.QMessageBox, "warning", lambda *a: warnings.append(a)
    )
    a_file = tmp_path / "file.txt"
    a_file.write_text("x", encoding="utf-8")

    dialog = ocr_training_data.OCRTrainingDataDialog()
    dialog.ui.lineEdit_saveFolder.setText(str(a_file / "sub"))
    dialog.accept()

    assert len(warnings) == 1
    assert fetch_data(SETTINGS_FILE, FOLDER_KEY) is None
    assert dialog.result() != QDialog.DialogCode.Accepted.value


@pytest.mark.qt
def test_open_folder_uses_qdesktopservices(qapp, monkeypatch, tmp_path):
    # os.startfile only exists on Windows
    opts = OCRTrainingDataOptions()
    monkeypatch.setattr(ocr_training_data, "ocr_training_data_options", opts)
    opened = []
    monkeypatch.setattr(
        ocr_training_data.QDesktopServices,
        "openUrl",
        lambda url: opened.append(url) or True,
    )
    folder = tmp_path / "not yet created"

    dialog = ocr_training_data.OCRTrainingDataDialog()
    dialog.ui.lineEdit_saveFolder.setText(str(folder))
    dialog.ui.pushButton_openFolder.click()

    assert folder.is_dir()
    assert len(opened) == 1
    assert os.path.samefile(opened[0].toLocalFile(), folder)


# --- the save path ------------------------------------------------------------


def test_saves_patches_and_label(options, frame, make_result):
    binary, gray = frame
    result = make_result("Home Score", "12", rect=(10, 5, 30, 20))

    assert options.save_ocr_results_to_folder(binary, gray, [result]) == 1

    folder = options.ocr_training_data_folder
    samples = samples_in(folder)
    assert list(samples.values()) == ["12"]
    (base,) = samples
    patch = read_png(os.path.join(folder, f"{base}.png"))
    gray_patch = read_png(os.path.join(folder, f"{base}_gray.png"))
    np.testing.assert_array_equal(patch, binary[5:25, 10:40])
    np.testing.assert_array_equal(gray_patch, gray[5:25, 10:40])


def test_saving_disabled_writes_nothing(options, frame, make_result):
    options.set_save_ocr_training_data(False)
    result = make_result("Home Score", "12", rect=(10, 5, 30, 20))
    assert options.save_ocr_results_to_folder(*frame, [result]) == 0
    assert not os.path.exists(options.ocr_training_data_folder)


def test_non_ascii_folder(options, frame, make_result, tmp_path):
    # cv2.imwrite silently fails on Windows for paths outside the ANSI code page
    # (e.g. a user name with accents in the default folder's path).
    options.ocr_training_data_folder = str(tmp_path / "Données שלום")
    result = make_result("Home Score", "7", rect=(0, 0, 20, 20))

    assert options.save_ocr_results_to_folder(*frame, [result]) == 1
    assert list(samples_in(options.ocr_training_data_folder).values()) == ["7"]


def test_only_readings_are_saved(options, frame, make_result):
    results = [
        make_result("Home Score", "12", state="Success", rect=(0, 0, 20, 20)),
        # rejected by the regex / confidence filter: worth correcting
        make_result("Away Score", "1B", state="FailedFilter", rect=(20, 0, 20, 20)),
        # unchanged, e.g. not read again by "skip similar image"
        make_result("Period", "3", state="SameNoChange", rect=(60, 0, 20, 20)),
        make_result("Name 1", "", state="Empty", rect=(80, 0, 20, 20)),
        make_result("Name 2", "  ", state="Success", rect=(100, 0, 20, 20)),
        make_result("Fouls", "4", state="Success", rect=(120, 0, 0, 20)),
        make_result(
            "Score",
            "12 - 1B",
            state="Success",
            rect=(0, 0, 0, 0),
            templatefield=True,
            templatefield_text="{{Home Score}} - {{Away Score}}",
        ),
    ]

    assert options.save_ocr_results_to_folder(*frame, results) == 2
    assert sorted(samples_in(options.ocr_training_data_folder).values()) == [
        "12",
        "1B",
    ]


def test_patch_is_clipped_to_the_frame(options, frame, make_result):
    binary, gray = frame
    partly_outside = make_result("Home Score", "5", rect=(-5, 110, 20, 20))
    outside = make_result("Away Score", "6", rect=(300, 0, 20, 20))

    assert options.save_ocr_results_to_folder(binary, gray, [partly_outside, outside])

    folder = options.ocr_training_data_folder
    (base,) = samples_in(folder)
    np.testing.assert_array_equal(
        read_png(os.path.join(folder, f"{base}.png")), binary[110:120, 0:15]
    )


def test_results_are_snapshotted_before_any_io(
    options, frame, make_result, monkeypatch
):
    # Regression: the result objects are shared with the GUI thread, whose
    # ocrResult sets template fields (0x0 rect) to Success while the OCR thread
    # is saving. Cropping that rect gave an empty image, cv2.imwrite raised and
    # the exception ended the OCR thread.
    score = make_result("Home Score", "12", rect=(10, 10, 20, 20))
    template = make_result(
        "Score",
        "",
        state="Empty",
        rect=(0, 0, 0, 0),
        templatefield=True,
        templatefield_text="{{Home Score}}",
    )
    results = [score, template]
    real_folder_size = ocr_training_data.folder_size_bytes

    def gui_thread_edits_results(folder):
        # what MainWindow.ocrResult does to template fields, plus an edit to a
        # regular result, while the saver measures the folder
        template.result = "12"
        template.result_state = template.ResultState.Success
        score.result = "edited"
        score.setRect(0, 0, 0, 0)
        return real_folder_size(folder)

    monkeypatch.setattr(
        ocr_training_data, "folder_size_bytes", gui_thread_edits_results
    )

    assert options.save_ocr_results_to_folder(*frame, results) == 1
    assert list(samples_in(options.ocr_training_data_folder).values()) == ["12"]


def test_never_raises_and_logs_a_failure_once(
    options, frame, make_result, monkeypatch, caplog, clock
):
    result = make_result("Home Score", "12", rect=(0, 0, 20, 20))
    real_encode = ocr_training_data._encode_png
    attempts = []

    def failing_encode(image):
        attempts.append(image)
        raise OSError("disk full")

    monkeypatch.setattr(ocr_training_data, "_encode_png", failing_encode)

    with caplog.at_level(logging.ERROR):
        for _ in range(5):
            assert options.save_ocr_results_to_folder(*frame, [result]) == 0
        # paused: no attempt until the retry interval has passed
        assert len(attempts) == 1
        clock.now += RETRY_INTERVAL_SECONDS + 1
        assert options.save_ocr_results_to_folder(*frame, [result]) == 0
        assert len(attempts) == 2

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None
    # nothing half-written is left behind
    assert os.listdir(options.ocr_training_data_folder) == []

    # recovers once writing works again
    monkeypatch.setattr(ocr_training_data, "_encode_png", real_encode)
    clock.now += RETRY_INTERVAL_SECONDS + 1
    assert options.save_ocr_results_to_folder(*frame, [result]) == 1


@pytest.mark.parametrize(
    "bad_frame",
    [
        (None, None),
        (np.zeros((0, 0), np.uint8), np.zeros((0, 0), np.uint8)),
    ],
)
def test_bad_input_never_raises(options, make_result, bad_frame):
    result = make_result("Home Score", "12", rect=(0, 0, 20, 20))
    assert options.save_ocr_results_to_folder(*bad_frame, [result]) == 0
    assert options.save_ocr_results_to_folder(*bad_frame, [object()]) == 0


def test_folder_is_measured_once_not_every_cycle(
    options, frame, make_result, monkeypatch
):
    measured = []
    real_folder_size = ocr_training_data.folder_size_bytes

    def counting_folder_size(folder):
        measured.append(folder)
        return real_folder_size(folder)

    monkeypatch.setattr(ocr_training_data, "folder_size_bytes", counting_folder_size)
    result = make_result("Home Score", "12", rect=(0, 0, 20, 20))

    for _ in range(10):
        options.save_ocr_results_to_folder(*frame, [result])

    assert len(measured) == 1
    assert len(samples_in(options.ocr_training_data_folder)) == 10
    # the cached size tracks what was written
    assert options._folder_size == real_folder_size(options.ocr_training_data_folder)

    # a new folder is measured again
    other = os.path.join(os.path.dirname(options.ocr_training_data_folder), "other")
    store_data(SETTINGS_FILE, FOLDER_KEY, other)
    assert options.ocr_training_data_folder == other
    options.save_ocr_results_to_folder(*frame, [result])
    assert measured[-1] == other
    assert len(measured) == 2


def test_max_size_stops_saving_logs_once_and_resumes(
    options, frame, make_result, caplog, clock
):
    folder = options.ocr_training_data_folder
    os.makedirs(folder)
    with open(os.path.join(folder, "existing.bin"), "wb") as f:
        f.write(b"x" * 2048)
    options.set_ocr_training_data_max_size(0.001)  # ~1 KB, already exceeded
    result = make_result("Home Score", "12", rect=(0, 0, 20, 20))

    with caplog.at_level(logging.ERROR):
        for _ in range(5):
            assert options.save_ocr_results_to_folder(*frame, [result]) == 0
            clock.now += RETRY_INTERVAL_SECONDS + 1  # re-measures, still full
    assert len([r for r in caplog.records if "maximum size" in r.getMessage()]) == 1
    assert samples_in(folder) == {}

    # room is made by moving samples out: noticed after the retry interval
    os.remove(os.path.join(folder, "existing.bin"))
    assert options.save_ocr_results_to_folder(*frame, [result]) == 1

    # raising the limit applies at once
    store_data(SETTINGS_FILE, MAX_SIZE_KEY, 10)
    assert options.ocr_training_data_max_size == 10
    assert options.save_ocr_results_to_folder(*frame, [result]) == 1


def test_enabling_saving_from_settings(frame, make_result, tmp_path):
    opts = OCRTrainingDataOptions()
    opts.ocr_training_data_folder = str(tmp_path / "training")
    result = make_result("Home Score", "12", rect=(0, 0, 20, 20))
    assert opts.save_ocr_results_to_folder(*frame, [result]) == 0

    store_data(SETTINGS_FILE, SAVE_KEY, True)

    assert opts.save_ocr_training_data is True
    assert opts.save_ocr_results_to_folder(*frame, [result]) == 1


def test_snapshot_samples_can_be_passed_directly(options, frame, make_result):
    # The OCR thread can snapshot before emitting the results to the GUI.
    samples = ocr_training_data.snapshot_training_samples(
        [make_result("Home Score", "12", rect=(0, 0, 20, 20))]
    )
    assert samples == [
        ocr_training_data.OCRTrainingSample("Home Score", "12", 0, 0, 20, 20)
    ]
    assert options.save_ocr_results_to_folder(*frame, samples) == 1

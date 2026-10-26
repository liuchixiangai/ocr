import os
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass

import cv2
from numpy import ndarray
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from sc_logging import logger
from storage import fetch_data, get_data_dir, store_data, subscribe_to_data
from text_detection_target import TextDetectionTargetWithResult
from ui_ocr_training_data_dialog import Ui_OCRTrainingDataDialog

SETTINGS_FILE = "scoresight.json"
SAVE_KEY = "save_ocr_training_data"
FOLDER_KEY = "ocr_training_data_folder"
# ScoreSight 0.1.x's dialog stored the folder under this key, which nothing read
# (issue #52). It is still honored when FOLDER_KEY was never written.
LEGACY_FOLDER_KEY = "ocr_training_data_save_folder"
MAX_SIZE_KEY = "ocr_training_data_max_size"
DEFAULT_MAX_SIZE_MB = 10

# While saving is paused (the folder is full, or a write failed), how long to
# wait before measuring the folder again and retrying. Lets saving resume after
# samples are moved out of the folder, without walking it on every OCR cycle.
RETRY_INTERVAL_SECONDS = 30.0

_ResultState = TextDetectionTargetWithResult.ResultState


def default_ocr_training_data_folder() -> str:
    return os.path.join(get_data_dir(), "ocr_training_data")


def folder_size_bytes(folder: str) -> int:
    """Total size of the files in folder and its subfolders (0 if it is missing)."""
    total = 0
    for root, _, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                # removed while walking
                pass
    return total


@dataclass(frozen=True)
class OCRTrainingSample:
    """What is saved for one box: the OCR text and the box's pixel rectangle."""

    name: str
    text: str
    x: int
    y: int
    width: int
    height: int


def snapshot_training_samples(results) -> list[OCRTrainingSample]:
    """Copy the results worth saving into immutable OCRTrainingSample objects.

    The result objects are shared with the GUI thread (a queued signal copies the
    list, not its items), and MainWindow.ocrResult rewrites the text and state of
    template fields in place while the OCR thread carries on. Taking this
    snapshot first means the save never sees a half-updated result.

    Kept: Success readings, and FailedFilter readings (text rejected by the
    format regex or the confidence threshold, which is worth correcting in the
    Training Dojo). Skipped: empty and unchanged (SameNoChange) results, which
    include boxes that "skip similar image" didn't read again, template fields
    (their text doesn't come from the image) and boxes without an area. Items
    that already are OCRTrainingSample are kept as is.
    """
    samples = []
    for result in results:
        if isinstance(result, OCRTrainingSample):
            samples.append(result)
            continue
        settings = result.settings or {}
        if settings.get("templatefield"):
            continue
        text = result.result
        state = result.result_state
        if not isinstance(text, str) or not text.strip():
            continue
        if state not in (_ResultState.Success, _ResultState.FailedFilter):
            continue
        sample = OCRTrainingSample(
            result.name,
            text,
            int(result.x()),
            int(result.y()),
            int(result.width()),
            int(result.height()),
        )
        if sample.width >= 1 and sample.height >= 1:
            samples.append(sample)
    return samples


def crop_patch(image: ndarray, sample: OCRTrainingSample) -> ndarray | None:
    """The sample's rectangle cut from image, clipped to the image, or None if
    nothing of it is inside the image."""
    height, width = image.shape[:2]
    x0, y0 = max(0, sample.x), max(0, sample.y)
    x1 = min(width, sample.x + sample.width)
    y1 = min(height, sample.y + sample.height)
    if x1 <= x0 or y1 <= y0:
        return None
    return image[y0:y1, x0:x1]


def _write_bytes(path: str, data: bytes) -> int:
    with open(path, "wb") as f:
        f.write(data)
    return len(data)


def _encode_png(image: ndarray) -> bytes:
    # cv2.imwrite can't open paths with non-ASCII characters on Windows (it just
    # returns False), so encode in memory and write the file from Python.
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("PNG encoding failed")
    return buffer.tobytes()


class OCRTrainingDataOptions:
    """The OCR training data settings, and the code that saves the samples.

    The settings are updated by storage subscribers, on whatever thread stored
    them; save_ocr_results_to_folder runs on the OCR thread. The folder-size
    bookkeeping is guarded by a lock that is never held during file I/O.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # Cached size in bytes of the folder that _measured_folder names, kept
        # up to date as samples are written. None: measure it on the next save.
        self._folder_size: int | None = None
        self._measured_folder: str | None = None
        # Bumped when a setting changes, so a save that started before the
        # change doesn't store a size measured under the old settings.
        self._generation = 0
        # time.monotonic() before which saving is paused (folder full or failing)
        self._paused_until = 0.0
        # The problem last logged; each problem is logged once, not every cycle.
        self._reported_problem: str | None = None

        self.save_ocr_training_data = fetch_data(SETTINGS_FILE, SAVE_KEY, False)
        subscribe_to_data(SETTINGS_FILE, SAVE_KEY, self.set_save_ocr_training_data)

        folder = fetch_data(SETTINGS_FILE, FOLDER_KEY, None)
        if folder is None:
            folder = fetch_data(SETTINGS_FILE, LEGACY_FOLDER_KEY, None)
        self._folder_setting = folder or ""
        subscribe_to_data(SETTINGS_FILE, FOLDER_KEY, self.set_ocr_training_data_folder)

        self.ocr_training_data_max_size = fetch_data(
            SETTINGS_FILE, MAX_SIZE_KEY, DEFAULT_MAX_SIZE_MB
        )
        subscribe_to_data(
            SETTINGS_FILE, MAX_SIZE_KEY, self.set_ocr_training_data_max_size
        )

    @property
    def ocr_training_data_folder(self) -> str:
        """The folder samples are saved to: the configured one, or the default
        folder in the data directory if none is set."""
        return self._folder_setting or default_ocr_training_data_folder()

    @ocr_training_data_folder.setter
    def ocr_training_data_folder(self, value):
        self.set_ocr_training_data_folder(value)

    def _settings_changed(self):
        with self._lock:
            self._generation += 1
            self._folder_size = None
            self._measured_folder = None
            self._paused_until = 0.0
            self._reported_problem = None

    def set_save_ocr_training_data(self, value):
        self.save_ocr_training_data = bool(value)
        if value:
            # measure the folder again: samples may have been removed meanwhile
            self._settings_changed()

    def set_ocr_training_data_folder(self, value):
        self._folder_setting = value or ""
        self._settings_changed()

    def set_ocr_training_data_max_size(self, value):
        self.ocr_training_data_max_size = value
        self._settings_changed()

    def _max_size_bytes(self) -> float:
        try:
            return float(self.ocr_training_data_max_size) * 1024 * 1024
        except (TypeError, ValueError):
            return DEFAULT_MAX_SIZE_MB * 1024 * 1024

    def _report_problem(self, problem: str, message: str, *args, exc_info=False):
        with self._lock:
            if self._reported_problem == problem:
                return
            self._reported_problem = problem
        logger.error(message, *args, exc_info=exc_info)

    def save_ocr_results_to_folder(
        self,
        binary: ndarray,
        gray: ndarray,
        result: list,
    ) -> int:
        """Save each new reading in result as <id>.png (the binary patch),
        <id>_gray.png and <id>.txt (the recognized text) in the training data
        folder, while the folder is under its maximum size.

        result holds TextDetectionTargetWithResult objects (or
        OCRTrainingSample snapshots); see snapshot_training_samples for which
        are saved. Runs on the OCR thread and never raises: problems are logged
        once, and saving pauses for RETRY_INTERVAL_SECONDS. Returns the number
        of samples saved.
        """
        if not self.save_ocr_training_data:
            return 0
        try:
            # first, before any I/O gives the GUI thread a chance to edit them
            samples = snapshot_training_samples(result)
            if not samples:
                return 0
            return self._save_samples(binary, gray, samples)
        except Exception:
            self._report_problem(
                "error",
                "Saving OCR training data failed; retrying in %d s",
                RETRY_INTERVAL_SECONDS,
                exc_info=True,
            )
            self._pause()
            return 0

    def _pause(self):
        with self._lock:
            self._paused_until = time.monotonic() + RETRY_INTERVAL_SECONDS
            # measure again before retrying: files may have been added or removed
            self._folder_size = None
            self._measured_folder = None

    def _save_samples(
        self, binary: ndarray, gray: ndarray, samples: list[OCRTrainingSample]
    ) -> int:
        with self._lock:
            if time.monotonic() < self._paused_until:
                return 0
            self._paused_until = 0.0
            generation = self._generation
            folder = self.ocr_training_data_folder
            folder_size = self._folder_size if self._measured_folder == folder else None
        max_size = self._max_size_bytes()

        if folder_size is None:
            os.makedirs(folder, exist_ok=True)
            folder_size = folder_size_bytes(folder)

        written = 0
        saved = 0
        try:
            for sample in samples:
                binary_patch = crop_patch(binary, sample)
                gray_patch = crop_patch(gray, sample)
                if binary_patch is None or gray_patch is None:
                    continue
                if folder_size + written >= max_size:
                    self._report_problem(
                        "full",
                        "The OCR training data folder %s reached its maximum size "
                        "of %s MB; not saving more samples until there is room",
                        folder,
                        self.ocr_training_data_max_size,
                    )
                    self._pause()
                    return saved
                written += self._write_sample(folder, sample, binary_patch, gray_patch)
                saved += 1
        finally:
            with self._lock:
                # unless the settings changed, or saving paused, meanwhile
                if self._generation == generation and not self._paused_until:
                    self._measured_folder = folder
                    self._folder_size = folder_size + written
                if saved:
                    self._reported_problem = None
        return saved

    def _write_sample(
        self,
        folder: str,
        sample: OCRTrainingSample,
        binary_patch: ndarray,
        gray_patch: ndarray,
    ) -> int:
        """Write one sample's three files and return their total size. The .txt
        is written last, so the Training Dojo never lists a sample without its
        images; a sample that fails half-way is removed."""
        files = [
            (".png", _encode_png(binary_patch)),
            ("_gray.png", _encode_png(gray_patch)),
            (".txt", sample.text.encode("utf-8")),
        ]
        base_path = os.path.join(folder, str(uuid.uuid4()))
        written_paths = []
        size = 0
        try:
            for suffix, data in files:
                path = base_path + suffix
                written_paths.append(path)
                size += _write_bytes(path, data)
        except BaseException:
            for path in written_paths:
                try:
                    os.remove(path)
                except OSError:
                    pass
            raise
        return size


ocr_training_data_options = OCRTrainingDataOptions()


def zip_folder(folder_path, zip_path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, folder_path)
                zipf.write(file_path, arcname)


class OCRTrainingDataDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.ui = Ui_OCRTrainingDataDialog()
        self.ui.setupUi(self)
        # OK runs accept() and Cancel reject() (connected in the .ui file)
        self.ui.toolButton_chooseSaveFolder.clicked.connect(self.choose_save_folder)
        self.ui.lineEdit_saveFolder.setText(
            ocr_training_data_options.ocr_training_data_folder
        )
        self.ui.spinBox_maxSize.setValue(
            int(ocr_training_data_options.ocr_training_data_max_size)
        )
        self.ui.pushButton_openFolder.clicked.connect(self.open_folder)
        self.ui.pushButton_saveZipFile.clicked.connect(self.save_zip_file)
        self.ui.pushButton_openTrainingDojo.clicked.connect(self.open_training_dojo)

    def selected_folder(self) -> str:
        """The folder in the dialog, or the default folder if the field is empty."""
        return (
            self.ui.lineEdit_saveFolder.text().strip()
            or default_ocr_training_data_folder()
        )

    def _ensure_folder(self, folder: str) -> bool:
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            logger.error(f"Can't create the OCR training data folder {folder}: {e}")
            QMessageBox.warning(
                self,
                self.tr("OCR Training Data"),
                self.tr("Can't use this folder for OCR training data:")
                + f"\n{folder}\n\n{e}",
            )
            return False
        return True

    def open_training_dojo(self):
        logger.debug("Opening OCR training dojo")
        folder = self.selected_folder()
        if not self._ensure_folder(folder):
            return
        from training_dojo import TrainingDojo

        training_dojo = TrainingDojo(folder)
        training_dojo.exec()

    def save_zip_file(self):
        logger.debug("Saving OCR training data zip file")
        folder = self.selected_folder()
        # Create a temporary file to store the zip
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp_zip:
            temp_zip_path = temp_zip.name
        # Zip the folder
        try:
            zip_folder(folder, temp_zip_path)
        except Exception as e:
            QMessageBox.critical(None, "Error", f"Failed to create zip file: {str(e)}")
            os.unlink(temp_zip_path)
            return

        # Ask user where to save the zip file
        save_path, _ = QFileDialog.getSaveFileName(
            None, "Save Zip File", "", "Zip Files (*.zip)"
        )
        if save_path:
            if not save_path.endswith(".zip"):
                save_path += ".zip"
            try:
                # Copy the temp zip to the chosen location
                with open(temp_zip_path, "rb") as src, open(save_path, "wb") as dst:
                    dst.write(src.read())
                logger.info(f"Zip file saved to {save_path}")
            except Exception as e:
                QMessageBox.critical(
                    None, "Error", f"Failed to save zip file: {str(e)}"
                )
        else:
            logger.info("Zip file was not saved.")

        # Clean up the temporary file
        os.unlink(temp_zip_path)

    def open_folder(self):
        folder = self.selected_folder()
        logger.debug(f"Opening OCR training data save folder {folder}")
        if not self._ensure_folder(folder):
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
            logger.warning(f"Could not open {folder} in the file manager")

    def choose_save_folder(self):
        logger.debug("Choosing OCR training data save folder")
        folder = QFileDialog.getExistingDirectory(
            self,
            self.tr("Choose OCR training data save folder"),
            self.selected_folder(),
        )
        if folder:
            self.ui.lineEdit_saveFolder.setText(os.path.normpath(folder))

    def save_settings(self) -> bool:
        """Store the dialog's settings. Returns False (and stores nothing) if the
        folder can't be created."""
        logger.debug("Saving OCR training data settings")
        folder = self.selected_folder()
        if not self._ensure_folder(folder):
            return False
        # The default folder is stored as "", so it keeps following the data
        # directory (SCORESIGHT_DATA_DIR).
        if os.path.normcase(os.path.abspath(folder)) == os.path.normcase(
            os.path.abspath(default_ocr_training_data_folder())
        ):
            folder = ""
        store_data(SETTINGS_FILE, FOLDER_KEY, folder)
        store_data(SETTINGS_FILE, MAX_SIZE_KEY, self.ui.spinBox_maxSize.value())
        return True

    def accept(self):
        if self.save_settings():
            super().accept()

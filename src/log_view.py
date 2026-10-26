import os

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog

import sc_logging
from sc_logging import logger
from ui_log_view import Ui_Dialog as Ui_LogViewerDialog
from storage import store_data, fetch_data

# How often the log is re-read while the dialog is visible.
REFRESH_INTERVAL_MS = 1000
# Only the end of the log is shown.
MAX_LINES = 1000
# At most this much is read from the end of the file on each refresh, so a long
# session's log isn't read in full every second.
MAX_TAIL_BYTES = 512 * 1024


def read_log_tail(path: str, max_lines: int = MAX_LINES) -> str:
    """The last max_lines lines of the log file at path (decoded as UTF-8; bytes
    that aren't valid UTF-8 are replaced, never an error)."""
    with open(path, "rb") as log_file:
        log_file.seek(0, os.SEEK_END)
        start = max(0, log_file.tell() - MAX_TAIL_BYTES)
        log_file.seek(start)
        data = log_file.read()
    if start > 0:
        # drop the line the read started in the middle of
        data = data[data.find(b"\n") + 1 :]
    lines = data.decode("utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


class LogViewerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_LogViewerDialog()
        self.ui.setupUi(self)
        # None when file logging could not be set up (logs go to stderr)
        self.log_file_path = sc_logging.log_file_path
        self.current_log_data = None
        # (size, mtime) of the log file when it was last read
        self._last_log_stat = None
        # Refreshes only while the dialog is shown (see showEvent/hideEvent).
        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_INTERVAL_MS)
        self.timer.timeout.connect(self.update_ui)
        self.ui.pushButton_openlogfolder.clicked.connect(self.open_log_folder)
        self.ui.checkBox_openOnStartup.clicked.connect(self.open_on_startup)
        self.ui.checkBox_openOnStartup.setChecked(
            fetch_data("scoresight.json", "open_on_startup", False)
        )
        if self.log_file_path is None:
            self.show_text(
                self.tr(
                    "The log file could not be created, so this session's log is "
                    "only written to the console (standard error)."
                )
            )

    def showEvent(self, event):
        super().showEvent(event)
        self.update_ui()
        if self.log_file_path is not None:
            self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def open_on_startup(self):
        store_data(
            "scoresight.json",
            "open_on_startup",
            self.ui.checkBox_openOnStartup.isChecked(),
        )

    def open_log_folder(self):
        log_dir = sc_logging.get_log_dir()
        if not os.path.isdir(log_dir):
            logger.warning(f"The log folder {log_dir} does not exist")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(log_dir)):
            logger.warning(f"Could not open the log folder {log_dir}")

    def update_ui(self):
        if self.log_file_path is None:
            return
        try:
            stat = os.stat(self.log_file_path)
            log_stat = (stat.st_size, stat.st_mtime_ns)
            if log_stat == self._last_log_stat:
                return
            log_data = read_log_tail(self.log_file_path)
        except OSError as e:
            # e.g. the file was deleted; keep trying, it may come back
            log_stat = None
            log_data = self.tr("Can't read the log file {path}: {error}").format(
                path=self.log_file_path, error=e
            )
        self._last_log_stat = log_stat
        if log_data == self.current_log_data:
            return
        self.show_text(log_data)

    def show_text(self, log_data: str):
        self.current_log_data = log_data
        text_edit = self.ui.textEdit_log
        scrollbar = text_edit.verticalScrollBar()
        position = scrollbar.value()
        text_edit.setPlainText(log_data)
        if self.ui.checkBox_autoScroll.isChecked():
            # scroll to the bottom
            scrollbar.setValue(scrollbar.maximum())
            self.ui.scrollArea.ensureWidgetVisible(text_edit)
        else:
            # keep the place the user scrolled to
            scrollbar.setValue(position)

"""Update now: the update dialog's button that updates an installed ScoreSight
in place on Windows (app_update.py does the work).

AppUpdater asks first while a source is open (the outputs stop until the new
version runs), downloads and checks the installer on a worker thread behind a
progress dialog, starts it, and closes the main window once Setup says it is
installing. If anything fails, or Setup ends without installing (the UAC prompt
was declined), ScoreSight keeps running and offers the release page.
"""

from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QProgressDialog

import app_update
from sc_logging import get_log_dir, logger
from update_check import UpdateCheckResult

# how often the ready file and Setup's process are looked at
POLL_MS = 250
READY_FILE_NAME = "installing.txt"


class AppUpdater(QObject):
    # (done, total) bytes of the download, from the worker thread
    _progress = Signal(int, int)
    # the installer's path, or an app_update.UpdateError, from the worker thread
    _downloaded = Signal(object)

    def __init__(self, window, installed_folder=app_update.installed_folder):
        super().__init__(window)
        self.window = window
        self._installed_folder = installed_folder
        self.result: UpdateCheckResult | None = None
        self.progress_dialog: QProgressDialog | None = None
        self.cancel_download = threading.Event()
        self.process = None
        self.ready_file: str | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.poll)
        self._progress.connect(self.progress)
        self._downloaded.connect(self.downloaded)

    def available(self) -> bool:
        """Whether this ScoreSight can update itself (installed on Windows)."""
        return self._installed_folder() is not None

    @property
    def busy(self) -> bool:
        return self.result is not None

    def start(self, result: UpdateCheckResult):
        """Update to result's version (Update now)."""
        if self.busy or result.installer is None:
            return
        version = result.installer.version
        if self.window.image_viewer is not None and not self.window.confirm(
            QCoreApplication.translate("AppUpdate", "Update ScoreSight now?"),
            QCoreApplication.translate(
                "AppUpdate",
                "ScoreSight will close to install version {version}, then start "
                "again. The source and the outputs stop until then.",
            ).format(version=version),
            QCoreApplication.translate("AppUpdate", "Update now"),
        ):
            return
        logger.info("Update now: updating to %s", version)
        self.result = result
        self.cancel_download.clear()
        dialog = QProgressDialog(self.window)
        dialog.setWindowTitle(QCoreApplication.translate("AppUpdate", "Update"))
        dialog.setLabelText(
            QCoreApplication.translate(
                "AppUpdate", "Downloading ScoreSight {version}…"
            ).format(version=version)
        )
        dialog.setRange(0, result.installer.size)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.canceled.connect(self.cancel_download.set)
        dialog.show()
        self.progress_dialog = dialog

        installer = result.installer

        def work():
            try:
                path = app_update.download_installer(
                    installer,
                    progress=lambda done, total: self._progress.emit(done, total),
                    cancelled=self.cancel_download.is_set,
                )
                app_update.check_signature(path)
                return path
            except app_update.UpdateError as e:
                return e
            except Exception as e:
                logger.exception("Update now failed")
                return app_update.UpdateError(str(e))

        def target():
            outcome = work()
            try:
                # delivered on the GUI thread (queued)
                self._downloaded.emit(outcome)
            except RuntimeError:
                logger.debug("The update download finished after the window closed")

        threading.Thread(target=target, name="Update download", daemon=True).start()

    def progress(self, done: int, total: int):
        dialog = self.progress_dialog
        if dialog is None:
            return
        dialog.setMaximum(total)
        dialog.setValue(min(done, total))
        if done >= total:
            dialog.setLabelText(
                QCoreApplication.translate("AppUpdate", "Checking the download…")
            )

    def downloaded(self, outcome):
        if isinstance(outcome, app_update.UpdateCancelled):
            logger.info("Update now: the download was cancelled")
            self.finish()
            return
        if isinstance(outcome, app_update.UpdateError):
            logger.warning("Update now failed: %s", outcome)
            self.fail(str(outcome))
            return
        folder = os.path.dirname(outcome)
        self.ready_file = os.path.join(folder, READY_FILE_NAME)
        mutex = app_update.mutex_name()
        # released when this process ends: Setup waits for it
        app_update.hold_mutex(mutex)
        # Setup's log, next to ScoreSight's (Setup stops if it can't write it)
        log_dir = get_log_dir()
        log_file = (
            os.path.join(
                log_dir, f"scoresight_setup_{time.strftime('%Y%m%d-%H%M%S')}.log"
            )
            if os.path.isdir(log_dir)
            else None
        )
        # this installation, not another one Setup would find first
        folder = self._installed_folder()
        mode = app_update.install_mode(folder) if folder else None
        try:
            self.process = app_update.start_installer(
                outcome, self.ready_file, mutex, log_file, folder, mode
            )
        except app_update.UpdateError as e:
            logger.warning("Update now failed: %s", e)
            self.fail(str(e))
            return
        dialog = self.progress_dialog
        if dialog is not None:
            dialog.setCancelButton(None)
            dialog.setRange(0, 0)
            dialog.setLabelText(
                QCoreApplication.translate(
                    "AppUpdate",
                    "Starting the installer… If Windows asks whether to allow it "
                    "to make changes, choose Yes.",
                )
            )
        self.timer.start()

    def poll(self):
        """Close ScoreSight once Setup is installing; report it if Setup ended
        before."""
        if self.ready_file and os.path.exists(self.ready_file):
            self.timer.stop()
            logger.info("Update now: the installer is installing, closing")
            if self.progress_dialog is not None:
                self.progress_dialog.close()
                self.progress_dialog = None
            # it asked already (start)
            self.window.close_without_asking = True
            self.window.close()
            return
        if self.process is None:
            self.timer.stop()
            return
        code = self.process.poll()
        if code is None:
            return
        self.timer.stop()
        logger.warning("Update now: the installer ended (%s) before installing", code)
        self.fail(
            QCoreApplication.translate(
                "AppUpdate",
                "The installer ended before installing the new version (it may "
                "not have been allowed to make changes).",
            )
        )

    def fail(self, reason: str):
        """Tell what went wrong and offer the release page."""
        release_url = self.result.release_url if self.result is not None else None
        self.finish()
        box = QMessageBox(
            QMessageBox.Icon.Warning,
            QCoreApplication.translate("AppUpdate", "ScoreSight wasn't updated"),
            QCoreApplication.translate(
                "AppUpdate",
                "{reason}\n\nScoreSight keeps running. You can download the new "
                "version from the release page instead.",
            ).format(reason=reason),
            parent=self.window,
        )
        page = box.addButton(
            QCoreApplication.translate("AppUpdate", "Open the release page"),
            QMessageBox.ButtonRole.AcceptRole,
        )
        box.addButton(QMessageBox.StandardButton.Close)
        box.setDefaultButton(page)
        box.exec()
        if release_url and box.clickedButton() is page:
            QDesktopServices.openUrl(QUrl(release_url))
        box.deleteLater()

    def finish(self):
        """Back to idle (nothing is running)."""
        self.timer.stop()
        if self.progress_dialog is not None:
            self.progress_dialog.close()
            self.progress_dialog = None
        self.result = None
        self.process = None
        self.ready_file = None

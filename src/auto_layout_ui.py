"""The ✨ Auto crop and ✨ Auto layout buttons of the Adjust picture row.

A click takes the last picture the capture thread transformed (the whole
rotated frame for Auto crop, the picture the boxes sit on for Auto layout),
runs the layout model (auto_layout) on a worker thread and hands the result to
the main window: Auto crop sets the crop around the scoreboard (as Drag to crop
would), Auto layout places the boxes of the fields it found. The model is
downloaded the first time, after asking. While it runs, its button is checked
and says what it does; another click is ignored.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QCoreApplication, QObject, QRectF, QSizeF, Signal
from PySide6.QtWidgets import QMessageBox, QToolButton

import auto_layout
import theme
from sc_logging import logger

CROP = "crop"
LAYOUT = "layout"


def busy_text(kind: str, downloaded: tuple[int, int] | None) -> str:
    if downloaded is not None:
        done, total = downloaded
        return QCoreApplication.translate(
            "AutoLayout", "Downloading the AI model… {percent}%"
        ).format(percent=min(100, done * 100 // max(total, 1)))
    if kind == CROP:
        return QCoreApplication.translate("AutoLayout", "Finding the scoreboard…")
    return QCoreApplication.translate("AutoLayout", "Finding the fields…")


def size_mb(size: int) -> str:
    return f"{size / 1_000_000:.0f}"


class AutoLayoutUI(QObject):
    # (kind, result) from the worker thread: {"size", "detections"} or {"error"}
    _finished = Signal(str, object)
    # (done, total) bytes of the model's download, from the worker thread
    _progress = Signal(int, int)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        ui = window.ui
        self.buttons: dict[str, QToolButton] = {
            CROP: ui.toolButton_autoCrop,
            LAYOUT: ui.toolButton_autoLayout,
        }
        self.busy: str | None = None
        # what the picture was made with when the model started: a result for
        # another picture (the crop, rotation, mirror or source changed) is dropped
        self.started_for = None
        self.downloaded: tuple[int, int] | None = None
        for kind, button in self.buttons.items():
            button.clicked.connect(lambda checked=False, kind=kind: self.run(kind))
        self._finished.connect(self.finished)
        self._progress.connect(self.progress)

    def applyStyle(self, palette):
        """The sparkle icons, in the AI colour of the palette."""
        icon = theme.tool_icon("sparkle", theme.ai_color(palette))
        for button in self.buttons.values():
            button.setIcon(icon)

    def styleSheet(self, palette) -> str:
        return theme.ai_button_style(
            [button.objectName() for button in self.buttons.values()], palette
        )

    def applyTexts(self):
        """The buttons' texts (again after the language changed: retranslateUi
        sets the designed ones)."""
        texts = {
            CROP: QCoreApplication.translate("MainWindow", "Auto crop"),
            LAYOUT: QCoreApplication.translate("MainWindow", "Auto layout"),
        }
        for kind, button in self.buttons.items():
            button.setChecked(self.busy == kind)
            button.setText(
                busy_text(kind, self.downloaded) if self.busy == kind else texts[kind]
            )

    # --- running the model ------------------------------------------------------------

    def pictureState(self):
        """What the pictures are made with: the viewer, the rotation, the
        mirror, the crop and the 4-corner correction."""
        window = self.window
        viewer = window.image_viewer
        thread = viewer.timerThread if viewer is not None else None
        crop = None if thread is None else thread.crop
        return (
            id(viewer),
            None if crop is None else crop.rotation,
            None if crop is None else bool(crop.flipHorizontal),
            None if crop is None else bool(crop.flipVertical),
            window.ui.toolButton_topCrop.isChecked(),
            window.cropValues(),
            None if thread is None else id(thread.homography),
        )

    def run(self, kind: str):
        if self.busy is not None:
            # (the click unchecked or checked a button: it shows the state again)
            self.applyTexts()
            return
        viewer = self.window.image_viewer
        thread = viewer.timerThread if viewer is not None else None
        frame = None
        if thread is not None:
            frame = (
                thread.latestRotatedFrame() if kind == CROP else thread.latestPicture()
            )
        if frame is None:
            self.applyTexts()
            return
        if not auto_layout.model_available() and not self.askToDownload():
            self.applyTexts()
            return
        if kind == CROP:
            # the crop is replaced: stop dragging one
            viewer.cancelCropSelection()
        viewer.cancelFourCornerSelection()
        self.busy = kind
        self.downloaded = None
        self.started_for = self.pictureState()
        self.applyTexts()
        logger.info(
            "Auto %s: looking at a %dx%d picture", kind, frame.shape[1], frame.shape[0]
        )

        def work():
            try:
                if not auto_layout.model_available():
                    auto_layout.download_model(
                        progress=lambda done, total: self._progress.emit(done, total)
                    )
                height, width = frame.shape[:2]
                return {
                    "size": (width, height),
                    "detections": auto_layout.detector().detect(frame),
                }
            except Exception as e:
                logger.exception("Auto %s failed", kind)
                return {"error": str(e)}

        def target():
            result = work()
            try:
                # delivered on the GUI thread (queued)
                self._finished.emit(kind, result)
            except RuntimeError:
                logger.debug("Auto %s finished after its window was closed", kind)

        threading.Thread(target=target, name=f"Auto {kind}", daemon=True).start()

    def askToDownload(self) -> bool:
        box = QMessageBox(
            QMessageBox.Icon.Question,
            QCoreApplication.translate("AutoLayout", "Download the AI model?"),
            QCoreApplication.translate(
                "AutoLayout",
                "Auto crop and Auto layout use ScoreSight's scoreboard model "
                "({size} MB), downloaded once from Hugging Face. It runs on this "
                "computer: the pictures aren't sent anywhere.",
            ).format(size=size_mb(auto_layout.MODEL_SIZE)),
            parent=self.window,
        )
        go = box.addButton(
            QCoreApplication.translate("AutoLayout", "Download"),
            QMessageBox.ButtonRole.AcceptRole,
        )
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(go)
        box.exec()
        accepted = box.clickedButton() is go
        box.deleteLater()
        return accepted

    def progress(self, done: int, total: int):
        if self.busy is None:
            return
        self.downloaded = (done, total)
        if done >= total:
            # the model is loaded and run next
            self.downloaded = None
        self.applyTexts()

    def finished(self, kind: str, result):
        self.busy = None
        self.downloaded = None
        self.applyTexts()
        if not isinstance(result, dict) or "error" in result:
            error = result.get("error") if isinstance(result, dict) else ""
            self.tell(
                QMessageBox.Icon.Warning,
                QCoreApplication.translate("AutoLayout", "The AI model couldn't run"),
                QCoreApplication.translate(
                    "AutoLayout",
                    "Check the internet connection (the model is downloaded the "
                    "first time) and try again.\n\n{error}",
                ).format(error=error),
            )
            return
        if self.pictureState() != self.started_for:
            logger.info("Auto %s: the picture changed meanwhile, result dropped", kind)
            return
        width, height = result["size"]
        detections = result["detections"]
        logger.info(
            "Auto %s found: %s",
            kind,
            ", ".join(f"{d.kind} {d.score:.2f}" for d in detections) or "nothing",
        )
        if kind == CROP:
            rect = auto_layout.board_crop(detections, width, height)
            if rect is None:
                self.tell(
                    QMessageBox.Icon.Information,
                    QCoreApplication.translate("AutoLayout", "No scoreboard found"),
                    QCoreApplication.translate(
                        "AutoLayout",
                        "The AI model didn't find a scoreboard on the picture. "
                        "Drag to crop sets the crop by hand.",
                    ),
                )
                return
            self.window.applyAutoCrop(QRectF(*rect), QSizeF(width, height))
        else:
            fields = auto_layout.field_boxes(detections, width, height)
            if not fields:
                self.tell(
                    QMessageBox.Icon.Information,
                    QCoreApplication.translate("AutoLayout", "No fields found"),
                    QCoreApplication.translate(
                        "AutoLayout",
                        "The AI model didn't find a score, a clock or a period on "
                        "the picture. Auto crop first may help, or draw the boxes "
                        "on the picture.",
                    ),
                )
                return
            self.window.applyAutoLayout(
                {name: QRectF(*rect) for name, rect in fields.items()}
            )

    def tell(self, icon, title: str, text: str):
        box = QMessageBox(icon, title, text, parent=self.window)
        box.exec()
        box.deleteLater()

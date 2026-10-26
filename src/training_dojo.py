import os
import json
from PySide6.QtWidgets import (
    QDialog,
    QListWidgetItem,
    QLabel,
    QSizePolicy,
)
from PySide6.QtGui import QPixmap, QKeyEvent, QKeySequence, QColor, QBrush, QPainter
from PySide6.QtCore import QEvent, Qt
from sc_logging import logger
from storage import write_json_atomic
from ui_training_dojo import Ui_TrainingDojo

APPROVED_ANNOTATIONS_FILE = "approved_annotations.json"


def native_shortcut(sequence: str) -> str:
    """A shortcut as the platform shows it, e.g. "Ctrl+N" or "⌘N" on macOS."""
    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText)


class AspectRatioPixmapLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScaledContents(False)
        self.pix = None

    def setPixmap(self, pixmap):
        self.pix = pixmap
        self.update()

    def clear(self):
        self.pix = None
        super().clear()
        self.update()

    def paintEvent(self, event):
        if self.pix is not None and not self.pix.isNull():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            scaled_pixmap = self.pix.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            x = (self.width() - scaled_pixmap.width()) // 2
            y = (self.height() - scaled_pixmap.height()) // 2
            painter.drawPixmap(x, y, scaled_pixmap)


class TrainingDojo(QDialog):
    """Review and correct OCR training samples (<id>.png + <id>.txt pairs).

    Keys, also while typing in the annotation box: Enter approves and goes to
    the next image, Shift+Enter goes back, Up/Down go to the previous/next
    image, Ctrl+Up/Ctrl+Down jump to the previous/next unapproved image,
    Ctrl+B/Ctrl+N go back/next and Ctrl+U removes the approval (Ctrl is ⌘ on
    macOS). Plain letters are always typed into the annotation.
    """

    def __init__(self, folder_path, parent=None):
        super().__init__(parent)
        self.ui = Ui_TrainingDojo()
        self.ui.setupUi(self)

        self.folder_path = folder_path
        self.image_files = []
        self.current_index = -1
        self.approved_annotations = self.load_approved_annotations()
        self.show_only_undone = False

        # The shortcuts also work while the annotation box has the focus.
        self.ui.lineEdit_text.installEventFilter(self)
        self.ui.lineEdit_text.setToolTip(
            self.tr("Enter: approve and go to the next image")
            + "\n"
            + self.tr("{shortcut}: remove the approval").format(
                shortcut=native_shortcut("Ctrl+U")
            )
        )
        self.ui.pushButton_prev.setToolTip(
            self.tr("Previous image ({shortcuts})").format(
                shortcuts=", ".join(native_shortcut(k) for k in ("Up", "Ctrl+B"))
            )
        )
        self.ui.pushButton_next.setToolTip(
            self.tr("Next image ({shortcuts})").format(
                shortcuts=", ".join(native_shortcut(k) for k in ("Down", "Ctrl+N"))
            )
        )

        # Create an AspectRatioPixmapLabel for displaying the image
        self.image_label = AspectRatioPixmapLabel(self)
        self.image_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.ui.label_imagePlaceholder.setParent(None)
        self.ui.label_imagePlaceholder = self.image_label
        self.ui.widget_image.layout().addWidget(self.image_label)

        self.load_files()
        self.setup_connections()

    def load_approved_annotations(self):
        tracking_file = os.path.join(self.folder_path, APPROVED_ANNOTATIONS_FILE)
        if not os.path.exists(tracking_file):
            return {}
        try:
            with open(tracking_file, "r", encoding="utf-8") as f:
                approved = json.load(f)
        except (OSError, ValueError) as e:
            logger.error(f"Can't read the approved annotations {tracking_file}: {e}")
            return {}
        if not isinstance(approved, dict):
            logger.error(f"Unexpected content in {tracking_file}, ignoring it")
            return {}
        return approved

    def save_approved_annotations(self):
        tracking_file = os.path.join(self.folder_path, APPROVED_ANNOTATIONS_FILE)
        try:
            write_json_atomic(tracking_file, self.approved_annotations)
        except OSError as e:
            logger.error(f"Can't save the approved annotations {tracking_file}: {e}")

    def setup_connections(self):
        self.ui.listWidget_files.itemClicked.connect(self.load_selected_item)
        self.ui.pushButton_next.clicked.connect(self.next_image)
        self.ui.pushButton_prev.clicked.connect(self.prev_image)
        self.ui.lineEdit_text.editingFinished.connect(self.save_text)
        self.ui.pushButton_onlyUndone.clicked.connect(self.toggle_undone_filter)

    def has_current_item(self) -> bool:
        return 0 <= self.current_index < len(self.image_files)

    def read_text(self, base_name) -> str:
        text_path = os.path.join(self.folder_path, f"{base_name}.txt")
        try:
            with open(text_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except OSError as e:
            logger.warning(f"Can't read the annotation {text_path}: {e}")
            return ""

    def load_current_item(self):
        if self.has_current_item():
            base_name = self.image_files[self.current_index]

            # Load image
            image_path = os.path.join(self.folder_path, f"{base_name}.png")
            self.image_label.setPixmap(QPixmap(image_path))

            # Load text
            self.ui.lineEdit_text.setText(self.read_text(base_name))

            # Highlight if approved
            self.highlight_approved()

            # Highlight and scroll to current item in list
            item = self.find_list_item(base_name)
            if item is not None:
                self.ui.listWidget_files.setCurrentItem(item)
                self.ui.listWidget_files.scrollToItem(item)
            else:
                self.ui.listWidget_files.clearSelection()

            # Focus on lineEdit_text
            self.ui.lineEdit_text.setFocus()
        else:
            # Clear the UI if no valid item is selected
            self.image_label.clear()
            self.ui.lineEdit_text.clear()

    def load_selected_item(self, item):
        base_name = item.data(Qt.UserRole)
        if base_name not in self.image_files:
            return
        self.save_text()
        self.current_index = self.image_files.index(base_name)
        self.load_current_item()

    def load_files(self):
        self.ui.listWidget_files.clear()  # Clear existing items
        self.image_files = []

        try:
            file_names = os.listdir(self.folder_path)
        except OSError as e:
            logger.error(f"Can't list the OCR training data in {self.folder_path}: {e}")
            file_names = []
        for file in file_names:
            if file.endswith(".png"):
                base_name = os.path.splitext(file)[0]
                if os.path.exists(os.path.join(self.folder_path, f"{base_name}.txt")):
                    self.image_files.append(base_name)

        # Sort files by filename
        self.image_files.sort()

        self.update_list_widget()

        self.current_index = 0 if self.image_files else -1
        self.load_current_item()

    def style_list_item(self, item: QListWidgetItem):
        base_name = item.data(Qt.UserRole)
        if base_name in self.approved_annotations:
            item.setForeground(QBrush(QColor("green")))
            item.setText(f"✓ {base_name}")
        else:
            # the palette's text color (readable in light and dark mode)
            item.setData(Qt.ForegroundRole, None)
            item.setText(base_name)

    def find_list_item(self, base_name) -> QListWidgetItem | None:
        list_widget = self.ui.listWidget_files
        for row in range(list_widget.count()):
            item = list_widget.item(row)
            if item.data(Qt.UserRole) == base_name:
                return item
        return None

    def update_list_widget(self):
        self.ui.listWidget_files.clear()
        for base_name in self.image_files:
            if not self.show_only_undone or base_name not in self.approved_annotations:
                # the list rows don't match image_files indices when filtered, so
                # each item carries its sample name
                item = QListWidgetItem()
                item.setData(Qt.UserRole, base_name)
                self.style_list_item(item)
                self.ui.listWidget_files.addItem(item)

    def refresh_list_item(self, base_name):
        if self.show_only_undone:
            self.update_list_widget()
            item = self.find_list_item(base_name)
            if item is not None:
                self.ui.listWidget_files.setCurrentItem(item)
        else:
            item = self.find_list_item(base_name)
            if item is not None:
                self.style_list_item(item)

    def toggle_undone_filter(self):
        self.save_text()
        self.show_only_undone = self.ui.pushButton_onlyUndone.isChecked()
        self.update_list_widget()
        if not self.has_current_item() or (
            self.show_only_undone
            and self.image_files[self.current_index] in self.approved_annotations
        ):
            self.update_current_index()
        self.load_current_item()

    def highlight_approved(self):
        if not self.has_current_item():
            return
        base_name = self.image_files[self.current_index]
        if base_name in self.approved_annotations:
            self.ui.lineEdit_text.setStyleSheet("border: 2px solid green;")
            self.image_label.setStyleSheet("border: 3px solid green;")
        else:
            self.ui.lineEdit_text.setStyleSheet("")
            self.image_label.setStyleSheet("")

    def update_current_index(self):
        if self.show_only_undone:
            # Find the first undone item
            for i, base_name in enumerate(self.image_files):
                if base_name not in self.approved_annotations:
                    self.current_index = i
                    return
            # If all items are done, set current_index to -1
            self.current_index = -1
        else:
            # If there are items, set current_index to 0, otherwise -1
            self.current_index = 0 if self.image_files else -1

    def go_to(self, step: int, only_unapproved: bool):
        """Save the current annotation and move step (+1/-1) to the nearest
        image, skipping approved ones if only_unapproved."""
        count = len(self.image_files)
        if count == 0:
            return
        self.save_text()
        if self.has_current_item():
            index = self.current_index
        else:
            index = -1 if step > 0 else 0
        for _ in range(count):
            index = (index + step) % count
            if (
                not only_unapproved
                or self.image_files[index] not in self.approved_annotations
            ):
                self.current_index = index
                self.load_current_item()
                return
        logger.info("All OCR training samples are approved.")

    def next_image(self):
        self.go_to(1, self.show_only_undone)

    def prev_image(self):
        self.go_to(-1, self.show_only_undone)

    def jump_to_next_unapproved(self):
        self.go_to(1, True)

    def jump_to_prev_unapproved(self):
        self.go_to(-1, True)

    def save_text(self):
        if self.has_current_item():
            base_name = self.image_files[self.current_index]
            text_path = os.path.join(self.folder_path, f"{base_name}.txt")
            try:
                with open(text_path, "w", encoding="utf-8") as f:
                    f.write(self.ui.lineEdit_text.text())
            except OSError as e:
                logger.error(f"Can't save the annotation {text_path}: {e}")

    def approve_annotation(self):
        """Approve the current annotation. Doesn't move to another image."""
        if self.has_current_item():
            base_name = self.image_files[self.current_index]
            self.save_text()
            self.approved_annotations[base_name] = self.ui.lineEdit_text.text()
            self.save_approved_annotations()
            self.highlight_approved()
            self.refresh_list_item(base_name)

    def unapprove_annotation(self):
        if self.has_current_item():
            base_name = self.image_files[self.current_index]
            if base_name in self.approved_annotations:
                del self.approved_annotations[base_name]
                self.save_approved_annotations()
                self.highlight_approved()
                self.refresh_list_item(base_name)

    def handle_key_event(self, event: QKeyEvent):
        key = event.key()
        modifiers = event.modifiers()
        # Ctrl (⌘ on macOS) but not Alt: AltGr is reported as Ctrl+Alt on
        # Windows, and AltGr+letter types a character on many layouts.
        command = (
            modifiers & (Qt.ControlModifier | Qt.AltModifier)
        ) == Qt.ControlModifier
        if command and key == Qt.Key_N:
            self.next_image()
        elif command and key == Qt.Key_B:
            self.prev_image()
        elif command and key == Qt.Key_U:
            self.unapprove_annotation()
        elif key == Qt.Key_Return or key == Qt.Key_Enter:
            if modifiers & Qt.ShiftModifier:
                self.prev_image()
            else:
                self.approve_annotation()
                self.next_image()
        elif key == Qt.Key_Up:
            if command:
                self.jump_to_prev_unapproved()
            else:
                self.prev_image()
        elif key == Qt.Key_Down:
            if command:
                self.jump_to_next_unapproved()
            else:
                self.next_image()
        else:
            return False
        return True

    def eventFilter(self, watched, event):
        if (
            watched is self.ui.lineEdit_text
            and event.type() == QEvent.Type.KeyPress
            and self.handle_key_event(event)
        ):
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent):
        if not self.handle_key_event(event):
            super().keyPressEvent(event)

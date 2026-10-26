"""The OBS page's table of text sources: the OBS text source each field
updates (its obs_source_name setting), editable.

A row per field on the picture (template fields included). The name column
isn't editable; the text source column is, and an edit is saved in the box's
settings through the box storage, like any box setting (it is saved with the
boxes and in exported field layouts). An empty name sends nothing to OBS.
"""

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QHeaderView, QTableView

from storage import TextDetectionTargetMemoryStorage
from text_detection_target import TextDetectionTarget

SOURCE_SETTING = "obs_source_name"


def source_rows(boxes: list[TextDetectionTarget]) -> list[tuple[str, str]]:
    """(field name, OBS text source) of each box, in the boxes' order."""
    return [
        (box.name, str((box.settings or {}).get(SOURCE_SETTING) or "")) for box in boxes
    ]


class OBSSourcesTable:
    """Fills tableView_obsSources from the boxes and saves its edits."""

    def __init__(
        self, view: QTableView, storage: TextDetectionTargetMemoryStorage
    ) -> None:
        self.view = view
        self.storage = storage
        self.model = QStandardItemModel(0, 2, view)
        view.setModel(self.model)
        view.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        # the model is filled by refresh(): only the user's edits are saved
        self._filling = False
        self.model.itemChanged.connect(self.sourceEdited)
        self.retranslate()

    def retranslate(self):
        self.model.setHorizontalHeaderLabels(
            [
                QCoreApplication.translate("OBSSources", "Field"),
                QCoreApplication.translate("OBSSources", "OBS text source"),
            ]
        )

    def rows(self) -> list[tuple[str, str]]:
        model = self.model
        return [
            (model.item(row, 0).text(), model.item(row, 1).text())
            for row in range(model.rowCount())
        ]

    def refresh(self, boxes: list[TextDetectionTarget]):
        """Show the boxes (left alone when they are shown already, e.g. after
        the user's own edit, so an edit in progress isn't interrupted)."""
        rows = source_rows(boxes)
        if rows == self.rows():
            return
        self._filling = True
        try:
            self.model.setRowCount(0)
            for name, source in rows:
                name_item = QStandardItem(name)
                name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                source_item = QStandardItem(source)
                source_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsEditable
                )
                source_item.setToolTip(
                    QCoreApplication.translate(
                        "OBSSources",
                        "The OBS text source that “{name}” updates. Empty: “{name}” "
                        "isn't sent to OBS.",
                    ).format(name=name)
                )
                self.model.appendRow([name_item, source_item])
        finally:
            self._filling = False

    def sourceEdited(self, item: QStandardItem):
        if self._filling or item.column() != 1:
            return
        name = self.model.item(item.row(), 0).text()
        source = item.text().strip()
        if source != item.text():
            # shown as saved (this runs again, with the same text)
            item.setText(source)
            return
        box = self.storage.find_item_by_name(name)
        if box is None:
            return
        if (box.settings or {}).get(SOURCE_SETTING, "") == source:
            return
        box.settings[SOURCE_SETTING] = source
        # saved with the boxes, like every box setting
        self.storage.edit_item(name, box)

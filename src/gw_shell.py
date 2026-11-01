"""光为 OCR broadcast shell: left nav, dark tokens, outbound-first right pane.

Layout, copy, and visibility only. OCR, JSON keys, the update channel, and
the existing app icon stay where they are.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from file_output import json_field_key
from product_brand import SIMPLIFY_OUTPUT_UI

# §3 tokens. Applied as a window stylesheet (no extra padding).
BROADCAST_QSS = """
QMainWindow {
    background-color: #0b0d10;
    color: #e8eaed;
}
QWidget#centralwidget, QWidget#widget_leftPane, QFrame#frame_source_view {
    background-color: #0b0d10;
    color: #e8eaed;
}
QWidget#widget_gwNav {
    background-color: #12151a;
}
QStatusBar {
    background-color: #0b0d10;
    color: #8b939e;
}
QPushButton[gwNav="true"] {
    background: transparent;
    color: #8b939e;
    border: none;
    border-radius: 4px;
    padding: 6px 8px;
    font-weight: 600;
}
QPushButton[gwNav="true"]:checked {
    background-color: #3d7ea6;
    color: #e8eaed;
}
QPushButton[gwNav="true"]:hover {
    background-color: #1e2430;
    color: #e8eaed;
}
"""

_NAV = (
    ("source", "源", "Source"),
    ("fields", "字段", "Fields"),
    ("out", "出站", "Out"),
    ("settings", "设置", "Settings"),
)


class FieldNameDelegate(QStyledItemDelegate):
    """Chinese (or given) name, with the English JSON key in small type."""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        name = opt.text or ""
        key = json_field_key(name) if name else ""
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget
        )
        painter.save()
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        role = (
            QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        )
        painter.setPen(opt.palette.color(role))
        rect = opt.rect.adjusted(8, 0, -4, 0)
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            name,
        )
        if key:
            name_w = painter.fontMetrics().horizontalAdvance(name + "  ")
            if rect.width() > name_w + 20:
                key_font = QFont(painter.font())
                if key_font.pointSize() > 0:
                    key_font.setPointSize(max(8, key_font.pointSize() - 2))
                key_font.setFamily("Consolas")
                painter.setFont(key_font)
                if not selected:
                    painter.setPen(QColor("#8b939e"))
                painter.drawText(
                    rect.adjusted(name_w, 0, 0, 0),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    key,
                )
        painter.restore()


class GwShell:
    """Installs the three-column broadcast chrome on an existing main window."""

    def __init__(self, window):
        self.window = window
        self._page = "out"
        self.nav_buttons: dict[str, QPushButton] = {}
        self.readings: QTableWidget | None = None

    def install(self) -> None:
        window = self.window
        self._apply_qss()
        self._install_nav()
        self._pin_flips()
        self._install_readings()
        self._install_reserved()
        self._wire_outbound()
        self._slim_empty_preview()
        self._field_keys()
        self.apply_texts()
        if not getattr(window, "_gw_shell_ready", False):
            self.select("out")
            window._gw_shell_ready = True

    def apply_texts(self) -> None:
        zh = _zh(self.window)
        for key, zh_label, en_label in _NAV:
            button = self.nav_buttons.get(key)
            if button is None:
                continue
            # Short Chinese labels in every locale: they fit the left column,
            # and this build's interface is Chinese.
            button.setText(zh_label)
            button.setToolTip(zh_label if zh else en_label)
        ui = self.window.ui
        if zh:
            for button, text, tip in (
                (
                    ui.toolButton_flipHorizontal,
                    "水平翻转",
                    "左右镜像。预览和识别读翻转后的画面。",
                ),
                (
                    ui.toolButton_flipVertical,
                    "垂直翻转",
                    "上下镜像。预览和识别读翻转后的画面。",
                ),
            ):
                button.setText(text)
                button.setToolTip(tip)
        table = ui.tableWidget_boxes
        header = table.horizontalHeaderItem(0)
        if header is not None and zh:
            header.setText("字段")
        if self.readings is not None and zh:
            self.readings.setHorizontalHeaderLabels(["字段", "值", "键"])
        elif self.readings is not None:
            self.readings.setHorizontalHeaderLabels(["Field", "Value", "Key"])
        self._check(self._page)
        self.refresh_readings()

    def select(self, key: str) -> None:
        self._page = key
        self._check(key)
        ui = self.window.ui
        if key == "source":
            ui.comboBox_camera_source.setFocus(Qt.FocusReason.OtherFocusReason)
        elif key == "fields":
            ui.tableWidget_boxes.setFocus(Qt.FocusReason.OtherFocusReason)
            self.window.setPaneHidden(0, False)
        elif key == "out":
            ui.tabWidget_inspector.setCurrentWidget(ui.widget_outputs)
            self.window.setPaneHidden(2, False)
        elif key == "settings":
            ui.tabWidget_inspector.setCurrentWidget(ui.tab_box)
            self.window.setPaneHidden(2, False)
            combo = getattr(ui, "comboBox_ocrModel", None)
            if combo is not None:
                combo.setFocus(Qt.FocusReason.OtherFocusReason)

    def refresh_readings(self) -> None:
        table = self.readings
        if table is None:
            return
        boxes = self.window.ui.tableWidget_boxes
        table.setRowCount(boxes.rowCount())
        for row in range(boxes.rowCount()):
            name_item = boxes.item(row, 0)
            value_item = boxes.item(row, 1)
            name = name_item.text() if name_item is not None else ""
            value = value_item.text() if value_item is not None else ""
            key = json_field_key(name) if name else ""
            for col, text in enumerate((name, value, key)):
                item = table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    table.setItem(row, col, item)
                if item.text() != text:
                    item.setText(text)
                if col == 2:
                    font = item.font()
                    font.setFamily("Consolas")
                    if font.pointSize() > 0:
                        font.setPointSize(max(8, font.pointSize() - 1))
                    item.setFont(font)

    def _check(self, key: str) -> None:
        for name, button in self.nav_buttons.items():
            button.setChecked(name == key)

    def _apply_qss(self) -> None:
        # Color the nav only. A stylesheet on the main window switches the
        # whole UI to the stylesheet style and widens every button enough to
        # miss a small screen.
        nav = getattr(self.window, "gwNav", None)
        if nav is not None and "gwNav" not in (nav.styleSheet() or ""):
            nav.setStyleSheet(BROADCAST_QSS)

    def _install_nav(self) -> None:
        if self.nav_buttons:
            return
        window = self.window
        nav = QWidget(window.ui.widget_leftPane)
        nav.setObjectName("widget_gwNav")
        layout = QHBoxLayout(nav)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(4)
        for key, _zh_label, _en_label in _NAV:
            button = QPushButton(nav)
            button.setObjectName(f"btn_nav_{key}")
            button.setProperty("gwNav", True)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(32)
            button.setMinimumWidth(0)
            # The labels are two characters. Don't let their size hints widen
            # the fields pane; the row shares whatever width the pane has.
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, key=key: self.select(key))
            layout.addWidget(button, 1)
            self.nav_buttons[key] = button
        window.ui.verticalLayout_leftPane.insertWidget(0, nav)
        window.gwNav = nav
        self._apply_qss()

    def _pin_flips(self) -> None:
        window = self.window
        ui = window.ui
        if getattr(window, "widget_flipBar", None) is not None:
            return
        row = QWidget(ui.frame_source_view)
        row.setObjectName("widget_flipBar")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 4)
        layout.setSpacing(6)
        tools = ui.horizontalLayout_10
        for button in (ui.toolButton_flipHorizontal, ui.toolButton_flipVertical):
            tools.removeWidget(button)
            button.setParent(row)
            layout.addWidget(button)
        layout.addStretch(1)
        ui._3.insertWidget(1, row)
        window.widget_flipBar = row
        for compact in getattr(window, "compactRows", []):
            compact.refresh()

    def _install_readings(self) -> None:
        if self.readings is not None:
            return
        window = self.window
        table = QTableWidget(0, 3, window.ui.widget_outputDetails)
        table.setObjectName("table_outboundReadings")
        table.setHorizontalHeaderLabels(["字段", "值", "键"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setShowGrid(False)
        table.setMinimumHeight(72)
        table.setMaximumHeight(160)
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        window.ui.verticalLayout_outputDetails.insertWidget(1, table)
        self.readings = table

    def _install_reserved(self) -> None:
        ui = self.window.ui
        if getattr(ui, "btn_reservedFeatures", None) is not None:
            return
        button = QPushButton(ui.tab_api)
        button.setObjectName("btn_reservedFeatures")
        button.setFlat(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet("text-align: left; color: #8b939e;")
        note = QLabel(ui.tab_api)
        note.setObjectName("label_reservedWebsite")
        note.setWordWrap(True)
        note.setText(
            "光为网站（预留）默认关闭，不在出站主路径。需要时再到 HTTP API 预设里选它。"
        )
        note.setStyleSheet("color: #8b939e;")
        note.hide()

        def toggle() -> None:
            note.setVisible(note.isHidden())
            button.setText("▾ 预留功能" if note.isVisible() else "▸ 预留功能")

        button.setText("▸ 预留功能")
        button.clicked.connect(toggle)
        ui.verticalLayout_api.addWidget(button)
        ui.verticalLayout_api.addWidget(note)
        ui.btn_reservedFeatures = button
        ui.label_reservedWebsite = note

    def _wire_outbound(self) -> None:
        window = self.window
        chooser = getattr(window, "outboundChooser", None)
        if chooser is None:
            return
        if not getattr(chooser, "_gw_more_wired", False):
            chooser.more_toggled.connect(self._more_outputs)
            chooser._gw_more_wired = True
        guide = getattr(window, "vmixOutboundGuide", None)
        if guide is not None and guide.frame is not None:
            # The chooser is the hero. The old card would be a second solid button.
            guide.frame.hide()
            parent = guide.frame.parentWidget()
            while parent is not None:
                lay = parent.layout()
                if lay is not None:
                    lay.invalidate()
                parent.updateGeometry()
                parent = parent.parentWidget()
        # The output pages keep their own height. The side pane must still fit
        # a small screen, so the stack shrinks instead of raising the window's
        # minimum height to the tallest page.
        stack = window.ui.stackedWidget_outputs
        stack.setMinimumHeight(0)
        stack.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        self._more_outputs(chooser.btn_more.isChecked())

    def _more_outputs(self, open_: bool) -> None:
        window = self.window
        view = window.ui.listView_destinations
        view.setVisible(bool(open_))
        splitter = window.ui.splitter_outputs
        if open_:
            height = window.destinationsListHeight()
            splitter.setSizes([max(120, height), 10000])
        else:
            splitter.setSizes([0, max(400, splitter.height() or 400)])

    def _slim_empty_preview(self) -> None:
        if not _zh(self.window):
            return
        ui = self.window.ui
        ui.label_12.setText("选择视频源")
        ui.label_firstRunSource.setText("选择视频源")
        for widget in (
            ui.label_firstRunStep2,
            ui.widget_firstRunBoxes,
            ui.label_firstRunStep3,
            ui.widget_firstRunOutputs,
            ui.label_firstRunQuickstart,
        ):
            widget.hide()

    def _field_keys(self) -> None:
        table = self.window.ui.tableWidget_boxes
        current = table.itemDelegateForColumn(0)
        if not isinstance(current, FieldNameDelegate):
            table.setItemDelegateForColumn(0, FieldNameDelegate(table))
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is None:
                continue
            key = json_field_key(item.text())
            tip = f"{item.text()}\n{key}"
            if item.toolTip() != tip:
                item.setToolTip(tip)


def _zh(window) -> bool:
    try:
        return bool(window._chinese_ui())
    except Exception:
        return False


def install_gw_shell(window) -> GwShell | None:
    """Idempotent. No-op when the GuangWei output UI is off."""
    if not SIMPLIFY_OUTPUT_UI:
        return None
    shell = getattr(window, "gwShell", None)
    if shell is None:
        shell = GwShell(window)
        window.gwShell = shell
    shell.install()
    return shell


def default_color_scheme_name() -> str:
    """GuangWei starts dark. An explicit System / Light / Dark choice is kept."""
    if SIMPLIFY_OUTPUT_UI:
        return "Dark"
    return "System"

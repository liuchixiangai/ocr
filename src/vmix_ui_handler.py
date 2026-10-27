import threading

from PySide6.QtGui import QGuiApplication, QStandardItem, QStandardItemModel
from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
)

from theme import (
    button_text_color,
    color as theme_color,
    is_dark,
    media_icon,
    muted_color,
)

from text_detection_target import TextDetectionTarget
from ui_mainwindow import Ui_MainWindow
from vmix_output import DEFAULT_VMIX_PORT, VMIX_TCP_API_PORT, VMixAPI
from vmix_title_scan import (
    TitleChoice,
    VmixScanError,
    VmixTitleInput,
    auto_match_fields,
    cell_is_placeholder,
    choose_title_input,
    fetch_vmix_api_xml,
    format_pick_prompt,
    format_scan_error,
    format_scan_success,
    format_title_problem,
    input_choice_label,
    parse_vmix_api_xml,
)
from sc_logging import logger
from storage import fetch_data, store_data

SETTINGS_FILE = "scoresight.json"


def migrate_vmix_port():
    """One-time migration: older versions defaulted the vMix port to 8099, vMix's
    TCP API, which ScoreSight can't talk to (#55). Change a stored 8099 to the
    Web API port 8088 once; a port the user sets later is left alone."""
    if fetch_data(SETTINGS_FILE, "vmix_port_migrated", False):
        return
    if str(fetch_data(SETTINGS_FILE, "vmix_port", "")).strip() == VMIX_TCP_API_PORT:
        store_data(SETTINGS_FILE, "vmix_port", DEFAULT_VMIX_PORT)
        logger.info(
            "vMix port changed from %s (vMix TCP API) to %s (vMix Web API, used "
            "by ScoreSight)",
            VMIX_TCP_API_PORT,
            DEFAULT_VMIX_PORT,
        )
    store_data(SETTINGS_FILE, "vmix_port_migrated", True)


def mapping_from_model(model: QStandardItemModel) -> dict[str, str]:
    """{box name: vMix field name} from the mapping table's model."""
    mapping = {}
    for i in range(model.rowCount()):
        item = model.item(i, 0)
        value = model.item(i, 1)
        if item and value:
            mapping[item.text()] = value.text()
    return mapping


class _ScanBridge(QObject):
    """Delivers a background scan result onto the GUI thread."""

    finished = Signal(object)


class VmixFieldCombo(QComboBox):
    """One mapping row's vMix field picker. It stays in the cell.

    Opening a combo only while the cell is editing does not work here: the
    view waits out the double-click interval before it creates the editor,
    and showing the list from that editor makes the list lose focus and
    close before the click can land. A combo that is already in the cell
    opens its list on a normal click of the arrow.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("combo_vmixField")
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # The built-in completer is a second popup and steals the click.
        self.setCompleter(None)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMaxVisibleItems(18)
        self._mapping_ready = False
        self._box_name = ""
        self._popup_open = False

    def wheelEvent(self, event):
        # Scrolling the page must not change the mapped field.
        popup = self.view()
        if popup is not None and popup.isVisible():
            super().wheelEvent(event)
            return
        event.ignore()

    def showPopup(self):
        popup = self.view()
        if popup is not None and self.count() > 0:
            width = self.width()
            metrics = popup.fontMetrics()
            for i in range(self.count()):
                width = max(width, metrics.horizontalAdvance(self.itemText(i)) + 36)
            popup.setMinimumWidth(max(width, 180))
            popup.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._popup_open = True
        super().showPopup()

    def hidePopup(self):
        super().hidePopup()
        # editingFinished can fire while the list is closing, still carrying
        # the previous text. Keep the "list is closing" flag until that
        # signal has run; the choice itself is committed from textActivated.
        self._popup_open = True
        # Tied to this combo: dropped if the row is rebuilt before it runs.
        QTimer.singleShot(0, self, self._popup_settled)

    def _popup_settled(self):
        self._popup_open = False


class VmixFieldDelegate(QStyledItemDelegate):
    """Line edit for the vMix column before a scan has produced names.

    After a scan, each row has a VmixFieldCombo instead. This delegate still
    builds a combo if something asks it to edit a cell that has names, but
    it must not open the list itself: that list is closed by the click that
    created the editor.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.field_names: list[str] = []
        self.placeholder = ""

    def createEditor(self, parent, option, index):
        view = self.parent()
        if view is not None and view.indexWidget(index) is not None:
            return None
        if not self.field_names:
            return super().createEditor(parent, option, index)
        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.setCompleter(None)
        combo.addItems(self.field_names)
        if self.placeholder and combo.lineEdit() is not None:
            combo.lineEdit().setPlaceholderText(self.placeholder)
        combo.textActivated.connect(lambda _text, box=combo: self._commit_editor(box))
        return combo

    def _commit_editor(self, editor):
        self.commitData.emit(editor)
        self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.NoHint)

    def setEditorData(self, editor, index):
        if isinstance(editor, QComboBox):
            editor.setCurrentText(index.data(Qt.ItemDataRole.EditRole) or "")
            return
        super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)
            return
        super().setModelData(editor, model, index)


class VMixUIHanlder:
    def __init__(self, ui: Ui_MainWindow):
        self.ui = ui
        self.vmixUpdater = None
        self._zh = True
        self._scan_busy = False
        self._scan_query = ""
        self._mapping_combos_live = False
        self.vmixUiSetup()

    def globalSettingsChanged(self, settingName, value):
        store_data(SETTINGS_FILE, settingName, value)

    def vmixConnectionChanged(self):
        # Update the existing updater: recreating it lost the field mapping and
        # stopped the output while the button still said "Stop".
        host = self.ui.lineEdit_vmixHost.text().strip()
        port = self.ui.lineEdit_vmixPort.text().strip()
        input_number = self.ui.inputLineEdit_vmix.text().strip()
        self.vmixUpdater.set_connection(host, port, input_number)
        for name, value in (
            ("vmix_host", host),
            ("vmix_port", port),
            ("vmix_input", input_number),
        ):
            if fetch_data(SETTINGS_FILE, name) != value:
                self.globalSettingsChanged(name, value)

    def vmixMappingChanged(self, *_):
        # store entire mapping data in scoresight.json
        model = self.ui.tableView_vmixMapping.model()
        if isinstance(model, QStandardItemModel):
            mapping = mapping_from_model(model)
            self.globalSettingsChanged("vmix_mapping", mapping)
            self.vmixUpdater.set_field_mapping(mapping)
        else:
            logger.error("vmixMappingChanged: model is not a QStandardItemModel")

    def vmixUiSetup(self):
        migrate_vmix_port()
        # populate the vmix connection from storage
        self.ui.lineEdit_vmixHost.setText(
            str(fetch_data(SETTINGS_FILE, "vmix_host", "localhost"))
        )
        self.ui.lineEdit_vmixPort.setText(
            str(fetch_data(SETTINGS_FILE, "vmix_port", DEFAULT_VMIX_PORT))
        )
        self.ui.inputLineEdit_vmix.setText(
            str(fetch_data(SETTINGS_FILE, "vmix_input", "1"))
        )
        # apply a connection change once it's typed (Enter or leaving the
        # field), not on every keystroke
        self.ui.lineEdit_vmixHost.editingFinished.connect(self.vmixConnectionChanged)
        self.ui.lineEdit_vmixPort.editingFinished.connect(self.vmixConnectionChanged)
        self.ui.inputLineEdit_vmix.editingFinished.connect(self.vmixConnectionChanged)

        # create the vmixUpdater
        self.vmixUpdater = VMixAPI(
            self.ui.lineEdit_vmixHost.text().strip(),
            self.ui.lineEdit_vmixPort.text().strip(),
            self.ui.inputLineEdit_vmix.text().strip(),
            {},
        )
        # add standard item model to the tableView_vmixMapping
        view = self.ui.tableView_vmixMapping
        view.setModel(QStandardItemModel())
        self._field_delegate = VmixFieldDelegate(view)
        view.setItemDelegateForColumn(1, self._field_delegate)
        self._set_inline_editing(True)
        mapping = fetch_data(SETTINGS_FILE, "vmix_mapping", {})
        if mapping:
            self.vmixUpdater.set_field_mapping(mapping)

        view.model().dataChanged.connect(self.vmixMappingChanged)
        self._set_mapping_headers()

        self.ui.pushButton_startvmix.toggled.connect(self.togglevMix)
        self._scan_bridge = _ScanBridge(view)
        self._scan_bridge.finished.connect(self._on_scan_finished)
        self._install_vmix_guide()
        # The mapping table is the part you edit after a scan. Let it take
        # the spare height instead of the instructions eating the page.
        page = getattr(self.ui, "tab_vmix", None)
        grid = page.layout() if page is not None else None
        if isinstance(grid, QGridLayout):
            grid.setRowStretch(1, 1)

    def restoreUiState(self):
        """Show the current connection and running state again, e.g. after
        retranslateUi() reset the fields and the button to the .ui defaults."""
        self.ui.lineEdit_vmixHost.setText(self.vmixUpdater.host)
        self.ui.lineEdit_vmixPort.setText(self.vmixUpdater.port)
        self.ui.inputLineEdit_vmix.setText(self.vmixUpdater.input_number)
        self.ui.pushButton_startvmix.setChecked(self.vmixUpdater.running)
        self._updateToggleText(self.vmixUpdater.running)
        self.apply_vmix_guide_texts(True)

    def _updateToggleText(self, running):
        button = self.ui.pushButton_startvmix
        button.setText(
            QCoreApplication.translate("MainWindow", "Stop vMix")
            if running
            else QCoreApplication.translate("MainWindow", "Start vMix")
        )
        button.setIcon(
            media_icon(
                "stop" if running else "play", button_text_color(button.palette())
            )
        )

    def refreshToggleButton(self):
        """Redraw the start/stop button, e.g. after a light/dark switch."""
        self._updateToggleText(self.ui.pushButton_startvmix.isChecked())
        self._style_scan_button()

    def togglevMix(self, value):
        if not self.vmixUpdater:
            return
        self._updateToggleText(value)
        if value:
            self.vmixUpdater.start()
        else:
            self.vmixUpdater.stop()

    def _connect_guide_buttons(self, replace=False):
        for button, slot in (
            (self.btn_scan_vmix_fields, self.scan_title_fields),
            (self.btn_copy_vmix_api, self.copy_vmix_api_summary),
        ):
            # disconnect() on a fresh button warns in PySide even when caught.
            # Only a second handler, adopting buttons that already have slots,
            # needs to drop the previous handler's connections.
            if replace:
                button.clicked.disconnect()
            button.clicked.connect(slot)

    def _install_vmix_guide(self):
        """Chinese how-to. JSON stays the recommended path; the API scan is optional."""
        if getattr(self, "_vmix_guide_installed", False):
            return
        existing = getattr(self.ui, "btn_scan_vmix_fields", None)
        if existing is not None:
            self.label_vmix_guide = self.ui.label_vmix_guide
            self.btn_scan_vmix_fields = existing
            self.label_vmix_scan_status = self.ui.label_vmix_scan_status
            self.btn_copy_vmix_api = self.ui.btn_copy_vmix_api
            self._ensure_scan_lead()
            self._connect_guide_buttons(replace=True)
            self._ensure_api_steps_toggle()
            self._place_vmix_guide_widgets()
            self._vmix_guide_installed = True
            self.apply_vmix_guide_texts(self._zh)
            return
        parent = getattr(self.ui, "tab_vmix", None)
        layout = getattr(self.ui, "verticalLayout_6", None)
        if parent is None or layout is None:
            return
        self.label_vmix_scan_lead = QLabel(parent)
        self.label_vmix_scan_lead.setObjectName("label_vmix_scan_lead")
        self.label_vmix_scan_lead.setWordWrap(True)
        self.label_vmix_guide = QLabel(parent)
        self.label_vmix_guide.setWordWrap(True)
        self.label_vmix_guide.setTextFormat(Qt.TextFormat.RichText)
        self.btn_scan_vmix_fields = QPushButton(parent)
        self.btn_scan_vmix_fields.setObjectName("pushButton_scanVmixFields")
        self.btn_scan_vmix_fields.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label_vmix_scan_status = QLabel(parent)
        self.label_vmix_scan_status.setWordWrap(True)
        self.label_vmix_scan_status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.btn_copy_vmix_api = QPushButton(parent)
        self.btn_copy_vmix_api.setCursor(Qt.CursorShape.PointingHandCursor)
        self._connect_guide_buttons()
        self.ui.label_vmix_scan_lead = self.label_vmix_scan_lead
        self.ui.label_vmix_guide = self.label_vmix_guide
        self.ui.btn_scan_vmix_fields = self.btn_scan_vmix_fields
        self.ui.label_vmix_scan_status = self.label_vmix_scan_status
        self.ui.btn_copy_vmix_api = self.btn_copy_vmix_api
        self._ensure_api_steps_toggle()
        self._place_vmix_guide_widgets()
        self._vmix_guide_installed = True
        self.apply_vmix_guide_texts(True)

    def _ensure_scan_lead(self):
        lead = getattr(self.ui, "label_vmix_scan_lead", None)
        if lead is None:
            parent = (
                getattr(self.ui, "tab_vmix", None) or self.btn_scan_vmix_fields.parent()
            )
            lead = QLabel(parent)
            lead.setObjectName("label_vmix_scan_lead")
            lead.setWordWrap(True)
            self.ui.label_vmix_scan_lead = lead
        self.label_vmix_scan_lead = lead

    def _ensure_api_steps_toggle(self):
        """The long API instructions start collapsed so they don't fill the page."""
        button = getattr(self, "btn_vmix_api_steps", None)
        if button is None:
            button = getattr(self.ui, "btn_vmix_api_steps", None)
        if button is None:
            parent = (
                getattr(self.ui, "tab_vmix", None) or self.label_vmix_guide.parent()
            )
            button = QPushButton(parent)
            button.setObjectName("pushButton_vmixApiSteps")
            button.setFlat(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet("text-align: left; color: #94a3b8; font-weight: 600;")
            button.clicked.connect(self._toggle_api_steps)
            self.ui.btn_vmix_api_steps = button
        self.btn_vmix_api_steps = button
        if not getattr(self, "_api_steps_ready", False):
            self._api_steps_open = False
            self.label_vmix_guide.hide()
            self._api_steps_ready = True

    def _toggle_api_steps(self) -> None:
        self._api_steps_open = not getattr(self, "_api_steps_open", False)
        self.label_vmix_guide.setVisible(self._api_steps_open)
        self._update_api_steps_button()

    def _update_api_steps_button(self) -> None:
        button = getattr(self, "btn_vmix_api_steps", None)
        if button is None:
            return
        open_ = bool(getattr(self, "_api_steps_open", False))
        if self._zh:
            button.setText(
                "▾ 收起 API 步骤" if open_ else "▸ API 步骤（可选，一般不用看）"
            )
        else:
            button.setText("▾ Hide API steps" if open_ else "▸ API steps (optional)")

    def _place_vmix_guide_widgets(self):
        """Short lead and the scan button, then optional steps.

        The long how-to stays collapsed. The Output tab's two path buttons
        are how you get here and how you get back to JSON.
        """
        layout = getattr(self.ui, "verticalLayout_6", None)
        if layout is None:
            return
        widgets = [
            self.label_vmix_scan_lead,
            self.btn_scan_vmix_fields,
            self.label_vmix_scan_status,
            self.btn_vmix_api_steps,
            self.label_vmix_guide,
            self.btn_copy_vmix_api,
        ]
        for widget in widgets:
            if widget is None:
                continue
            layout.removeWidget(widget)
        for widget in widgets:
            if widget is None:
                continue
            layout.addWidget(widget)

    def _style_scan_button(self):
        button = getattr(self, "btn_scan_vmix_fields", None)
        if button is None:
            return
        button.setObjectName("pushButton_scanVmixFields")
        # Slate, not the JSON blue: the blue button on the Output tab is
        # 「JSON → vMix 数据源」. This scan action stays inside the API page.
        dark = is_dark(button.palette())
        bg, hover = ("#475569", "#334155") if dark else ("#334155", "#1e293b")
        button.setStyleSheet(
            "QPushButton#pushButton_scanVmixFields {"
            f" background-color: {bg}; color: #ffffff;"
            " border: none; border-radius: 6px;"
            " padding: 10px 14px; font-weight: 700; }"
            "QPushButton#pushButton_scanVmixFields:hover {"
            f" background-color: {hover}; }}"
            "QPushButton#pushButton_scanVmixFields:pressed {"
            f" background-color: {hover}; }}"
            "QPushButton#pushButton_scanVmixFields:disabled {"
            " background-color: #64748b; color: #e2e8f0; }"
        )
        button.setMinimumHeight(44)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if not button.property("scan_font_set"):
            font = button.font()
            font.setBold(True)
            if font.pointSize() > 0:
                font.setPointSize(min(font.pointSize() + 2, 16))
            button.setFont(font)
            button.setProperty("scan_font_set", True)

    def _set_mapping_headers(self):
        view = self.ui.tableView_vmixMapping
        model = view.model()
        if not isinstance(model, QStandardItemModel):
            return
        if self._zh:
            labels = ["光为字段", "vMix 字段"]
        else:
            labels = ["Field", "vMix field"]
        model.setHorizontalHeaderLabels(labels)
        header = view.horizontalHeader()
        header.setVisible(True)
        header.setStretchLastSection(True)
        if model.columnCount() >= 2:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    _INLINE_EDIT = (
        QAbstractItemView.EditTrigger.SelectedClicked
        | QAbstractItemView.EditTrigger.DoubleClicked
        | QAbstractItemView.EditTrigger.EditKeyPressed
        | QAbstractItemView.EditTrigger.AnyKeyPressed
    )

    def _set_inline_editing(self, enabled: bool) -> None:
        """Line-edit cells before a scan. Afterwards the combos own the clicks."""
        triggers = (
            self._INLINE_EDIT
            if enabled
            else QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.ui.tableView_vmixMapping.setEditTriggers(triggers)

    def _suspend_mapping_combos(self) -> None:
        """Stop combos writing back while the model or the widgets are rebuilt."""
        self._mapping_combos_live = False
        view = self.ui.tableView_vmixMapping
        model = view.model()
        if not isinstance(model, QStandardItemModel):
            return
        for row in range(model.rowCount()):
            widget = view.indexWidget(model.index(row, 1))
            if isinstance(widget, VmixFieldCombo):
                widget._mapping_ready = False

    def _refresh_mapping_combo_placeholders(self) -> None:
        view = self.ui.tableView_vmixMapping
        model = view.model()
        if not isinstance(model, QStandardItemModel):
            return
        placeholder = self._field_delegate.placeholder
        for row in range(model.rowCount()):
            widget = view.indexWidget(model.index(row, 1))
            if not isinstance(widget, VmixFieldCombo):
                continue
            line = widget.lineEdit()
            if line is not None:
                line.setPlaceholderText(placeholder)

    def _commit_combo(self, combo: VmixFieldCombo, text: str) -> None:
        if not self._mapping_combos_live or not combo._mapping_ready:
            return
        box = combo._box_name
        text = (text or "").strip()
        model = self.ui.tableView_vmixMapping.model()
        if not isinstance(model, QStandardItemModel):
            return
        for row in range(model.rowCount()):
            name_item = model.item(row, 0)
            value_item = model.item(row, 1)
            if name_item is None or value_item is None or name_item.text() != box:
                continue
            if value_item.text() == text:
                return
            value_item.setText(text)
            return

    def _finish_combo_typing(self, combo: VmixFieldCombo) -> None:
        # The list closing also drops focus from the line edit. That signal
        # still has the previous text; the click is saved from textActivated.
        if combo._popup_open:
            return
        self._commit_combo(combo, combo.currentText())

    def _make_mapping_combo(
        self, box_name: str, current: str, fields: list[str]
    ) -> VmixFieldCombo:
        combo = VmixFieldCombo()
        combo._box_name = box_name
        combo.blockSignals(True)
        combo.addItems(fields)
        combo.setCurrentText(current)
        line = combo.lineEdit()
        if line is not None:
            line.blockSignals(True)
            line.setPlaceholderText(self._field_delegate.placeholder)
            line.blockSignals(False)
        combo.blockSignals(False)
        tip = (
            "点右边箭头，从扫到的文字名里选。也可以手打。"
            "改过之后，再扫描不会盖掉这一格。"
            if self._zh
            else "Use the arrow to pick a scanned text field, or type a name. "
            "A later scan will not replace a cell you changed."
        )
        combo.setToolTip(tip)
        combo.textActivated.connect(
            lambda text, box=combo: self._commit_combo(box, text)
        )
        if line is not None:
            line.editingFinished.connect(
                lambda box=combo: self._finish_combo_typing(box)
            )
        return combo

    def _size_mapping_rows(self) -> None:
        view = self.ui.tableView_vmixMapping
        model = view.model()
        if not isinstance(model, QStandardItemModel):
            return
        height = 34
        if model.rowCount():
            sample = view.indexWidget(model.index(0, 1))
            if isinstance(sample, QComboBox):
                height = max(height, sample.sizeHint().height() + 6)
        header = view.verticalHeader()
        header.setMinimumSectionSize(height)
        header.setDefaultSectionSize(height)
        for row in range(model.rowCount()):
            view.setRowHeight(row, height)

    def _clear_mapping_combos(self) -> None:
        view = self.ui.tableView_vmixMapping
        model = view.model()
        if not isinstance(model, QStandardItemModel):
            return
        for row in range(model.rowCount()):
            index = model.index(row, 1)
            if view.indexWidget(index) is not None:
                view.setIndexWidget(index, None)

    def _install_mapping_combos(self) -> None:
        """Put a dropdown on every row once a scan has names to offer.

        Rows that were auto-filled and rows that are still empty both get
        the same list, so a guess can be changed by hand.
        """
        view = self.ui.tableView_vmixMapping
        model = view.model()
        self._suspend_mapping_combos()
        self._clear_mapping_combos()
        fields = list(self._field_delegate.field_names)
        if (
            not fields
            or not isinstance(model, QStandardItemModel)
            or model.rowCount() == 0
        ):
            self._set_inline_editing(True)
            return
        made: list[VmixFieldCombo] = []
        for row in range(model.rowCount()):
            box_item = model.item(row, 0)
            value_item = model.item(row, 1)
            if box_item is None or value_item is None:
                continue
            combo = self._make_mapping_combo(box_item.text(), value_item.text(), fields)
            view.setIndexWidget(model.index(row, 1), combo)
            made.append(combo)
        self._set_inline_editing(False)
        self._mapping_combos_live = True
        for combo in made:
            combo._mapping_ready = True
        self._size_mapping_rows()

    def apply_vmix_guide_texts(self, zh: bool = True):
        self._zh = zh
        self._field_delegate.placeholder = "下拉选择，或手打" if zh else "Pick or type"
        self._set_mapping_headers()
        self._refresh_mapping_combo_placeholders()
        if not getattr(self, "_vmix_guide_installed", False):
            return
        if zh:
            self.label_vmix_scan_lead.setText(
                "大多数人点上面的「JSON → vMix 数据源」，勾选「给 vMix 的 JSON」。"
                "这一页是进阶：点下面扫描标题文字，再在表里逐行改。"
            )
            self.ui.pushButton_startvmix.setToolTip(
                "进阶。按映射表用 Web API 的 SetText 更新标题文字。"
                "日常请用「给 vMix 的 JSON」。"
            )
            self.ui.tableView_vmixMapping.setToolTip(
                "左列是光为的字段，右列是 vMix 标题里的文字名。"
                "扫描之后每一行都有下拉，自动填上的也能改，没填上的可以自己选。"
                "扫描只填空着的和仍显示字段原名的格子，你改过的不会被盖掉。"
            )
            self.btn_scan_vmix_fields.setText("进阶：扫描标题字段（API）")
            self.btn_scan_vmix_fields.setToolTip(
                "可选。向 vMix 读取这个标题里的文字字段。"
                "读完后下面每一行都能下拉改，包括已经自动填上的。"
                "不改你已经手改过的映射。日常出站请用「给 vMix 的 JSON」。"
            )
            self.btn_copy_vmix_api.setText("一键复制 API 示例 / 当前映射")
            self.label_vmix_guide.setText(
                "<b>进阶：vMix API（一般不用）</b><br>"
                "大多数人点上面蓝色的「JSON → vMix 数据源」，勾选"
                "<b>「给 vMix 的 JSON（推荐）」</b>就够："
                "vMix 用 Data Sources 读那一个文件。这一页是可选的，"
                "给已经做好标题、想让软件直接改标题文字的人。<br>"
                "1）vMix → Settings → Web Controller → 勾选 Enable（端口一般 8088）<br>"
                "2）填主机和端口。Input 可填编号、标题名或 Key；"
                "空着或对不上时，扫描会让你选一个标题<br>"
                "3）点上面的「进阶：扫描标题字段（API）」。"
                "下面每一行右边都变成下拉，列出扫到的文字名，也可以手打。"
                "空着的、以及还显示成光为字段原名的格子，会按名字猜一次；"
                "<b>你已经改过的格子不会被盖掉</b>。"
                "猜错了或没猜到的，直接在那一行的下拉里改<br>"
                "4）核对映射后，再点「启动 vMix」"
            )
        else:
            self.label_vmix_scan_lead.setText(
                "Most people use JSON for vMix (the button above) and tick "
                "JSON for vMix. This page is advanced: scan the title, then "
                "pick each field in the table."
            )
            self.ui.pushButton_startvmix.setToolTip(
                "Advanced. Push mapped fields with the Web API SetText. "
                "Most people should use JSON for vMix instead."
            )
            self.ui.tableView_vmixMapping.setToolTip(
                "Left: ScoreSight field. Right: vMix text field. "
                "After a scan, every row has a dropdown, including rows that "
                "were filled automatically. "
                "A scan fills empty cells and cells still showing the box name; "
                "cells you already edited are kept."
            )
            self.btn_scan_vmix_fields.setText("Advanced: scan title fields (API)")
            self.btn_scan_vmix_fields.setToolTip(
                "Optional. Read text field names from the vMix Web Controller. "
                "Afterwards every row can be changed from the dropdown. "
                "Does not replace mappings you already edited. "
                "JSON for vMix is the usual path."
            )
            self.btn_copy_vmix_api.setText("Copy API example / mapping")
            self.label_vmix_guide.setText(
                "<b>Advanced: vMix Web API (optional)</b><br>"
                "Most setups should use <b>JSON for vMix</b> on the Text files page "
                "(vMix Data Sources). This page only pushes SetText into a title "
                "you already built.<br>"
                "Enable Web Controller (port 8088), then "
                "“Advanced: scan title fields (API)”. "
                "Every row on the right becomes a dropdown you can also type in. "
                "Empty cells and cells still showing the box name are guessed once; "
                "<b>cells you already edited are kept</b>. "
                "Change any guess from that row's dropdown."
            )
        self._style_scan_button()
        self._update_api_steps_button()

    def _set_scan_status(self, text: str, kind: str) -> None:
        label = getattr(self, "label_vmix_scan_status", None)
        if label is None:
            return
        label.setText(text)
        if kind == "error":
            qcolor = theme_color("empty")
        elif kind == "ok":
            qcolor = theme_color("new")
        else:
            qcolor = muted_color()
        label.setStyleSheet(f"color: {qcolor.name()};")

    def _set_input_number(self, number: str) -> None:
        if not number:
            return
        edit = self.ui.inputLineEdit_vmix
        if edit.text().strip() == number:
            return
        edit.setText(number)
        edit.editingFinished.emit()

    def scan_title_fields(self):
        """GET /api/ on a background thread and fill empty mapping cells."""
        if self._scan_busy:
            return
        host = self.ui.lineEdit_vmixHost.text().strip()
        port = self.ui.lineEdit_vmixPort.text().strip()
        query = self.ui.inputLineEdit_vmix.text().strip()
        if not host:
            self._set_scan_status(
                format_scan_error(VmixScanError("no_host"), self._zh), "error"
            )
            return
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            self._set_scan_status(
                format_scan_error(VmixScanError("bad_port"), self._zh), "error"
            )
            return
        self._scan_busy = True
        self._scan_query = query
        button = getattr(self, "btn_scan_vmix_fields", None)
        if button is not None:
            button.setEnabled(False)
        self._set_scan_status(
            "正在读取 vMix 标题…" if self._zh else "Reading vMix titles…",
            "info",
        )
        self._start_scan_thread(host, port)

    def _start_scan_thread(self, host: str, port: str) -> None:
        def work():
            try:
                xml = fetch_vmix_api_xml(host, port)
                inputs = parse_vmix_api_xml(xml)
                return ("ok", inputs)
            except VmixScanError as error:
                return ("error", error)

        def target():
            try:
                result = work()
            except Exception:
                logger.exception("vMix title scan failed")
                result = ("error", VmixScanError("unexpected"))
            try:
                self._scan_bridge.finished.emit(result)
            except RuntimeError:
                logger.debug("vMix title scan finished after the window was closed")

        threading.Thread(target=target, name="vMix title scan", daemon=True).start()

    def _on_scan_finished(self, result) -> None:
        try:
            self._handle_scan_result(result)
        finally:
            self._scan_busy = False
            button = getattr(self, "btn_scan_vmix_fields", None)
            if button is not None:
                button.setEnabled(True)

    def _handle_scan_result(self, result) -> None:
        if not result or result[0] != "ok":
            error = result[1] if result and len(result) > 1 else None
            if not isinstance(error, VmixScanError):
                error = VmixScanError("unexpected")
            text = format_scan_error(error, self._zh)
            logger.warning("vMix title scan: %s", text)
            self._set_scan_status(text, "error")
            return
        self._offer_titles(result[1], self._scan_query)

    def _ask_which_title(self, choice: TitleChoice) -> VmixTitleInput | None:
        labels: list[str] = []
        by_label: dict[str, VmixTitleInput] = {}
        for item in choice.options:
            label = input_choice_label(item)
            if label in by_label:
                label = f"{label}  {item.key}"
            labels.append(label)
            by_label[label] = item
        parent = self.ui.tableView_vmixMapping.window()
        title = "选择 vMix 标题" if self._zh else "Choose a vMix title"
        chosen, ok = QInputDialog.getItem(
            parent,
            title,
            format_pick_prompt(choice, self._zh),
            labels,
            0,
            False,
        )
        if not ok:
            return None
        return by_label.get(str(chosen))

    def _offer_titles(self, inputs: list[VmixTitleInput], query: str) -> None:
        choice = choose_title_input(inputs, query)
        if choice.kind == "none":
            text = format_title_problem(choice, self._zh)
            logger.info("vMix title scan: %s", text)
            self._set_scan_status(text, "error")
            return
        chosen = choice.chosen
        if choice.kind == "pick":
            chosen = self._ask_which_title(choice)
            if chosen is None:
                self._set_scan_status(
                    "已取消，映射没有改。"
                    if self._zh
                    else "Cancelled. Mappings were not changed.",
                    "info",
                )
                return
            self._set_input_number(chosen.number)
        elif not (query or "").strip():
            self._set_input_number(chosen.number)
        filled, kept, unmatched = self._apply_title_fields(chosen)
        text = format_scan_success(chosen, filled, kept, unmatched, self._zh)
        logger.info("vMix title scan: %s", text)
        self._set_scan_status(text, "ok")

    def _apply_title_fields(self, title: VmixTitleInput) -> tuple[int, int, int]:
        """Put ``title.fields`` in the dropdown and guess only placeholder cells.

        A cell is a placeholder when it is blank or still equal to the box
        name (the table's own default). Anything else is a user mapping and
        is not overwritten. Returns (filled, kept, unmatched).
        """
        fields = list(title.fields)
        # Drop the old pickers before rewriting cells, so a combo that is
        # destroyed cannot write its previous text back over a new guess.
        self._suspend_mapping_combos()
        self._field_delegate.field_names = fields
        model = self.ui.tableView_vmixMapping.model()
        if not isinstance(model, QStandardItemModel):
            logger.error("vMix title scan: mapping model is not a QStandardItemModel")
            return (0, 0, 0)
        placeholders: list[tuple[int, str]] = []
        used_fields: set[str] = set()
        kept = 0
        for row in range(model.rowCount()):
            box_item = model.item(row, 0)
            value_item = model.item(row, 1)
            if box_item is None or value_item is None:
                continue
            box = box_item.text()
            current = value_item.text()
            if cell_is_placeholder(box, current):
                placeholders.append((row, box))
            else:
                kept += 1
                if current.strip():
                    used_fields.add(current.strip())
        available = [name for name in fields if name not in used_fields]
        guesses = auto_match_fields([box for _, box in placeholders], available)
        filled = 0
        unmatched = 0
        model.blockSignals(True)
        try:
            for row, box in placeholders:
                guess = guesses.get(box)
                item = model.item(row, 1)
                if guess and item is not None and item.text().strip() != guess:
                    item.setText(guess)
                    filled += 1
                elif guess:
                    filled += 1
                else:
                    unmatched += 1
        finally:
            model.blockSignals(False)
        self._install_mapping_combos()
        if filled:
            self.vmixMappingChanged()
        return filled, kept, unmatched

    def copy_vmix_api_summary(self):
        host = self.ui.lineEdit_vmixHost.text().strip() or "localhost"
        port = self.ui.lineEdit_vmixPort.text().strip() or DEFAULT_VMIX_PORT
        inp = self.ui.inputLineEdit_vmix.text().strip() or "1"
        mapping = {}
        model = self.ui.tableView_vmixMapping.model()
        if isinstance(model, QStandardItemModel):
            mapping = mapping_from_model(model)
        lines = [
            "# 光为 OCR → vMix Web API 摘要（可粘贴备忘）",
            f"# Web Controller: http://{host}:{port}/",
            f"# Input: {inp}",
            "# 示例（浏览器或脚本可调）：",
            f"http://{host}:{port}/api/?Function=SetText&Input={inp}&SelectedName=主队得分.Text&Value=52",
            "",
            "# 当前字段映射（光为字段 → vMix 字段名）：",
        ]
        if mapping:
            for src, dst in mapping.items():
                lines.append(f"#   {src}  =>  {dst}")
                lines.append(
                    f"http://{host}:{port}/api/?Function=SetText&Input={inp}"
                    f"&SelectedName={dst}&Value=测试"
                )
        else:
            lines.append("#   （还没有映射：先在画面上放字段）")
        text_out = "\n".join(lines) + "\n"
        QGuiApplication.clipboard().setText(text_out)
        QMessageBox.information(
            None,
            "已复制",
            "已复制 vMix API 示例和当前映射摘要到剪贴板。\n不会改你现有的连接设置。",
        )

    def updatevMixTable(self, detectionTargets: list[TextDetectionTarget]):
        mapping_storage = fetch_data(SETTINGS_FILE, "vmix_mapping")
        # Replacing the model destroys the row combos. Silence them first so
        # they cannot write a half-cleared value into the new model.
        self._suspend_mapping_combos()
        # a new model with exactly one row per box, so there are no stale rows
        model = QStandardItemModel()
        model.blockSignals(True)

        for box in detectionTargets:
            # add the detection to the vmix output mapping: tableView_vmixMapping
            # check if the table already has the detectionTarget
            items = model.findItems(box.name, Qt.MatchFlag.MatchExactly)
            if len(items) == 0:
                # add the item to the list
                row = model.rowCount()
                model.insertRow(row)
                model.setItem(row, 0, QStandardItem(box.name))
                # the first item shouldn't be editable
                model.item(row, 0).setFlags(Qt.ItemFlag.NoItemFlags)
            else:
                # update the item in the list
                item = items[0]
                row = item.row()

            # get value from storage or use the box name
            if mapping_storage and box.name in mapping_storage:
                model.setItem(row, 1, QStandardItem(mapping_storage[box.name]))
            else:
                model.setItem(row, 1, QStandardItem(box.name))

        model.blockSignals(False)
        self.ui.tableView_vmixMapping.setModel(model)
        self.ui.tableView_vmixMapping.model().dataChanged.connect(
            self.vmixMappingChanged
        )
        self._set_mapping_headers()
        # send with the mapping the table shows (including the defaults for boxes
        # that have no stored mapping yet), without storing it
        self.vmixUpdater.set_field_mapping(mapping_from_model(model))
        self._install_mapping_combos()

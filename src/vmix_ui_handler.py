import threading

from PySide6.QtGui import QGuiApplication, QStandardItem, QStandardItemModel
from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QStyledItemDelegate,
)

from theme import button_text_color, color as theme_color, media_icon, muted_color

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


class VmixFieldDelegate(QStyledItemDelegate):
    """Editable combo for the vMix field column.

    With no scanned names yet, the cell is an ordinary line edit, same as
    before. After a scan, the dropdown lists those names and still accepts
    a name typed by hand.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.field_names: list[str] = []
        self.placeholder = ""

    def createEditor(self, parent, option, index):
        if not self.field_names:
            return super().createEditor(parent, option, index)
        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItems(self.field_names)
        if self.placeholder and combo.lineEdit() is not None:
            combo.lineEdit().setPlaceholderText(self.placeholder)
        combo.activated.connect(lambda _index, box=combo: self._commit_editor(box))
        # Open the list on the click that started the edit. Tied to the combo
        # so it is dropped if the editor is destroyed first.
        QTimer.singleShot(0, combo, combo.showPopup)
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
        view.setEditTriggers(
            QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        mapping = fetch_data(SETTINGS_FILE, "vmix_mapping", {})
        if mapping:
            self.vmixUpdater.set_field_mapping(mapping)

        view.model().dataChanged.connect(self.vmixMappingChanged)
        self._set_mapping_headers()

        self.ui.pushButton_startvmix.toggled.connect(self.togglevMix)
        self._scan_bridge = _ScanBridge(view)
        self._scan_bridge.finished.connect(self._on_scan_finished)
        self._install_vmix_guide()

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
            self._connect_guide_buttons(replace=True)
            self._vmix_guide_installed = True
            self.apply_vmix_guide_texts(self._zh)
            return
        parent = getattr(self.ui, "tab_vmix", None)
        layout = getattr(self.ui, "verticalLayout_6", None)
        if parent is None or layout is None:
            return
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
        layout.addWidget(self.label_vmix_guide)
        layout.addWidget(self.btn_scan_vmix_fields)
        layout.addWidget(self.label_vmix_scan_status)
        layout.addWidget(self.btn_copy_vmix_api)
        self.ui.label_vmix_guide = self.label_vmix_guide
        self.ui.btn_scan_vmix_fields = self.btn_scan_vmix_fields
        self.ui.label_vmix_scan_status = self.label_vmix_scan_status
        self.ui.btn_copy_vmix_api = self.btn_copy_vmix_api
        self._vmix_guide_installed = True
        self.apply_vmix_guide_texts(True)

    def _set_mapping_headers(self):
        model = self.ui.tableView_vmixMapping.model()
        if not isinstance(model, QStandardItemModel):
            return
        if self._zh:
            labels = ["光为字段", "vMix 字段"]
        else:
            labels = ["Field", "vMix field"]
        model.setHorizontalHeaderLabels(labels)
        self.ui.tableView_vmixMapping.horizontalHeader().setVisible(True)

    def apply_vmix_guide_texts(self, zh: bool = True):
        self._zh = zh
        self._field_delegate.placeholder = "下拉或手打" if zh else "Pick or type"
        self._set_mapping_headers()
        if not getattr(self, "_vmix_guide_installed", False):
            return
        if zh:
            self.ui.pushButton_startvmix.setToolTip(
                "进阶。按映射表用 Web API 的 SetText 更新标题文字。"
                "日常请用「给 vMix 的 JSON」。"
            )
            self.ui.tableView_vmixMapping.setToolTip(
                "左列是光为的字段，右列是 vMix 标题里的文字名。"
                "点右列可下拉（扫描之后）或手打。"
                "扫描只填空着的和仍显示字段原名的格子，你改过的不会被盖掉。"
            )
            self.btn_scan_vmix_fields.setText("进阶：扫描标题字段（API）")
            self.btn_scan_vmix_fields.setToolTip(
                "可选。向 vMix Web Controller 读取标题里的文字字段，填进右列下拉。"
                "不改你已经填好的映射。日常出站请用「给 vMix 的 JSON」。"
            )
            self.btn_copy_vmix_api.setText("一键复制 API 示例 / 当前映射")
            self.label_vmix_guide.setText(
                "<b>进阶：vMix API（一般不用）</b><br>"
                "大多数人用左边「文本文件」里的"
                "<b>「给 vMix 的 JSON（推荐）」</b>就够："
                "vMix 用 Data Sources 读那一个文件。这一页是可选的，"
                "给已经做好标题、想让软件直接改标题文字的人。<br>"
                "1）vMix → Settings → Web Controller → 勾选 Enable（端口一般 8088）<br>"
                "2）填主机和端口。Input 可填编号、标题名或 Key；"
                "空着或对不上时，扫描会让你选一个标题<br>"
                "3）点「进阶：扫描标题字段（API）」。右列变成下拉，也可以手打。"
                "空着的、以及还显示成光为字段原名的格子，会按名字猜一次；"
                "<b>你已经改过的格子不会被盖掉</b><br>"
                "4）核对映射后，再点「启动 vMix」"
            )
        else:
            self.ui.pushButton_startvmix.setToolTip(
                "Advanced. Push mapped fields with the Web API SetText. "
                "Most people should use JSON for vMix instead."
            )
            self.ui.tableView_vmixMapping.setToolTip(
                "Left: ScoreSight field. Right: vMix text field. "
                "After a scan, click the right column for a dropdown, or type. "
                "A scan fills empty cells and cells still showing the box name; "
                "cells you already edited are kept."
            )
            self.btn_scan_vmix_fields.setText("Advanced: scan title fields (API)")
            self.btn_scan_vmix_fields.setToolTip(
                "Optional. Read text field names from the vMix Web Controller. "
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
                "The right column becomes a dropdown you can also type in. "
                "Empty cells and cells still showing the box name are guessed once; "
                "<b>cells you already edited are kept</b>."
            )

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

from PySide6.QtGui import QGuiApplication, QStandardItemModel, QStandardItem
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton

from theme import button_text_color, media_icon

from text_detection_target import TextDetectionTarget
from ui_mainwindow import Ui_MainWindow
from vmix_output import DEFAULT_VMIX_PORT, VMIX_TCP_API_PORT, VMixAPI
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


class VMixUIHanlder:
    def __init__(self, ui: Ui_MainWindow):
        self.ui = ui
        self.vmixUpdater = None
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
        self.ui.tableView_vmixMapping.setModel(QStandardItemModel())
        mapping = fetch_data(SETTINGS_FILE, "vmix_mapping", {})
        if mapping:
            self.vmixUpdater.set_field_mapping(mapping)

        self.ui.tableView_vmixMapping.model().dataChanged.connect(
            self.vmixMappingChanged
        )

        self.ui.pushButton_startvmix.toggled.connect(self.togglevMix)
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

    def _install_vmix_guide(self):
        """Chinese how-to + one-click copy of a SetText example / mapping summary."""
        if getattr(self, "_vmix_guide_installed", False):
            return
        parent = self.ui.tab_vmix
        layout = self.ui.verticalLayout_6
        self.label_vmix_guide = QLabel(parent)
        self.label_vmix_guide.setWordWrap(True)
        self.label_vmix_guide.setTextFormat(Qt.RichText)
        self.label_vmix_guide.setText(
            "<b>方式③：vMix API（SetText）</b><br>"
            "1）vMix → Settings → Web Controller → 勾选 Enable（端口一般 8088）<br>"
            "2）填好本页主机/端口/Input 号，点「启动 vMix」<br>"
            "3）下表右列填标题里的字段名（和 GT 标题一致）<br>"
            "适合已经做好标题模板、不想用 Data Sources 文件的场合。"
            "日常更省事的是「文本文件 → 给 vMix 的 JSON」。"
        )
        self.btn_copy_vmix_api = QPushButton("一键复制 API 示例 / 当前映射", parent)
        self.btn_copy_vmix_api.setCursor(Qt.PointingHandCursor)
        self.btn_copy_vmix_api.clicked.connect(self.copy_vmix_api_summary)
        # insert above the mapping table area: after send_same checkbox
        layout.addWidget(self.label_vmix_guide)
        layout.addWidget(self.btn_copy_vmix_api)
        self._vmix_guide_installed = True
        self.apply_vmix_guide_texts(True)

    def apply_vmix_guide_texts(self, zh: bool = True):
        if not getattr(self, "_vmix_guide_installed", False):
            return
        if zh:
            self.ui.pushButton_startvmix.setToolTip(
                "启动后按映射表用 Web API 的 SetText 更新 vMix 标题字段"
            )
            self.btn_copy_vmix_api.setText("一键复制 API 示例 / 当前映射")
            self.label_vmix_guide.setText(
                "<b>方式③：vMix API（SetText）</b><br>"
                "1）vMix → Settings → Web Controller → 勾选 Enable（端口一般 8088）<br>"
                "2）填好本页主机/端口/Input 号，点启动<br>"
                "3）下表右列填标题里的字段名<br>"
                "新手优先用「出站 → 文本文件 → 给 vMix 的 JSON」。"
            )
        else:
            self.btn_copy_vmix_api.setText("Copy API example / mapping")
            self.label_vmix_guide.setText(
                "<b>vMix Web API (SetText)</b><br>"
                "Enable Web Controller (port 8088), map field names, Start vMix."
            )

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
        # send with the mapping the table shows (including the defaults for boxes
        # that have no stored mapping yet), without storing it
        self.vmixUpdater.set_field_mapping(mapping_from_model(model))

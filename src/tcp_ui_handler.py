"""The TCP / UDP output's page in the Outputs list (tcp_output.ui, added to
stackedWidget_outputs): binds its controls to the "tcp_output" and
"tcp_mapping" settings and to the TcpOutput that sends.

The settings take effect as they are changed: a text (template, "on connect"
message, template name, WebSocket headers) a moment after the typing stops,
the host, port and URL when Enter is pressed or the field is left. The preview shows what the current
values make with the settings being edited.
"""

import filecmp
import shutil
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import (
    QFont,
    QStandardItem,
    QStandardItemModel,
    QTextOption,
)
from PySide6.QtWidgets import QFileDialog, QMessageBox, QToolTip, QWidget

import output_status
from resource_path import resource_path
from sc_logging import logger
from status_bar import primary_lan_address
from storage import fetch_data, store_data
from tcp_output import (
    CASPARCG,
    CHYRON,
    CUSTOM,
    ENCODINGS,
    ESCAPES,
    LIVE_SCORE,
    MAGICSOFT,
    MODES,
    PER_FIELD,
    PRESETS,
    TCP_SERVER,
    TRANSPORTS,
    VIZRT,
    WEBSOCKET,
    WEBSOCKET_JSON,
    TcpConfig,
    TcpOutput,
    default_field_name,
    parse_port,
    preview,
    visible_text,
)
from theme import button_text_color, media_icon, muted_color
from ui_tcp_output import Ui_TcpOutputPage

SETTINGS_FILE = "scoresight.json"
SETTINGS_KEY = "tcp_output"
MAPPING_KEY = "tcp_mapping"
CASPARCG_TEMPLATE_FILE = "scoresight_scorebug.html"

# the presets in the picker: the products, then Custom
PRESET_ORDER = (CASPARCG, CHYRON, LIVE_SCORE, MAGICSOFT, VIZRT, WEBSOCKET_JSON, CUSTOM)

# values for the preview before the first readings
SAMPLE_VALUES = {
    "Home Score": "21",
    "Away Score": "17",
    "Time": "04:32",
    "Period": "2",
    "Home Fouls": "3",
    "Away Fouls": "5",
    "Shot Clock": "14",
}
SAMPLE_VALUE = "12"

# the template, "on connect" and preview font (the first one installed)
MONOSPACE_FAMILIES = [
    "Consolas",
    "Menlo",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "Courier New",
]

# a text setting is applied this long after the typing stops (ms)
TEXT_APPLY_DELAY_MS = 400


def preset_name(key: str) -> str:
    """The preset's name in the picker ("Custom" is translated)."""
    if key == CUSTOM:
        return QCoreApplication.translate("TcpOutput", "Custom")
    return PRESETS[key].name


def preset_note(key: str) -> str:
    """Where the preset's protocol comes from and what to set up."""
    if key == CASPARCG:
        return QCoreApplication.translate(
            "TcpOutput",
            "From CasparCG's AMCP documentation; not tested with CasparCG yet. "
            "Copy the bundled template into CasparCG's templates folder.",
        )
    if key == CHYRON:
        return QCoreApplication.translate(
            "TcpOutput",
            "From Chyron's Intelligent Interface documentation; not tested with "
            "PRIME yet. Use the port of PRIME's Intelligent Interface connection, "
            "and name the scene's objects like the names below.",
        )
    if key == LIVE_SCORE:
        return QCoreApplication.translate(
            "TcpOutput",
            "Based on public documentation, untested: the format comes from "
            "another OCR app's guide. Use the port and the field names of Live "
            "Score's OCR Setup.",
        )
    if key == MAGICSOFT:
        return QCoreApplication.translate(
            "TcpOutput",
            "Based on public documentation, untested: the format comes from "
            "another OCR app's guide. Put [name] in a text item, then File › "
            "Refresh All projects.",
        )
    if key == VIZRT:
        return QCoreApplication.translate(
            "TcpOutput",
            "From Vizrt's Shared Memory documentation; not tested with Viz Engine "
            "yet. Use the Shared Memory port set in Viz Configuration.",
        )
    if key == WEBSOCKET_JSON:
        return QCoreApplication.translate(
            "TcpOutput",
            "For your own web app or server: each time a value changes, a JSON "
            "object with every field goes to its WebSocket URL. A token goes in "
            "Headers (Authorization: Bearer …) or in the URL.",
        )
    return QCoreApplication.translate(
        "TcpOutput",
        "Your own protocol: edit the template and the options. The preview shows "
        "what is sent.",
    )


def listen_note(port: int | None, lan_address: str | None) -> str:
    """Where other programs connect to the TCP server."""
    port_text = "?" if port is None else str(port)
    if lan_address:
        return QCoreApplication.translate(
            "TcpOutput",
            "TCP server: other programs connect to {address}:{port} "
            "(localhost:{port} on this computer), and each one gets the messages.",
        ).format(address=lan_address, port=port_text)
    return QCoreApplication.translate(
        "TcpOutput",
        "TCP server: other programs connect to this computer's address, port "
        "{port} (localhost:{port} on this computer), and each one gets the "
        "messages.",
    ).format(port=port_text)


def _combo_texts() -> dict[str, list[tuple[str, str]]]:
    """(text, key) of the items of each choice."""
    return {
        "mode": [
            (
                QCoreApplication.translate("TcpOutput", "A message per changed field"),
                "field",
            ),
            (
                QCoreApplication.translate("TcpOutput", "One message with every field"),
                "frame",
            ),
        ],
        "line_ending": [
            ("CR LF (\\r\\n)", "crlf"),
            ("LF (\\n)", "lf"),
            ("CR (\\r)", "cr"),
            ("NUL (\\0)", "nul"),
            (QCoreApplication.translate("TcpOutput", "None"), "none"),
        ],
        "encoding": [
            ("UTF-8", "utf-8"),
            ("ISO-8859-1 (Latin-1)", "iso-8859-1"),
            ("Windows-1252", "cp1252"),
            ("UTF-16 LE", "utf-16-le"),
            ("ASCII", "ascii"),
        ],
        "escape": [
            (QCoreApplication.translate("TcpOutput", "None"), "none"),
            (QCoreApplication.translate("TcpOutput", "Line breaks to spaces"), "line"),
            (QCoreApplication.translate("TcpOutput", "JSON string"), "json"),
            ("XML", "xml"),
            (QCoreApplication.translate("TcpOutput", "AMCP (CasparCG)"), "amcp"),
            (QCoreApplication.translate("TcpOutput", "Chyron fields"), "chyron"),
            (QCoreApplication.translate("TcpOutput", "Leave out NUL"), "nul"),
        ],
    }


class TcpUIHandler(QObject):
    """The page, its TcpOutput (self.output) and the field names table."""

    def __init__(self, stacked_widget, parent=None):
        super().__init__(parent)
        self.page = QWidget()
        self.ui = Ui_TcpOutputPage()
        self.ui.setupUi(self.page)
        # like the other output pages (tab_vmix...)
        self.page.setObjectName("tab_tcp")
        stacked_widget.addWidget(self.page)

        config = TcpConfig.from_dict(fetch_data(SETTINGS_FILE, SETTINGS_KEY))
        stored = fetch_data(SETTINGS_FILE, MAPPING_KEY, {})
        # {box name: name}, only where it isn't the preset's name
        self.mappingOverrides: dict[str, str] = {
            str(box): str(name)
            for box, name in (stored.items() if isinstance(stored, dict) else ())
        }
        self.boxNames: list[str] = []
        self.output = TcpOutput(config)
        self._filling = False  # the widgets are being set from the settings

        # apply a typed text a moment after the typing stops
        self.textTimer = QTimer(self)
        self.textTimer.setSingleShot(True)
        self.textTimer.setInterval(TEXT_APPLY_DELAY_MS)
        self.textTimer.timeout.connect(self.applyWidgets)

        self.setupWidgets()
        self.applyTexts()
        self.showConfig(config)
        self.connectSignals()
        self.updateMappingTable()
        self.updateRunningControls(False)

    # --- setup ----------------------------------------------------------------

    def setupWidgets(self):
        ui = self.ui
        # a monospace font as large as the rest of the page
        fixed = QFont(self.page.font())
        fixed.setFamilies(MONOSPACE_FAMILIES)
        fixed.setStyleHint(QFont.StyleHint.Monospace)
        fixed.setFixedPitch(True)
        for widget in (
            ui.plainTextEdit_tcpTemplate,
            ui.plainTextEdit_tcpPreview,
            ui.lineEdit_tcpInit,
        ):
            widget.setFont(fixed)
        for edit in (ui.plainTextEdit_tcpTemplate, ui.plainTextEdit_tcpPreview):
            edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        ui.tableView_tcpMapping.setModel(QStandardItemModel(ui.tableView_tcpMapping))
        # the host (or a WebSocket's URL) takes the row's width; without it
        # (TCP server), the port and the transport stay on the left
        row = ui.horizontalLayout_tcpAddressRow
        row.setStretchFactor(ui.lineEdit_tcpHost, 1)
        row.setStretchFactor(ui.lineEdit_tcpUrl, 1)
        row.addStretch(0)
        # the scroll area doesn't give the wrapped labels the height they need
        # at its width: fitContentHeight() does, whenever the width changes
        self._viewport = ui.scrollArea_tcp.viewport()
        self._viewport.installEventFilter(self)
        self.refreshTheme()

    def eventFilter(self, watched, event):
        # (no Qt call before the check: it also runs while the window is
        # destroyed)
        if watched is self._viewport and event.type() == QEvent.Type.Resize:
            self.fitContentHeight()
        return super().eventFilter(watched, event)

    def fitContentHeight(self):
        """At least the height the page's layout needs at the scroll area's
        width (the word-wrapped notes take more lines when it's narrow)."""
        contents = self.ui.scrollAreaWidgetContents_tcp
        layout = contents.layout()
        width = self.ui.scrollArea_tcp.viewport().width()
        if layout is not None and layout.hasHeightForWidth() and width > 0:
            height = layout.heightForWidth(width)
            if height > 0 and contents.minimumHeight() != height:
                contents.setMinimumHeight(height)

    def connectSignals(self):
        ui = self.ui
        ui.comboBox_tcpPreset.activated.connect(self.presetChosen)
        ui.toolButton_tcpResetPreset.clicked.connect(
            lambda: self.presetChosen(ui.comboBox_tcpPreset.currentIndex())
        )
        ui.lineEdit_tcpHost.editingFinished.connect(self.applyWidgets)
        ui.lineEdit_tcpPort.editingFinished.connect(self.applyWidgets)
        ui.lineEdit_tcpUrl.editingFinished.connect(self.applyWidgets)
        ui.plainTextEdit_tcpHeaders.textChanged.connect(self.textEdited)
        for combo in (
            ui.comboBox_tcpTransport,
            ui.comboBox_tcpMode,
            ui.comboBox_tcpLineEnding,
            ui.comboBox_tcpEncoding,
            ui.comboBox_tcpEscape,
        ):
            combo.currentIndexChanged.connect(self.applyWidgets)
        for spin in (
            ui.spinBox_tcpChannel,
            ui.spinBox_tcpLayer,
            ui.spinBox_tcpCgLayer,
            ui.doubleSpinBox_tcpMinInterval,
            ui.doubleSpinBox_tcpRefresh,
        ):
            spin.valueChanged.connect(self.applyWidgets)
        ui.checkBox_tcpSendSame.toggled.connect(self.applyWidgets)
        ui.plainTextEdit_tcpTemplate.textChanged.connect(self.textEdited)
        ui.lineEdit_tcpInit.textChanged.connect(self.textEdited)
        ui.lineEdit_tcpTemplateName.textChanged.connect(self.textEdited)
        ui.pushButton_tcpStart.toggled.connect(self.toggleRunning)
        ui.pushButton_tcpLoad.clicked.connect(self.output.resend_init)
        ui.pushButton_tcpPlay.clicked.connect(
            lambda: self.output.send_command("CG {channel}-{layer} PLAY {cg_layer}")
        )
        ui.pushButton_tcpStop.clicked.connect(
            lambda: self.output.send_command("CG {channel}-{layer} STOP {cg_layer}")
        )
        ui.pushButton_tcpCopyTemplate.clicked.connect(self.copyCasparTemplate)
        ui.toolButton_tcpResetNames.clicked.connect(self.resetNames)

    def applyTexts(self):
        """The texts set in code (the choices, the button, the notes), in the
        current language."""
        ui = self.ui
        self._filling = True
        try:
            combo = ui.comboBox_tcpPreset
            current = combo.currentData()
            combo.clear()
            for key in PRESET_ORDER:
                combo.addItem(preset_name(key), key)
            combo.setCurrentIndex(
                max(0, combo.findData(current or self.output.config.preset))
            )
            texts = _combo_texts()
            for name, combo in (
                ("mode", ui.comboBox_tcpMode),
                ("line_ending", ui.comboBox_tcpLineEnding),
                ("encoding", ui.comboBox_tcpEncoding),
                ("escape", ui.comboBox_tcpEscape),
            ):
                current = combo.currentData()
                combo.clear()
                for text, key in texts[name]:
                    combo.addItem(text, key)
                if current is not None:
                    combo.setCurrentIndex(max(0, combo.findData(current)))
        finally:
            self._filling = False
        ui.checkBox_tcpSendSame.setText(
            QCoreApplication.translate("MainWindow", "Also resend unchanged values")
        )
        ui.checkBox_tcpSendSame.setToolTip(
            QCoreApplication.translate(
                "TcpOutput",
                "Also send a field's value when it hasn't changed, at every "
                "detection. Off: a value is sent only when it changes.",
            )
        )
        ui.doubleSpinBox_tcpMinInterval.setSpecialValueText(
            QCoreApplication.translate("TcpOutput", "No limit")
        )
        ui.doubleSpinBox_tcpRefresh.setSpecialValueText(
            QCoreApplication.translate("TcpOutput", "Never")
        )
        self.updateToggleText(ui.pushButton_tcpStart.isChecked())
        self.updateAddressRow(self.widgetConfig().transport)
        self.updatePresetNote()
        self.updatePreview()
        self.updateReply()

    def retranslate(self):
        """After a language change."""
        self.ui.retranslateUi(self.page)
        self.applyTexts()
        self.updateMappingTable()

    def refreshTheme(self):
        """Colours that follow the light/dark palette."""
        palette = self.page.palette()
        style = f"color: {muted_color(palette).name()};"
        self.ui.label_tcpPresetNote.setStyleSheet(style)
        self.ui.label_tcpReply.setStyleSheet(style)
        self.updateToggleText(self.ui.pushButton_tcpStart.isChecked())

    # --- settings ---------------------------------------------------------------

    def showConfig(self, config: TcpConfig):
        """Set the widgets from settings (without applying them again)."""
        ui = self.ui
        self._filling = True
        try:
            ui.comboBox_tcpPreset.setCurrentIndex(
                max(0, ui.comboBox_tcpPreset.findData(config.preset))
            )
            ui.lineEdit_tcpHost.setText(config.host)
            ui.lineEdit_tcpPort.setText("" if config.port is None else str(config.port))
            ui.lineEdit_tcpUrl.setText(config.url)
            ui.lineEdit_tcpUrl.setCursorPosition(0)  # show its start
            if ui.plainTextEdit_tcpHeaders.toPlainText() != config.headers:
                ui.plainTextEdit_tcpHeaders.setPlainText(config.headers)
            ui.comboBox_tcpTransport.setCurrentIndex(TRANSPORTS.index(config.transport))
            ui.comboBox_tcpMode.setCurrentIndex(MODES.index(config.mode))
            if ui.plainTextEdit_tcpTemplate.toPlainText() != config.template:
                ui.plainTextEdit_tcpTemplate.setPlainText(config.template)
            ui.lineEdit_tcpInit.setText(config.init)
            ui.lineEdit_tcpInit.setCursorPosition(0)  # show its start
            ui.comboBox_tcpLineEnding.setCurrentIndex(
                max(0, ui.comboBox_tcpLineEnding.findData(config.line_ending))
            )
            ui.comboBox_tcpEncoding.setCurrentIndex(
                max(0, ui.comboBox_tcpEncoding.findData(config.encoding))
            )
            ui.comboBox_tcpEscape.setCurrentIndex(
                max(0, ui.comboBox_tcpEscape.findData(config.escape))
            )
            ui.checkBox_tcpSendSame.setChecked(config.send_same)
            ui.doubleSpinBox_tcpMinInterval.setValue(config.min_interval)
            ui.doubleSpinBox_tcpRefresh.setValue(config.refresh_interval)
            ui.spinBox_tcpChannel.setValue(config.channel)
            ui.spinBox_tcpLayer.setValue(config.layer)
            ui.spinBox_tcpCgLayer.setValue(config.cg_layer)
            ui.lineEdit_tcpTemplateName.setText(config.template_name)
        finally:
            self._filling = False
        self.updateVisibility(config)
        self.updatePresetNote()
        self.updatePreview()

    def widgetConfig(self) -> TcpConfig:
        """The settings the widgets show."""
        ui = self.ui
        return TcpConfig.from_dict(
            {
                "preset": ui.comboBox_tcpPreset.currentData() or CUSTOM,
                "host": ui.lineEdit_tcpHost.text().strip(),
                "port": parse_port(ui.lineEdit_tcpPort.text()),
                "url": ui.lineEdit_tcpUrl.text().strip(),
                "headers": ui.plainTextEdit_tcpHeaders.toPlainText(),
                "transport": TRANSPORTS[
                    max(0, ui.comboBox_tcpTransport.currentIndex())
                ],
                "mode": MODES[max(0, ui.comboBox_tcpMode.currentIndex())],
                "template": ui.plainTextEdit_tcpTemplate.toPlainText(),
                "init": ui.lineEdit_tcpInit.text(),
                "line_ending": ui.comboBox_tcpLineEnding.currentData() or "lf",
                "encoding": ui.comboBox_tcpEncoding.currentData() or ENCODINGS[0],
                "escape": ui.comboBox_tcpEscape.currentData() or ESCAPES[0],
                "send_same": ui.checkBox_tcpSendSame.isChecked(),
                "min_interval": ui.doubleSpinBox_tcpMinInterval.value(),
                "refresh_interval": ui.doubleSpinBox_tcpRefresh.value(),
                "channel": ui.spinBox_tcpChannel.value(),
                "layer": ui.spinBox_tcpLayer.value(),
                "cg_layer": ui.spinBox_tcpCgLayer.value(),
                "template_name": ui.lineEdit_tcpTemplateName.text().strip(),
            }
        )

    def textEdited(self, *_):
        if self._filling:
            return
        self.updatePreview()
        self.textTimer.start()

    def applyWidgets(self, *_):
        """Store and use the settings the widgets show."""
        if self._filling:
            return
        self.textTimer.stop()
        self.applyConfig(self.widgetConfig())

    def applyConfig(self, config: TcpConfig):
        old = self.output.config
        if config != old:
            store_data(SETTINGS_FILE, SETTINGS_KEY, config.to_dict())
            self.output.set_config(config)
        self.updateVisibility(config)
        if (config.preset, config.listens, config.port) != (
            old.preset,
            old.listens,
            old.port,
        ):
            self.updatePresetNote()
        if config.preset != old.preset:
            # the preset's default names
            self.updateMappingTable()
        self.updatePreview()

    def presetChosen(self, index: int):
        """A preset was picked (or Reset): its port, template and options."""
        key = self.ui.comboBox_tcpPreset.itemData(index) or CUSTOM
        config = self.widgetConfig().with_preset(key)
        self.showConfig(config)
        self.applyConfig(config)

    def flushPendingEdits(self):
        """Apply a text being typed now (e.g. before the window closes)."""
        if self.textTimer.isActive():
            self.applyWidgets()

    # --- running ----------------------------------------------------------------

    def setRunning(self, on: bool):
        """Start or stop (the destination's switch): the button decides."""
        self.ui.pushButton_tcpStart.setChecked(on)

    def toggleRunning(self, on: bool):
        self.flushPendingEdits()
        self.updateToggleText(on)
        if on:
            self.output.start()
        else:
            self.output.stop()
        self.updateRunningControls(on)
        self.updateReply()

    def updateToggleText(self, running: bool):
        button = self.ui.pushButton_tcpStart
        button.setText(
            QCoreApplication.translate("MainWindow", "Stop sending")
            if running
            else QCoreApplication.translate("MainWindow", "Start sending")
        )
        button.setIcon(
            media_icon(
                "stop" if running else "play", button_text_color(button.palette())
            )
        )

    def updateRunningControls(self, running: bool):
        # CasparCG commands go over the connection
        for button in (
            self.ui.pushButton_tcpLoad,
            self.ui.pushButton_tcpPlay,
            self.ui.pushButton_tcpStop,
        ):
            button.setEnabled(running)

    def restoreUiState(self):
        """Show the settings and the running state again (e.g. after a
        language change)."""
        self.showConfig(self.output.config)
        self.ui.pushButton_tcpStart.setChecked(self.output.running)
        self.updateToggleText(self.output.running)
        self.updateRunningControls(self.output.running)

    def close(self):
        self.flushPendingEdits()
        self._viewport.removeEventFilter(self)
        self.output.close()

    # --- what the page shows ------------------------------------------------------

    def updateVisibility(self, config: TcpConfig):
        caspar = config.preset == CASPARCG
        self.ui.groupBox_tcpCaspar.setVisible(
            caspar or config.uses_casparcg_parameters()
        )
        self.ui.widget_tcpCasparButtons.setVisible(caspar)
        self.ui.pushButton_tcpCopyTemplate.setVisible(caspar)
        self.updateAddressRow(config.transport)
        self.fitContentHeight()

    def updateAddressRow(self, transport: str):
        """A TCP server needs only its port: no host; a WebSocket a URL (and
        maybe headers) instead of the host and port. WebSocket messages are
        always UTF-8."""
        ui = self.ui
        listens = transport == TCP_SERVER
        websocket = transport == WEBSOCKET
        ui.lineEdit_tcpUrl.setVisible(websocket)
        ui.lineEdit_tcpHost.setVisible(not listens and not websocket)
        ui.label_tcpColon.setVisible(not listens and not websocket)
        ui.lineEdit_tcpPort.setVisible(not websocket)
        ui.formLayout_tcp.setRowVisible(ui.plainTextEdit_tcpHeaders, websocket)
        ui.comboBox_tcpEncoding.setEnabled(not websocket)
        if websocket:
            ui.label_tcpAddress.setText(QCoreApplication.translate("TcpOutput", "URL"))
            ui.label_tcpAddress.setBuddy(ui.lineEdit_tcpUrl)
            return
        ui.label_tcpAddress.setBuddy(ui.lineEdit_tcpHost)
        if listens:
            ui.label_tcpAddress.setText(
                QCoreApplication.translate("TcpOutput", "Listen on port")
            )
            ui.lineEdit_tcpPort.setToolTip(
                QCoreApplication.translate(
                    "TcpOutput",
                    "The port ScoreSight listens on. Set the same port in the "
                    "program that connects to ScoreSight.",
                )
            )
        else:
            ui.label_tcpAddress.setText(
                QCoreApplication.translate("TcpOutputPage", "Address")
            )
            ui.lineEdit_tcpPort.setToolTip(
                QCoreApplication.translate(
                    "TcpOutputPage",
                    "The port the receiving program listens on (set in that program)",
                )
            )

    def updatePresetNote(self):
        note = preset_note(self.ui.comboBox_tcpPreset.currentData() or CUSTOM)
        config = self.widgetConfig()
        if config.listens:
            note += "\n" + listen_note(config.port, primary_lan_address())
        self.ui.label_tcpPresetNote.setText(note)
        self.fitContentHeight()

    def previewValues(self):
        """The current values, or sample ones before the first readings."""
        values = self.output.current_values()
        if values:
            return values
        names = self.boxNames or list(SAMPLE_VALUES)[:2]
        samples = []
        for box in names:
            name = self.output.field_name(box)
            if name:
                samples.append((name, SAMPLE_VALUES.get(box, SAMPLE_VALUE)))
        return tuple(samples)

    def updatePreview(self):
        config = self.widgetConfig()
        result = preview(config, self.previewValues())
        parts = []
        if result.on_connect:
            parts.append(QCoreApplication.translate("TcpOutput", "On connect:"))
            parts.append(visible_text(result.on_connect).rstrip("\n"))
        if config.mode == PER_FIELD:
            parts.append(
                QCoreApplication.translate("TcpOutput", "For a changed field:")
            )
        else:
            parts.append(
                QCoreApplication.translate("TcpOutput", "When a value changes:")
            )
        parts.append(
            visible_text(result.message).rstrip("\n")
            or QCoreApplication.translate("TcpOutput", "(nothing)")
        )
        if result.more_fields:
            parts.append(
                QCoreApplication.translate(
                    "TcpOutput",
                    "(and a message like it for each of {count} more fields)",
                ).format(count=result.more_fields)
            )
        text = "\n".join(parts)
        edit = self.ui.plainTextEdit_tcpPreview
        if edit.toPlainText() != text:
            # the values change every second: keep where it was scrolled to
            bar = edit.verticalScrollBar()
            position = bar.value()
            edit.setPlainText(text)
            bar.setValue(position)

    def updateReply(self):
        label = self.ui.label_tcpReply
        status = self.output.connection_status()
        if self.output.running and status.last_reply:
            text = QCoreApplication.translate(
                "TcpOutput", "Last answer: {reply}"
            ).format(reply=status.last_reply)
            if label.text() != text or label.isHidden():
                label.setText(text)
                label.show()
                self.fitContentHeight()
        elif not label.isHidden():
            label.hide()
            self.fitContentHeight()

    def refresh(self):
        """Once a second: the last answer, and the preview of the current
        values (while the page shows)."""
        self.updateReply()
        if self.page.isVisible():
            self.updatePreview()

    def destinationStatus(self) -> output_status.DestinationStatus:
        config = self.output.config
        return output_status.tcp_status(
            self.output.running,
            config,
            "" if config.preset == CUSTOM else PRESETS[config.preset].name,
            self.hasFields(),
            self.output.status(),
            self.output.connection_status(),
        )

    # --- field names ------------------------------------------------------------

    def effectiveMapping(self) -> dict[str, str]:
        preset = self.output.config.preset
        return {
            box: self.mappingOverrides.get(box, default_field_name(preset, box))
            for box in self.boxNames
        }

    def hasFields(self) -> bool:
        return any(name.strip() for name in self.effectiveMapping().values())

    def updateTable(self, detectionTargets):
        """The boxes changed: a row per box."""
        self.boxNames = [box.name for box in detectionTargets]
        self.output.forget_boxes(set(self.boxNames))
        self.updateMappingTable()

    def updateMappingTable(self):
        view = self.ui.tableView_tcpMapping
        model = QStandardItemModel(view)
        for box, name in self.effectiveMapping().items():
            box_item = QStandardItem(box)
            box_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            model.appendRow([box_item, QStandardItem(name)])
        old = view.model()
        view.setModel(model)
        if old is not None:
            old.deleteLater()
        model.dataChanged.connect(self.mappingEdited)
        self.output.set_field_mapping(self.effectiveMapping())
        self.updatePreview()

    def mappingEdited(self, *_):
        model = self.ui.tableView_tcpMapping.model()
        preset = self.output.config.preset
        for row in range(model.rowCount()):
            box = model.item(row, 0).text()
            name = model.item(row, 1).text()
            if name == default_field_name(preset, box):
                self.mappingOverrides.pop(box, None)
            else:
                self.mappingOverrides[box] = name
        store_data(SETTINGS_FILE, MAPPING_KEY, dict(self.mappingOverrides))
        self.output.set_field_mapping(self.effectiveMapping())
        self.updatePreview()

    def resetNames(self):
        self.mappingOverrides.clear()
        store_data(SETTINGS_FILE, MAPPING_KEY, {})
        self.updateMappingTable()

    # --- the CasparCG template ------------------------------------------------------

    def copyCasparTemplate(self):
        folder = QFileDialog.getExistingDirectory(
            self.page,
            QCoreApplication.translate(
                "TcpOutput", "Choose CasparCG's templates folder"
            ),
        )
        if not folder:
            return
        source = Path(resource_path("casparcg", "scoresight_scorebug.html"))
        target = Path(folder) / CASPARCG_TEMPLATE_FILE
        try:
            if target.exists() and not filecmp.cmp(source, target, shallow=False):
                answer = QMessageBox.question(
                    self.page,
                    QCoreApplication.translate("TcpOutput", "Replace the template?"),
                    QCoreApplication.translate(
                        "TcpOutput", "{file} already exists in {folder}. Replace it?"
                    ).format(file=CASPARCG_TEMPLATE_FILE, folder=folder),
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            shutil.copyfile(source, target)
        except OSError as e:
            logger.error("Could not copy the CasparCG template to %s: %s", folder, e)
            QMessageBox.warning(
                self.page,
                QCoreApplication.translate("TcpOutput", "The template wasn't copied"),
                QCoreApplication.translate(
                    "TcpOutput", "Could not copy {file} to {folder}: {error}"
                ).format(file=CASPARCG_TEMPLATE_FILE, folder=folder, error=e),
            )
            return
        logger.info("Copied the CasparCG template to %s", target)
        button = self.ui.pushButton_tcpCopyTemplate
        QToolTip.showText(
            button.mapToGlobal(QPoint(0, button.height())),
            QCoreApplication.translate("TcpOutput", "Copied to {path}").format(
                path=str(target)
            ),
            button,
        )

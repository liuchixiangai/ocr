"""The HTTP API output page: its sending options and the presets.

MainWindow binds the URL, the method and the encoding (and the on/off
checkbox) to their settings. This handler binds the other options (Only
changes, Resend all, Fields per request, Rate, safe names, Headers), applies a
preset when one is picked in the Preset list, and shows the names the fields
are sent with. api_output reads every setting through its subscriptions.

The Preset list shows the preset whose settings are in use (with any address
and login), or Custom once a setting differs: picking a preset only fills the
settings in, and every one of them can still be changed.
"""

from collections.abc import Iterable

from PySide6.QtCore import QCoreApplication, Qt

import api_output
from storage import fetch_data, store_data

SETTINGS = "scoresight.json"

# The values api_output understands, in the order of comboBox_api_encode and
# comboBox_outApiMethod. The settings store these, not the (translated) item text.
OUT_API_ENCODINGS = api_output.ENCODINGS
OUT_API_METHODS = ("POST", "PUT", "GET")
# the items of comboBox_outApiPreset: Custom, then the presets
PRESET_KEYS = (None, "tricaster", "pearl", "guangwei_website")


def preset_help(key: str | None) -> str:
    """What to do after picking a preset, and how its names work."""
    if key == "tricaster":
        return QCoreApplication.translate(
            "MainWindow",
            "Replace TRICASTER-IP with the TriCaster's address (and admin:admin with "
            "its login). Each field is a DataLink key named like the field: put "
            "%Home Score% in a title to show the Home Score field.",
        )
    if key == "pearl":
        return QCoreApplication.translate(
            "MainWindow",
            "Replace PEARL-IP with the Pearl's address and PASSWORD with its admin "
            "password. Each field is a Pearl variable with the name shown below: "
            "put %(home_score) in a layout's text to show the Home Score field.",
        )
    if key == "guangwei_website":
        return QCoreApplication.translate(
            "MainWindow",
            "【预留/未启用】把 YOUR-WEBSITE-HOST 换成网站真实地址，并填写鉴权头。"
            "在未拿到网站接口前请保持「启用 HTTP API」关闭，占位地址不会发包。"
            "字段以 JSON 全文推送（见 cn-edition/docs/网站对接预留接口.md）。",
        )
    return ""


def names_text(key: str | None, names: dict[str, str], safe: bool) -> str:
    """The names the fields are sent with, as the preset's product shows them
    ("%Home Score%", "%(home_score)"), or "" when they are the field names."""
    if not names:
        return ""
    if key == "tricaster":
        shown = ", ".join(f"%{sent}%" for sent in names.values())
        return QCoreApplication.translate(
            "MainWindow", "DataLink keys: {names}"
        ).format(names=shown)
    if key == "pearl":
        shown = ", ".join(f"%({sent})" for sent in names.values())
        return QCoreApplication.translate(
            "MainWindow", "Pearl variables: {names}"
        ).format(names=shown)
    if not safe:
        return ""
    shown = ", ".join(
        sent if sent == name else f"{sent} ({name})" for name, sent in names.items()
    )
    return QCoreApplication.translate("MainWindow", "Sent as: {names}").format(
        names=shown
    )


class APIUIHandler:
    def __init__(self, ui):
        self.ui = ui
        self.boxNames: list[str] = []
        self._applying = False
        # box names are shown as they are, never as rich text
        for label in (ui.label_outApiPresetHelp, ui.label_outApiNamesSent):
            label.setTextFormat(Qt.TextFormat.PlainText)

        self._bindCheckBox(ui.checkBox_outApiOnlyChanges, "out_api_only_changes")
        self._bindCheckBox(ui.checkBox_outApiSafeNames, "out_api_safe_names")
        self._bindSpinBox(ui.spinBox_outApiRefresh, "out_api_refresh_s")
        self._bindSpinBox(
            ui.spinBox_outApiFieldsPerRequest, "out_api_fields_per_request"
        )
        self._bindSpinBox(ui.spinBox_outApiMaxRate, "out_api_max_rate")

        headers = ui.plainTextEdit_outApiHeaders
        headers.setPlainText(fetch_data(SETTINGS, "out_api_headers", "") or "")
        headers.textChanged.connect(self._headersChanged)

        # the settings MainWindow binds: follow them for the Preset list
        ui.lineEdit_api_url.textChanged.connect(self.refresh)
        ui.comboBox_outApiMethod.currentIndexChanged.connect(self.refresh)
        ui.comboBox_api_encode.currentIndexChanged.connect(self.refresh)

        # activated: picked by the user (not set by refresh())
        ui.comboBox_outApiPreset.activated.connect(self.presetPicked)
        self.ensure_guangwei_preset_item()
        self.restoreUiState()

    # --- bindings -------------------------------------------------------------

    def _bindCheckBox(self, checkbox, setting: str):
        checkbox.setChecked(bool(fetch_data(SETTINGS, setting, False)))
        checkbox.toggled.connect(lambda value: self._store(setting, bool(value)))

    def _bindSpinBox(self, spinbox, setting: str):
        stored = fetch_data(SETTINGS, setting, 0)
        try:
            spinbox.setValue(int(stored or 0))
        except (TypeError, ValueError):
            spinbox.setValue(0)
        spinbox.valueChanged.connect(lambda value: self._store(setting, int(value)))

    def _headersChanged(self):
        self._store(
            "out_api_headers", self.ui.plainTextEdit_outApiHeaders.toPlainText()
        )

    def _store(self, setting: str, value):
        if fetch_data(SETTINGS, setting) != value:
            store_data(SETTINGS, setting, value)
        self.refresh()

    # --- presets ----------------------------------------------------------------

    def settings(self) -> api_output.ApiSettings:
        """The settings the page shows."""
        ui = self.ui
        method_index = ui.comboBox_outApiMethod.currentIndex()
        encoding_index = ui.comboBox_api_encode.currentIndex()
        return api_output.ApiSettings(
            url=ui.lineEdit_api_url.text(),
            method=OUT_API_METHODS[method_index]
            if 0 <= method_index < len(OUT_API_METHODS)
            else "POST",
            encoding=OUT_API_ENCODINGS[encoding_index]
            if 0 <= encoding_index < len(OUT_API_ENCODINGS)
            else OUT_API_ENCODINGS[0],
            fields_per_request=ui.spinBox_outApiFieldsPerRequest.value(),
            only_changes=ui.checkBox_outApiOnlyChanges.isChecked(),
            max_rate=ui.spinBox_outApiMaxRate.value(),
            refresh_s=ui.spinBox_outApiRefresh.value(),
            safe_names=ui.checkBox_outApiSafeNames.isChecked(),
        )

    def presetPicked(self, index: int):
        if 0 <= index < len(PRESET_KEYS) and PRESET_KEYS[index] is not None:
            self.applyPreset(PRESET_KEYS[index])
        else:
            self.refresh()

    def applyPreset(self, key: str):
        """Fill in the settings of preset key (keeping the URL's address and
        login, if it has one)."""
        preset = api_output.PRESETS[key]
        ui = self.ui
        self._applying = True
        try:
            ui.lineEdit_api_url.setText(preset.url(ui.lineEdit_api_url.text()))
            ui.comboBox_outApiMethod.setCurrentIndex(
                OUT_API_METHODS.index(preset.method)
            )
            ui.comboBox_api_encode.setCurrentIndex(
                OUT_API_ENCODINGS.index(preset.encoding)
            )
            ui.spinBox_outApiFieldsPerRequest.setValue(preset.fields_per_request)
            ui.checkBox_outApiOnlyChanges.setChecked(preset.only_changes)
            ui.spinBox_outApiMaxRate.setValue(preset.max_rate)
            ui.spinBox_outApiRefresh.setValue(preset.refresh_s)
            ui.checkBox_outApiSafeNames.setChecked(preset.safe_names)
        finally:
            self._applying = False
        self.refresh()

    def setBoxNames(self, names: Iterable[str]):
        """The fields there are (for the names they are sent with)."""
        self.boxNames = list(names)
        self.refresh()

    def refresh(self, *_):
        """Show the preset in use, its help, and the names sent."""
        if self._applying:
            return
        settings = self.settings()
        key = api_output.matching_preset(settings)
        combo = self.ui.comboBox_outApiPreset
        combo.blockSignals(True)
        combo.setCurrentIndex(PRESET_KEYS.index(key) if key in PRESET_KEYS else 0)
        combo.blockSignals(False)

        help_label = self.ui.label_outApiPresetHelp
        help_text = preset_help(key)
        help_label.setText(help_text)
        help_label.setVisible(bool(help_text))

        names = api_output.sent_names(self.boxNames, settings.safe_names)
        names_label = self.ui.label_outApiNamesSent
        text = names_text(key, names, settings.safe_names)
        names_label.setText(text)
        names_label.setVisible(bool(text))

    def fitWidth(self):
        """The page scrolls vertically only: make it as wide as its content
        needs (which depends on the language), as it was without scrolling."""
        area = getattr(self.ui, "scrollArea_api", None)
        if area is None:
            return
        content = area.widget()
        content.adjustSize()
        width = (
            content.minimumSizeHint().width()
            + area.verticalScrollBar().sizeHint().width()
            + 2 * area.frameWidth()
        )
        area.setMinimumWidth(width)

    def ensure_guangwei_preset_item(self):
        """Add「光为网站（预留）」to the Preset combo if the .ui only has 3 items."""
        combo = self.ui.comboBox_outApiPreset
        label = "光为网站（预留）"
        if combo.count() < len(PRESET_KEYS):
            combo.addItem(label)
        else:
            idx = PRESET_KEYS.index("guangwei_website")
            if 0 <= idx < combo.count():
                combo.setItemText(idx, label)

    def restoreUiState(self):
        """Show the preset and the names again, e.g. after retranslateUi()."""
        self.refresh()
        self.fitWidth()

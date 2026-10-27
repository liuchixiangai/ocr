"""The Box tab of the inspector: the settings of the selected field.

stackedWidget_boxSettings shows a short explanation while no field is
selected, and the field's settings page otherwise. What the page shows depends
on the field (MainWindow.listItemClicked calls showField):
- a box on the picture: its reading settings (groupBox_target_settings) in
  three sections: Reading, Filtering and Image cleanup. Image cleanup is
  collapsed unless the user opened it (remembered as "image_cleanup_expanded")
  and says how many of its settings differ from the defaults. Its settings
  prepare the box's image for Tesseract: while a PP-OCR model reads the boxes
  (setPpocr) they are disabled, and kept, with a note, except for a dot
  counter, which uses them with any engine;
- a field that isn't on the picture: how to place it;
- a template field: its template, with Insert field to add another field.
Only custom fields can be templates, and a field on the picture can't become
one (it would be both).

The Format preset list shows the preset whose expression is in the Format
field ("Custom" for any other), and an expression Python can't compile (which
the OCR ignores) is shown in red.
"""

from PySide6.QtWidgets import QSpinBox, QComboBox, QLabel


import re
from functools import partial

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from PySide6.QtWidgets import QFormLayout, QMenu

import theme
from defaults import (
    CLEAR_VALUES,
    FieldType,
    default_info_for_box_name,
    format_rejects_cjk,
    normalize_settings_dict,
    format_prefixes,
)
from storage import TextDetectionTargetMemoryStorage, fetch_data, store_data
from ui_mainwindow import Ui_MainWindow
from sc_logging import logger

# a field's placement (the UserRole of its name in the boxes list)
PLACED = "checked"
NOT_PLACED = "unchecked"
TEMPLATE = "templatefield"

# the last item of comboBox_formatPrefix: an expression that is no preset
CUSTOM_PRESET_INDEX = len(format_prefixes) - 1

# the settings in the Image cleanup section (its badge counts the changed ones)
IMAGE_CLEANUP_SETTINGS = (
    "binarization_method",
    "cleanup_thresh",
    "vscale",
    "skew",
    "dilate",
    "invert_patch",
    "autocrop",
    "rescale_patch",
    "normalize_wh_ratio",
    "composite_box",
)
# whether the Image cleanup section is open
IMAGE_CLEANUP_SETTING = "image_cleanup_expanded"
# the label of the first Image cleanup row that isn't Tesseract preprocessing:
# the rows above it are disabled while a PP-OCR model reads the boxes
IMAGE_CLEANUP_LEGACY_LABEL = "label_legacy"


def _off():
    return QCoreApplication.translate("BoxSettings", "Off")


def _percent(value) -> str:
    return QCoreApplication.translate("BoxSettings", "{value} %").format(value=value)


def slider_readout(name: str, value: int) -> str:
    """The text shown next to a box settings slider for its value."""
    if name == "cleanup":
        return _off() if value == 0 else _percent(value)
    if name == "conf_thresh":
        # the OCR model's confidence in %
        return _off() if value == 0 else _percent(value)
    if name == "vscale":
        # value/10 of the height
        return _percent(value * 10)
    if name == "dilate":
        # passes of a 3x3 dilation
        return _off() if value == 0 else str(value)
    if name == "skew":
        if value == 0:
            return "0"
        return f"+{value}" if value > 0 else f"−{-value}"
    return str(value)


def matching_preset(regex: str) -> int:
    """The index (in comboBox_formatPrefix) of the format preset whose
    expression is regex, or CUSTOM_PRESET_INDEX."""
    for index, preset in enumerate(format_prefixes[:CUSTOM_PRESET_INDEX]):
        if preset == regex:
            return index
    return CUSTOM_PRESET_INDEX


def regex_error(pattern: str) -> str | None:
    """Why a format can't be used (the OCR then ignores it), or None."""
    try:
        re.compile(pattern)
    except re.error as error:
        return str(error)
    return None


def image_cleanup_engine_note() -> str:
    return QCoreApplication.translate(
        "BoxSettings",
        "Image cleanup is for the Tesseract models; PP-OCR reads the picture as it is.",
    )


def _layout_widgets(item) -> list:
    """The widgets of a layout item (a widget, or every widget of a layout)."""
    if item is None:
        return []
    widget = item.widget()
    if widget is not None:
        return [widget]
    layout = item.layout()
    if layout is None:
        return []
    widgets = []
    for index in range(layout.count()):
        widgets += _layout_widgets(layout.itemAt(index))
    return widgets


def tesseract_cleanup_widgets(ui) -> list:
    """The Image cleanup controls that prepare a box's image for Tesseract
    (black & white, specks, squash, slant, thicken, invert, crop, resize,
    digit width): the form's rows above "Experimental / legacy"."""
    layout = ui.formLayout_imageCleanup
    legacy = getattr(ui, IMAGE_CLEANUP_LEGACY_LABEL)
    widgets = []
    for row in range(layout.rowCount()):
        row_widgets = []
        for role in (
            QFormLayout.ItemRole.LabelRole,
            QFormLayout.ItemRole.FieldRole,
            QFormLayout.ItemRole.SpanningRole,
        ):
            row_widgets += _layout_widgets(layout.itemAt(row, role))
        if legacy in row_widgets:
            break
        widgets += row_widgets
    return widgets


def changed_settings(settings: dict | None, name: str, keys=None) -> list[str]:
    """The settings (of the given keys, or all) of the box name that differ
    from the defaults for that field."""
    info = default_info_for_box_name(name)
    defaults = normalize_settings_dict({}, info)
    current = normalize_settings_dict(settings, info)
    return [
        key
        for key in (defaults if keys is None else keys)
        if current[key] != defaults[key]
    ]


class BoxSettingsUIHandler:
    def __init__(self, ui: Ui_MainWindow):
        self.ui = ui
        self.detectionTargetsStorage = TextDetectionTargetMemoryStorage()
        # the field shown: its name (None: none selected), placement (PLACED,
        # NOT_PLACED or TEMPLATE) and whether it is a custom field
        self.fieldName: str | None = None
        self.fieldRole: str | None = None
        self.fieldCustom = False
        # a PP-OCR model reads the boxes: the Tesseract Image cleanup settings
        # don't apply (except to dot counters)
        self.ppocr = False
        self.boxSettingsUiSetup()

    def readoutSliders(self):
        """(setting name, slider, value label) of the sliders with a readout."""
        ui = self.ui
        return (
            ("cleanup", ui.horizontalSlider_cleanup, ui.label_cleanupValue),
            ("conf_thresh", ui.horizontalSlider_conf_thresh, ui.label_confThreshValue),
            ("vscale", ui.horizontalSlider_vscale, ui.label_vscaleValue),
            ("dilate", ui.horizontalSlider_dilate, ui.label_dilateValue),
            ("skew", ui.horizontalSlider_skew, ui.label_skewValue),
        )

    def settingsWidgets(self):
        """The controls populateSettings sets (with their signals blocked)."""
        ui = self.ui
        return (
            ui.lineEdit_format,
            ui.comboBox_fieldType,
            ui.checkBox_smoothing,
            ui.comboBox_clearValue,
            ui.horizontalSlider_conf_thresh,
            ui.checkBox_autocrop,
            ui.checkBox_skip_similar_image,
            ui.horizontalSlider_cleanup,
            ui.horizontalSlider_dilate,
            ui.horizontalSlider_skew,
            ui.horizontalSlider_vscale,
            ui.checkBox_removeLeadingZeros,
            ui.checkBox_rescalePatch,
            ui.checkBox_normWHRatio,
            ui.checkBox_invertPatch,
            ui.checkBox_dotDetector,
            ui.checkBox_ordinalIndicator,
            ui.comboBox_binarizationMethod,
            ui.comboBox_formatPrefix,
            ui.checkBox_templatefield,
            ui.lineEdit_templatefield,
            ui.checkBox_compositeBox,
        )

    def updateReadouts(self, *_):
        """Show each slider's value next to it ("Off" when the setting is off),
        also to screen readers."""
        for name, slider, label in self.readoutSliders():
            text = slider_readout(name, slider.value())
            label.setText(text)
            metrics = QFontMetrics(label.font())
            widest = max(
                metrics.horizontalAdvance(slider_readout(name, value))
                for value in (slider.minimum(), slider.maximum(), 0, 1)
            )
            label.setMinimumWidth(widest + 2)
            slider.setAccessibleDescription(text)

    def editSettings(self, settingsMutatorCallback):
        # update the settings of the field shown in the detectionTargetsStorage
        item_name = self.fieldName
        if item_name is None:
            logger.info("no item selected")
            return
        item_obj = self.detectionTargetsStorage.find_item_by_name(item_name)
        if item_obj is None:
            logger.info("item not found: %s", item_name)
            return
        item_obj = settingsMutatorCallback(item_obj)
        self.detectionTargetsStorage.edit_item(item_name, item_obj)

    def restoreDefaults(self):
        # restore the default settings for the selected item
        def restoreDefaultsSettings(item_obj):
            info = default_info_for_box_name(item_obj.name)
            item_obj.settings = normalize_settings_dict({}, info)
            return item_obj

        if self.fieldName is None:
            return
        self.editSettings(restoreDefaultsSettings)
        self.populateSettings(self.fieldName)

    def confThreshChanged(self):
        self.genericSettingsChanged(
            "conf_thresh", float(self.ui.horizontalSlider_conf_thresh.value()) / 100.0
        )

    def cleanupThreshChanged(self):
        self.genericSettingsChanged(
            "cleanup_thresh", float(self.ui.horizontalSlider_cleanup.value()) / 100.0
        )

    def clearValueChanged(self, index):
        if 0 <= index < len(CLEAR_VALUES):
            self.genericSettingsChanged("clear_value", CLEAR_VALUES[index])

    def fieldTypeChanged(self, index):
        """Switching to 文本/队名 must not keep a digit format or the score debounce."""
        self.genericSettingsChanged("type", index)
        try:
            text_type = int(index) == int(FieldType.TEXT)
        except (TypeError, ValueError):
            return
        if not text_type:
            return
        regex = self.ui.lineEdit_format.text()
        if format_rejects_cjk(regex):
            any_text = 10
            if self.ui.comboBox_formatPrefix.currentIndex() != any_text:
                self.ui.comboBox_formatPrefix.setCurrentIndex(any_text)
            else:
                self.ui.lineEdit_format.setText(format_prefixes[any_text])
        stack = getattr(self.ui, "spinBox_led_stack_frames", None)
        stable = getattr(self.ui, "spinBox_led_stable_ms", None)
        if stack is not None and stack.value() != 0:
            stack.setValue(0)
        if stable is not None and stable.value() != 0:
            stable.setValue(0)

    def formatPrefixChanged(self, index):
        if index == CUSTOM_PRESET_INDEX:
            # "Custom": type an expression
            self.ui.lineEdit_format.setFocus()
            self.ui.lineEdit_format.selectAll()
            return
        # the preset's expression (textChanged stores it)
        self.ui.lineEdit_format.setText(format_prefixes[index])

    def genericSettingsChanged(self, settingName, value):
        def editGenericSettings(item_obj):
            item_obj.settings[settingName] = value
            return item_obj

        self.editSettings(editGenericSettings)
        if settingName in IMAGE_CLEANUP_SETTINGS:
            self.updateBadge()

    def boxSettingsUiSetup(self):
        self.ui.pushButton_restoreDefaults.clicked.connect(self.restoreDefaults)
        self.ui.checkBox_smoothing.toggled.connect(
            partial(self.genericSettingsChanged, "smoothing")
        )
        self.ensureLedWidgets()
        self.ui.comboBox_clearValue.currentIndexChanged.connect(self.clearValueChanged)
        self.ui.horizontalSlider_conf_thresh.valueChanged.connect(
            self.confThreshChanged
        )
        self.ui.lineEdit_format.textChanged.connect(
            partial(self.genericSettingsChanged, "format_regex")
        )
        self.ui.lineEdit_format.textChanged.connect(self.updateFormatState)
        self.ui.comboBox_fieldType.currentIndexChanged.connect(self.fieldTypeChanged)
        self.ui.checkBox_skip_similar_image.toggled.connect(
            partial(self.genericSettingsChanged, "skip_similar_image")
        )
        self.ui.checkBox_autocrop.toggled.connect(
            partial(self.genericSettingsChanged, "autocrop")
        )
        self.ui.horizontalSlider_cleanup.valueChanged.connect(self.cleanupThreshChanged)
        self.ui.horizontalSlider_dilate.valueChanged.connect(
            partial(self.genericSettingsChanged, "dilate")
        )
        self.ui.horizontalSlider_skew.valueChanged.connect(
            partial(self.genericSettingsChanged, "skew")
        )
        self.ui.horizontalSlider_vscale.valueChanged.connect(
            partial(self.genericSettingsChanged, "vscale")
        )
        self.ui.checkBox_removeLeadingZeros.toggled.connect(
            partial(self.genericSettingsChanged, "remove_leading_zeros")
        )
        self.ui.checkBox_rescalePatch.toggled.connect(
            partial(self.genericSettingsChanged, "rescale_patch")
        )
        self.ui.checkBox_normWHRatio.toggled.connect(
            partial(self.genericSettingsChanged, "normalize_wh_ratio")
        )
        self.ui.checkBox_invertPatch.toggled.connect(
            partial(self.genericSettingsChanged, "invert_patch")
        )
        self.ui.checkBox_dotDetector.toggled.connect(
            partial(self.genericSettingsChanged, "dot_detector")
        )
        # a dot counter uses the Image cleanup settings with any engine
        self.ui.checkBox_dotDetector.toggled.connect(self.updateImageCleanupEngine)
        self.ui.checkBox_ordinalIndicator.toggled.connect(
            partial(self.genericSettingsChanged, "ordinal_indicator")
        )
        self.ui.comboBox_binarizationMethod.currentIndexChanged.connect(
            partial(self.genericSettingsChanged, "binarization_method")
        )
        self.ui.lineEdit_templatefield.textChanged.connect(
            partial(self.genericSettingsChanged, "templatefield_text")
        )
        self.ui.checkBox_compositeBox.toggled.connect(
            partial(self.genericSettingsChanged, "composite_box")
        )
        self.ui.comboBox_formatPrefix.currentIndexChanged.connect(
            self.formatPrefixChanged
        )
        for _, slider, _ in self.readoutSliders():
            slider.valueChanged.connect(self.updateReadouts)
        self.updateReadouts()

        ui = self.ui
        # the expression in a fixed-width font: brackets and dots stand out
        fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font = QFont(ui.lineEdit_format.font())
        font.setFamilies(["Cascadia Mono", "Consolas", "Menlo", fixed.family()])
        font.setStyleHint(QFont.StyleHint.Monospace)
        ui.lineEdit_format.setFont(font)

        # Image cleanup: collapsed unless the user opened it
        ui.toolButton_imageCleanup.toggled.connect(self.setImageCleanupExpanded)
        self.setImageCleanupExpanded(
            fetch_data("scoresight.json", IMAGE_CLEANUP_SETTING, False) is True,
            store=False,
        )
        # a shortcut to the view tools' Binary View (they stay in sync)
        ui.toolButton_cleanupBinary.clicked.connect(self.toggleBinaryView)
        ui.pushButton_binary.toggled.connect(ui.toolButton_cleanupBinary.setChecked)
        # Insert field: the fields, as {{Name}}
        self.insertFieldMenu = QMenu(ui.toolButton_insertField)
        self.insertFieldMenu.aboutToShow.connect(self.fillInsertFieldMenu)
        ui.toolButton_insertField.setMenu(self.insertFieldMenu)

        self.showField(None)

    # --- the field shown ------------------------------------------------------------

    def showField(self, name: str | None, role: str | None = None, custom=False):
        """Show the settings of the field name (None: the empty page)."""
        ui = self.ui
        self.fieldName = name
        self.fieldRole = role
        self.fieldCustom = bool(custom)
        if name is None:
            ui.stackedWidget_boxSettings.setCurrentWidget(ui.page_boxEmpty)
            ui.groupBox_target_settings.setEnabled(False)
            ui.label_selectedInfo.setText("")
            self.populateSettings("")
            return
        placed = role == PLACED
        template = role == TEMPLATE
        ui.stackedWidget_boxSettings.setCurrentWidget(ui.page_boxSettings)
        ui.label_selectedInfo.setText(name)
        # the name may be cut off in a narrow inspector
        ui.label_selectedInfo.setToolTip(name)
        # the reading settings only exist for a box on the picture
        ui.groupBox_target_settings.setVisible(placed)
        ui.groupBox_target_settings.setEnabled(placed)
        ui.pushButton_restoreDefaults.setEnabled(placed)
        # the template: custom fields only, and not one on the picture
        ui.label_sectionTemplate.setVisible(self.fieldCustom)
        ui.checkBox_templatefield.setVisible(self.fieldCustom)
        ui.checkBox_templatefield.setEnabled(self.fieldCustom and not placed)
        for widget in (ui.lineEdit_templatefield, ui.toolButton_insertField):
            widget.setVisible(self.fieldCustom)
            widget.setEnabled(self.fieldCustom and template)
        self.populateSettings(name)
        self.updateNotes()

    def updateNotes(self):
        """The notes of the settings page, for the field shown (in the current
        language)."""
        if self.fieldName is None:
            return
        ui = self.ui
        if self.fieldRole == TEMPLATE:
            note = QCoreApplication.translate(
                "BoxSettings",
                "This field is built from other fields (see Template): it has no "
                "box on the picture and no reading settings.",
            )
        elif self.fieldRole != PLACED:
            note = QCoreApplication.translate(
                "BoxSettings",
                "This field isn't on the picture yet. Drag over its numbers on "
                "the picture, or double-click the field in the list.",
            )
        else:
            note = ""
        ui.label_boxNote.setText(note)
        ui.label_boxNote.setVisible(bool(note))
        if not self.fieldCustom:
            hint = QCoreApplication.translate(
                "BoxSettings",
                "Template: only custom fields can be built from other fields.",
            )
        elif self.fieldRole == PLACED:
            hint = QCoreApplication.translate(
                "BoxSettings",
                "To build this field from other fields instead, remove it from "
                "the picture first.",
            )
        else:
            hint = QCoreApplication.translate(
                "BoxSettings",
                "Each field in double braces is replaced by its value. Insert "
                "field adds one, spelled exactly as the template needs it.",
            )
        ui.label_templateHint.setText(hint)

    def retranslate(self):
        """Set the texts made at runtime again (after a language change)."""
        self.updateNotes()
        self.updateFormatState()
        self.updateBadge()
        self.updateReadouts()
        self.updateImageCleanupEngine()

    # --- the OCR engine: Image cleanup is Tesseract's -------------------------------

    def setPpocr(self, ppocr: bool):
        """A PP-OCR model (True) or a Tesseract model reads the boxes."""
        self.ppocr = bool(ppocr)
        self.updateImageCleanupEngine()

    def updateImageCleanupEngine(self, *_):
        """With PP-OCR, the Tesseract preprocessing settings of Image cleanup
        are disabled (and kept) with a note, except for a dot counter, which
        counts the lights in the cleaned-up image whatever the engine."""
        ui = self.ui
        disabled = self.ppocr and not ui.checkBox_dotDetector.isChecked()
        for widget in tesseract_cleanup_widgets(ui):
            widget.setEnabled(not disabled)
        note = ui.label_imageCleanupEngineNote
        note.setText(image_cleanup_engine_note())
        note.setVisible(disabled)
        ui.toolButton_imageCleanup.setToolTip(
            QCoreApplication.translate(
                "MainWindow",
                "Show or hide the settings that clean up the box's image before a "
                "Tesseract model reads it. Binary view shows the result.",
            )
            + ("\n" + image_cleanup_engine_note() if disabled else "")
        )

    def populateSettings(self, name):
        widgets = self.settingsWidgets()
        for widget in widgets:
            widget.blockSignals(True)

        ui = self.ui
        # populate the settings from the detectionTargetsStorage
        item_obj = self.detectionTargetsStorage.find_item_by_name(name)
        if item_obj is None:
            # no box (yet): the defaults for the field
            settings = normalize_settings_dict({}, default_info_for_box_name(name))
        else:
            item_obj.settings = normalize_settings_dict(
                item_obj.settings, default_info_for_box_name(item_obj.name)
            )
            settings = item_obj.settings
        ui.lineEdit_format.setText(settings["format_regex"])
        ui.comboBox_fieldType.setCurrentIndex(settings["type"])
        ui.checkBox_smoothing.setChecked(settings["smoothing"])
        self.ensureLedWidgets()
        ui.spinBox_led_stack_frames.blockSignals(True)
        ui.comboBox_led_stack_mode.blockSignals(True)
        ui.spinBox_led_stable_ms.blockSignals(True)
        ui.spinBox_led_stack_frames.setValue(int(settings.get("led_stack_frames") or 0))
        mode = settings.get("led_stack_mode") or "max"
        ui.comboBox_led_stack_mode.setCurrentIndex(0 if mode != "median" else 1)
        ui.spinBox_led_stable_ms.setValue(int(settings.get("led_stable_ms") or 0))
        ui.spinBox_led_stack_frames.blockSignals(False)
        ui.comboBox_led_stack_mode.blockSignals(False)
        ui.spinBox_led_stable_ms.blockSignals(False)
        ui.comboBox_clearValue.setCurrentIndex(
            CLEAR_VALUES.index(settings["clear_value"])
        )
        # stored as a fraction of the OCR's confidence %; a value above 1
        # (hand-edited) is already a percentage, as in tesseract.py
        conf_thresh = float(settings["conf_thresh"])
        ui.horizontalSlider_conf_thresh.setValue(
            round(conf_thresh if conf_thresh > 1 else conf_thresh * 100)
        )
        ui.checkBox_autocrop.setChecked(settings["autocrop"])
        ui.checkBox_skip_similar_image.setChecked(settings["skip_similar_image"])
        ui.horizontalSlider_cleanup.setValue(int(settings["cleanup_thresh"] * 100))
        ui.horizontalSlider_dilate.setValue(settings["dilate"])
        ui.horizontalSlider_skew.setValue(settings["skew"])
        ui.horizontalSlider_vscale.setValue(settings["vscale"])
        ui.checkBox_removeLeadingZeros.setChecked(settings["remove_leading_zeros"])
        ui.checkBox_rescalePatch.setChecked(settings["rescale_patch"])
        ui.checkBox_normWHRatio.setChecked(settings["normalize_wh_ratio"])
        ui.checkBox_invertPatch.setChecked(settings["invert_patch"])
        ui.checkBox_dotDetector.setChecked(settings["dot_detector"])
        ui.checkBox_ordinalIndicator.setChecked(settings["ordinal_indicator"])
        ui.comboBox_binarizationMethod.setCurrentIndex(settings["binarization_method"])
        ui.checkBox_templatefield.setChecked(settings["templatefield"])
        ui.lineEdit_templatefield.setText(settings["templatefield_text"])
        ui.checkBox_compositeBox.setChecked(settings["composite_box"])

        for widget in widgets:
            widget.blockSignals(False)
        # the values were set with the signals blocked
        self.updateFormatState()
        self.updateBadge()
        self.updateReadouts()
        self.updateImageCleanupEngine()

    # --- Format: the matching preset, an invalid expression ------------------------

    def updateFormatState(self, *_):
        """Show the preset that matches the expression ("Custom" for others),
        and mark an expression the OCR can't use."""
        ui = self.ui
        text = ui.lineEdit_format.text()
        combo = ui.comboBox_formatPrefix
        preset = matching_preset(text)
        if combo.currentIndex() != preset:
            combo.blockSignals(True)
            combo.setCurrentIndex(preset)
            combo.blockSignals(False)
        error = regex_error(text)
        status = ui.label_formatStatus
        if error is None:
            ui.lineEdit_format.setStyleSheet("")
            ui.lineEdit_format.setAccessibleDescription("")
            status.setText("")
            status.setToolTip("")
            status.hide()
            return
        red = theme.color("empty", ui.lineEdit_format.palette()).name()
        ui.lineEdit_format.setStyleSheet(
            f"QLineEdit#lineEdit_format {{ border: 2px solid {red};"
            " border-radius: 3px; padding: 1px 2px; }"
        )
        message = QCoreApplication.translate(
            "BoxSettings",
            "Not a valid expression, so it is ignored: every reading passes.",
        )
        status.setText(message)
        status.setStyleSheet(f"QLabel#label_formatStatus {{ color: {red}; }}")
        # Python's explanation, e.g. "missing ), unterminated subpattern"
        status.setToolTip(error)
        ui.lineEdit_format.setAccessibleDescription(message)
        status.show()

    # --- Image cleanup: collapsed, the changed settings ----------------------------

    def setImageCleanupExpanded(self, expanded: bool, store: bool = True):
        ui = self.ui
        expanded = bool(expanded)
        ui.frame_imageCleanup.setVisible(expanded)
        ui.toolButton_imageCleanup.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        if ui.toolButton_imageCleanup.isChecked() != expanded:
            ui.toolButton_imageCleanup.blockSignals(True)
            ui.toolButton_imageCleanup.setChecked(expanded)
            ui.toolButton_imageCleanup.blockSignals(False)
        if store and fetch_data("scoresight.json", IMAGE_CLEANUP_SETTING) != expanded:
            store_data("scoresight.json", IMAGE_CLEANUP_SETTING, expanded)

    def updateBadge(self):
        """How many of the Image cleanup settings of the box shown differ from
        the defaults."""
        box = (
            self.detectionTargetsStorage.find_item_by_name(self.fieldName)
            if self.fieldName
            else None
        )
        changed = (
            len(changed_settings(box.settings, box.name, IMAGE_CLEANUP_SETTINGS))
            if box is not None
            else 0
        )
        if changed:
            text = QCoreApplication.translate(
                "BoxSettings", "%n changed", None, changed
            )
        else:
            text = QCoreApplication.translate("BoxSettings", "all default")
        self.ui.label_imageCleanupBadge.setText(text)
        self.ui.toolButton_imageCleanup.setAccessibleDescription(text)

    def toggleBinaryView(self):
        # through the view tools' button: it does nothing while they are
        # disabled (no source yet)
        self.ui.pushButton_binary.click()
        self.ui.toolButton_cleanupBinary.setChecked(
            self.ui.pushButton_binary.isChecked()
        )

    # --- Template: Insert field ------------------------------------------------------

    def fieldNames(self) -> list[tuple[str, str]]:
        """(name, placement) of every field, in the order of the boxes list."""
        table = self.ui.tableWidget_boxes
        fields = []
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None:
                fields.append((item.text(), item.data(Qt.ItemDataRole.UserRole)))
        return fields

    def fillInsertFieldMenu(self):
        menu = self.insertFieldMenu
        menu.clear()
        for name, role in self.fieldNames():
            if name == self.fieldName:
                continue
            text = name.replace("&", "&&")
            if role not in (PLACED, TEMPLATE):
                # its placeholder stays as it is until the field is placed
                text = QCoreApplication.translate(
                    "BoxSettings", "{name} (not on the picture)"
                ).format(name=text)
            action = menu.addAction(text)
            action.triggered.connect(partial(self.insertField, name))
        if menu.isEmpty():
            menu.addAction(
                QCoreApplication.translate("BoxSettings", "No other fields")
            ).setEnabled(False)

    def insertField(self, name: str):
        """Insert {{name}} at the cursor of the template (textChanged stores it)."""
        line_edit = self.ui.lineEdit_templatefield
        line_edit.insert("{{" + name + "}}")
        line_edit.setFocus()

    def ensureLedWidgets(self):
        """Create GuangWei OCR LED controls under Filtering if the .ui lacks them."""
        ui = self.ui
        if getattr(ui, "spinBox_led_stack_frames", None) is not None:
            return
        form = ui.formLayout_filtering
        ui.label_led_stack = QLabel("LED 多帧融合 (0=关)", ui.groupBox_target_settings)
        ui.spinBox_led_stack_frames = QSpinBox(ui.groupBox_target_settings)
        ui.spinBox_led_stack_frames.setRange(0, 15)
        ui.spinBox_led_stack_frames.setToolTip(
            "把最近 N 帧框内图像按最大值/中位数合成后再识别，减轻 LED 扫描残影。建议比分框 5。0 关闭。"
        )
        ui.spinBox_led_stack_frames.valueChanged.connect(self.ledStackFramesChanged)
        form.addRow(ui.label_led_stack, ui.spinBox_led_stack_frames)

        ui.label_led_mode = QLabel("融合方式", ui.groupBox_target_settings)
        ui.comboBox_led_stack_mode = QComboBox(ui.groupBox_target_settings)
        ui.comboBox_led_stack_mode.addItem("最大值 (推荐 LED)", "max")
        ui.comboBox_led_stack_mode.addItem("中位数", "median")
        ui.comboBox_led_stack_mode.currentIndexChanged.connect(self.ledStackModeChanged)
        form.addRow(ui.label_led_mode, ui.comboBox_led_stack_mode)

        ui.label_led_stable = QLabel("稳定后再输出 (毫秒)", ui.groupBox_target_settings)
        ui.spinBox_led_stable_ms = QSpinBox(ui.groupBox_target_settings)
        ui.spinBox_led_stable_ms.setRange(0, 5000)
        ui.spinBox_led_stable_ms.setSingleStep(50)
        ui.spinBox_led_stable_ms.setToolTip(
            "同一数字连续稳定至少这么久才发给 vMix/JSON。建议比分 200，时钟 80。0 关闭。"
        )
        ui.spinBox_led_stable_ms.valueChanged.connect(self.ledStableMsChanged)
        form.addRow(ui.label_led_stable, ui.spinBox_led_stable_ms)

    def ledStackFramesChanged(self, value):
        self.genericSettingsChanged("led_stack_frames", int(value))

    def ledStackModeChanged(self, index):
        mode = self.ui.comboBox_led_stack_mode.itemData(index) or "max"
        self.genericSettingsChanged("led_stack_mode", mode)

    def ledStableMsChanged(self, value):
        self.genericSettingsChanged("led_stable_ms", int(value))

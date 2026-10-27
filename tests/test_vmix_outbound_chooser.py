"""The two Output-tab buttons: JSON → vMix Data Sources, and the vMix API."""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QCheckBox, QPushButton, QStackedWidget, QWidget

from vmix_outbound_guide import (
    API_PATH,
    JSON_PATH,
    OutboundPathChooser,
    focus_api_outbound,
    focus_json_outbound,
)


@pytest.mark.qt
def test_path_buttons_match_and_json_ticks_the_file(qtbot):
    root = QWidget()
    qtbot.addWidget(root)
    stack = QStackedWidget(root)
    files = QWidget()
    files.setObjectName("tab_textFiles")
    api = QWidget()
    api.setObjectName("tab_vmix")
    other = QWidget()
    other.setObjectName("tab_obs")
    stack.addWidget(files)
    stack.addWidget(api)
    stack.addWidget(other)
    stack.setCurrentWidget(api)
    checkbox = QCheckBox(root)
    scan = QPushButton(api)
    ui = SimpleNamespace(
        stackedWidget_outputs=stack,
        tab_textFiles=files,
        tab_vmix=api,
        checkBox_saveJson=checkbox,
        btn_scan_vmix_fields=scan,
    )
    guide = SimpleNamespace(_installed=False)

    chooser = OutboundPathChooser()
    qtbot.addWidget(chooser)
    chooser.apply_texts(True)
    assert chooser.btn_json.minimumHeight() == chooser.btn_api.minimumHeight()
    assert chooser.btn_json.minimumHeight() >= 56
    assert chooser.btn_json.font().bold() and chooser.btn_api.font().bold()
    assert chooser.btn_json.text() == "JSON → vMix 数据源\n推荐"
    assert chooser.btn_api.text() == "进阶：vMix API\n扫描标题字段"
    assert "background" in chooser.btn_json.styleSheet()
    assert "background" in chooser.btn_api.styleSheet()

    seen = []
    chooser.path_selected.connect(seen.append)
    chooser.btn_json.click()
    assert seen == [JSON_PATH]
    assert chooser.btn_json.isChecked()
    assert not chooser.btn_api.isChecked()
    focus_json_outbound(ui, guide)
    assert stack.currentWidget() is files
    assert checkbox.isChecked()

    checkbox.setChecked(False)
    chooser.btn_api.click()
    assert seen[-1] == API_PATH
    assert chooser.btn_api.isChecked()
    focus_api_outbound(ui, guide)
    assert stack.currentWidget() is api
    assert not checkbox.isChecked()

    chooser.sync_from_page(files)
    assert chooser.btn_json.isChecked() and not chooser.btn_api.isChecked()
    chooser.sync_from_page(other)
    assert not chooser.btn_json.isChecked()
    assert not chooser.btn_api.isChecked()


@pytest.mark.qt
def test_first_run_wizard_leads_with_json_not_the_api(qapp):
    from PySide6.QtWidgets import QLabel

    from cn_first_run_wizard import CnFirstRunWizard

    wizard = CnFirstRunWizard()
    try:
        text = "\n".join(label.text() for label in wizard.page(3).findChildren(QLabel))
    finally:
        wizard.deleteLater()
    assert "JSON → vMix 数据源" in text
    assert "复制 JSON 路径" in text
    assert "进阶：vMix API" in text
    assert text.find("JSON → vMix 数据源") < text.find("进阶：vMix API")
    assert "SetText" not in text
    assert "18099" not in text

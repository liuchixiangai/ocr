"""vMix title scan: state XML, SetText names, and empty-cell auto-match.

No live vMix. The fixture is a Web Controller GET /api/ document.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from vmix_title_scan import (
    SCAN_TIMEOUT,
    VmixScanError,
    VmixTitleInput,
    auto_match_fields,
    cell_is_placeholder,
    choose_title_input,
    fetch_vmix_api_xml,
    format_scan_error,
    format_title_problem,
    parse_vmix_api_xml,
    selected_name,
)

FIXTURE = Path(__file__).parent / "fixtures" / "vmix_api_state.xml"

GT_FIELDS = (
    "Home Score.Text",
    "Away Score.Text",
    "CLOCK.Text",
    "Period.Text",
    "ShotClock.Text",
    "Home Fouls.Text",
    "Away Fouls.Text",
    "Home Title.Text",
)


def _load():
    return parse_vmix_api_xml(FIXTURE.read_text(encoding="utf-8"))


def _by_number(inputs, number):
    return next(item for item in inputs if item.number == number)


def test_fixture_splits_title_types_and_ignores_cameras():
    inputs = _load()
    numbers = [item.number for item in inputs]
    # Capture has no text and is not a title. Empty GT is kept (no fields yet).
    assert numbers == ["2", "3", "4", "5", "6"]
    gt = _by_number(inputs, "2")
    assert gt.type == "GT"
    assert gt.short_title == "Scoreboard"
    assert gt.key == "gt-key-1111"
    assert gt.fields == GT_FIELDS
    assert "Home Logo.Source" not in gt.fields

    xaml = _by_number(inputs, "3")
    assert xaml.fields == ("Headline", "HomeScore")
    assert _by_number(inputs, "4").fields == ("Message",)
    assert _by_number(inputs, "5").fields == ()
    # Not a GT: a <text> name is kept, not given a .Text suffix.
    assert _by_number(inputs, "6").fields == ("Label",)


def test_gt_suffix_is_appended_only_when_missing():
    assert selected_name("GT", "ShotClock") == "ShotClock.Text"
    assert selected_name("gt", "Home Score.Text") == "Home Score.Text"
    assert selected_name("Xaml", "Headline") == "Headline"
    assert selected_name("Title", "Message") == "Message"
    assert selected_name("Xaml", "Already.Text") == "Already.Text"
    assert selected_name("Virtual", "Label") == "Label"
    assert selected_name("GT", "  ") == ""


def test_whitespace_around_a_gt_name_is_stripped_before_the_suffix():
    xml = """
    <vmix><inputs>
      <input number="1" type="GT" title="T" shortTitle="T">
        <text name="  Clock  "/>
      </input>
    </inputs></vmix>
    """
    [title] = parse_vmix_api_xml(xml)
    assert title.fields == ("Clock.Text",)


def test_malformed_or_non_vmix_xml_is_rejected():
    with pytest.raises(VmixScanError) as empty:
        parse_vmix_api_xml("   ")
    assert empty.value.code == "xml"
    with pytest.raises(VmixScanError) as broken:
        parse_vmix_api_xml("<vmix>")
    assert broken.value.code == "xml"
    with pytest.raises(VmixScanError) as html:
        parse_vmix_api_xml("<html><body>login</body></html>")
    assert html.value.code == "xml"
    with pytest.raises(VmixScanError) as tcp:
        parse_vmix_api_xml("VERSION OK 26.0.0.0")
    assert tcp.value.code == "tcp_port"


def test_choose_matches_number_key_and_title_and_asks_when_it_cannot():
    inputs = _load()
    by_number = choose_title_input(inputs, "2")
    assert by_number.kind == "unique"
    assert by_number.chosen.short_title == "Scoreboard"

    by_key = choose_title_input(inputs, "GT-KEY-1111")
    assert by_key.chosen.number == "2"

    by_title = choose_title_input(inputs, "scoreboard")
    assert by_title.chosen.number == "2"
    assert choose_title_input(inputs, "custom.xaml").chosen.number == "3"

    # Input 1 is a camera. Don't pretend it is a title; offer the real ones.
    missed = choose_title_input(inputs, "1")
    assert missed.kind == "pick"
    assert missed.reason == "no_match"
    assert [item.number for item in missed.options] == ["2", "3", "4", "6"]

    empty = choose_title_input(inputs, "  ")
    assert empty.kind == "pick"
    assert empty.reason == "empty"

    one = choose_title_input([_by_number(inputs, "3")], "")
    assert one.kind == "unique"
    assert one.chosen.number == "3"


def test_choose_reports_no_titles_and_a_title_with_no_text():
    none = choose_title_input([], "1")
    assert none.kind == "none"
    assert none.reason == "no_titles"
    assert "没有找到标题" in format_title_problem(none)

    empty_gt = VmixTitleInput("5", "k", "GT", "Empty.gtzip", "Empty Title", ())
    missing = choose_title_input([empty_gt], "5")
    assert missing.reason == "no_fields"
    text = format_title_problem(missing)
    assert "Empty Title" in text
    assert "没有文字字段" in text


def test_duplicate_titles_are_not_guessed():
    a = VmixTitleInput("1", "a", "GT", "Score", "Score", ("A.Text",))
    b = VmixTitleInput("2", "b", "GT", "Score", "Score", ("B.Text",))
    choice = choose_title_input([a, b], "Score")
    assert choice.kind == "pick"
    assert choice.reason == "ambiguous"
    assert [item.number for item in choice.options] == ["1", "2"]


def test_chinese_and_english_boxes_match_scoreboard_fields():
    fields = list(GT_FIELDS)
    matched = auto_match_fields(
        ["主队得分", "客队得分", "比赛时间", "节次", "24秒", "主队犯规", "客队犯规"],
        fields,
    )
    assert matched == {
        "主队得分": "Home Score.Text",
        "客队得分": "Away Score.Text",
        "比赛时间": "CLOCK.Text",
        "节次": "Period.Text",
        "24秒": "ShotClock.Text",
        "主队犯规": "Home Fouls.Text",
        "客队犯规": "Away Fouls.Text",
    }


def test_english_aliases_and_bare_home_away_prefer_score_over_title():
    fields = list(GT_FIELDS) + ["Home Team.Text"]
    assert auto_match_fields(
        ["Home Score", "Away Score", "Time", "Period", "shot_clock", "home_fouls"],
        fields,
    ) == {
        "Home Score": "Home Score.Text",
        "Away Score": "Away Score.Text",
        "Time": "CLOCK.Text",
        "Period": "Period.Text",
        "shot_clock": "ShotClock.Text",
        "home_fouls": "Home Fouls.Text",
    }
    assert auto_match_fields(["主队", "away"], fields) == {
        "主队": "Home Score.Text",
        "away": "Away Score.Text",
    }
    assert auto_match_fields(["Home Team"], fields) == {"Home Team": "Home Team.Text"}
    assert auto_match_fields(["Home Title"], fields) == {
        "Home Title": "Home Title.Text"
    }
    # A name box must not take the score field when that is all there is.
    assert auto_match_fields(["Home Team"], ["Home Score.Text"]) == {}


def test_clock_and_shot_clock_do_not_take_each_other():
    fields = ["CLOCK.Text", "Shot Clock.Text", "Period.Text"]
    matched = auto_match_fields(["比赛时间", "24秒"], fields)
    assert matched["比赛时间"] == "CLOCK.Text"
    assert matched["24秒"] == "Shot Clock.Text"
    assert "Period.Text" not in matched.values()

    game_only = auto_match_fields(["clock"], ["Shot Clock.Text", "CLOCK.Text"])
    assert game_only == {"clock": "CLOCK.Text"}
    shot_only = auto_match_fields(["24"], ["CLOCK.Text", "ShotClock.Text"])
    assert shot_only == {"24": "ShotClock.Text"}


def test_a_tie_fills_nothing():
    assert auto_match_fields(["主队得分", "home_score"], ["Home Score.Text"]) == {}
    assert auto_match_fields(["犯规"], ["Home Fouls.Text", "Away Fouls.Text"]) == {}
    # Two game-clock boxes want the same field: leave both empty.
    assert auto_match_fields(["比赛时间", "clock"], ["CLOCK.Text"]) == {}


def test_unknown_box_names_are_left_alone():
    assert auto_match_fields(["备注", "自定义"], list(GT_FIELDS)) == {}


def test_placeholder_is_blank_or_the_box_name_only():
    assert cell_is_placeholder("主队得分", "")
    assert cell_is_placeholder("主队得分", "  ")
    assert cell_is_placeholder("主队得分", "主队得分")
    assert not cell_is_placeholder("主队得分", "Home Score.Text")
    assert not cell_is_placeholder("Time", "Time.Text")
    assert not cell_is_placeholder("Time", "CLOCK.Text")


class _Response:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def test_fetch_requests_the_api_root_and_classifies_failures():
    seen = {}

    def ok(url, timeout):
        seen["url"] = url
        seen["timeout"] = timeout
        return _Response(200, "<vmix></vmix>")

    assert fetch_vmix_api_xml("127.0.0.1", "8088", get=ok) == "<vmix></vmix>"
    assert seen == {
        "url": "http://127.0.0.1:8088/api/",
        "timeout": SCAN_TIMEOUT,
    }

    def boom(url, timeout):
        raise requests.ConnectionError("connection refused")

    with pytest.raises(VmixScanError) as refused:
        fetch_vmix_api_xml("localhost", "8088", get=boom)
    assert refused.value.code == "connection"
    assert "连不上" in format_scan_error(refused.value)
    assert "8088" in format_scan_error(refused.value)

    def slow(url, timeout):
        raise requests.Timeout("timed out")

    with pytest.raises(VmixScanError) as timed:
        fetch_vmix_api_xml("localhost", "8088", get=slow)
    assert timed.value.code == "timeout"
    assert "等太久" in format_scan_error(timed.value)

    def tcp(url, timeout):
        raise requests.ConnectionError("VERSION OK 27.0")

    with pytest.raises(VmixScanError) as tcp_error:
        fetch_vmix_api_xml("localhost", "8099", get=tcp)
    assert tcp_error.value.code == "tcp_port"
    assert "8099" in format_scan_error(tcp_error.value)

    def body_tcp(url, timeout):
        return _Response(200, "VERSION OK 27.0")

    with pytest.raises(VmixScanError) as body:
        fetch_vmix_api_xml("localhost", "8099", get=body_tcp)
    assert body.value.code == "tcp_port"

    def denied(url, timeout):
        return _Response(401, "login")

    with pytest.raises(VmixScanError) as http401:
        fetch_vmix_api_xml("localhost", "8088", get=denied)
    assert http401.value.code == "unauthorized"
    assert "登录" in format_scan_error(http401.value)
    assert "401" in format_scan_error(http401.value)

    def forbidden(url, timeout):
        return _Response(403, "no")

    with pytest.raises(VmixScanError) as http403:
        fetch_vmix_api_xml("localhost", "8088", get=forbidden)
    assert http403.value.code == "unauthorized"

    def other(url, timeout):
        return _Response(500, "nope")

    with pytest.raises(VmixScanError) as http500:
        fetch_vmix_api_xml("localhost", "8088", get=other)
    assert http500.value.code == "http"
    assert "500" in format_scan_error(http500.value)

    with pytest.raises(VmixScanError) as no_host:
        fetch_vmix_api_xml("  ", "8088", get=ok)
    assert no_host.value.code == "no_host"
    with pytest.raises(VmixScanError) as bad_port:
        fetch_vmix_api_xml("localhost", "abc", get=ok)
    assert bad_port.value.code == "bad_port"


def test_error_copy_mentions_web_controller_when_it_is_off():
    text = format_scan_error(VmixScanError("connection"))
    assert "Web Controller" in text
    assert "Enable" in text


class _ImmediateThread:
    def __init__(self, target, name=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _rows(model):
    return {
        model.item(i, 0).text(): model.item(i, 1).text()
        for i in range(model.rowCount())
    }


@pytest.fixture
def scan_ui(qtbot):
    from PySide6.QtWidgets import (
        QLineEdit,
        QPushButton,
        QTableView,
        QVBoxLayout,
        QWidget,
    )

    # One parent, destroyed on the GUI thread when the test ends. A leftover
    # QObject collected from an output-worker thread crashes native Qt.
    parent = QWidget()
    ui = SimpleNamespace(
        tab_vmix=parent,
        verticalLayout_6=QVBoxLayout(parent),
        lineEdit_vmixHost=QLineEdit(parent),
        lineEdit_vmixPort=QLineEdit(parent),
        inputLineEdit_vmix=QLineEdit(parent),
        tableView_vmixMapping=QTableView(parent),
        pushButton_startvmix=QPushButton(parent),
    )
    ui.pushButton_startvmix.setCheckable(True)
    qtbot.addWidget(parent)
    return ui


@pytest.fixture
def handler(scan_ui):
    from vmix_output import VMixAPI
    from vmix_ui_handler import VMixUIHanlder

    ui_handler = VMixUIHanlder(scan_ui)
    ui_handler.vmixUpdater.close()
    ui_handler.vmixUpdater = VMixAPI("localhost", "8088", "1", {})
    # The real updater subscribes to settings; close it with the test.
    created = ui_handler.vmixUpdater
    yield ui_handler
    created.close()


@pytest.mark.qt
def test_guide_keeps_json_primary_and_labels_scan_as_advanced(handler, scan_ui):
    assert scan_ui.btn_scan_vmix_fields.text() == "进阶：扫描标题字段（API）"
    guide = scan_ui.label_vmix_guide.text()
    assert "给 vMix 的 JSON" in guide
    assert "进阶" in guide
    assert "不会被盖掉" in guide
    assert guide.find("给 vMix 的 JSON") < guide.find("进阶：扫描标题字段")


@pytest.mark.qt
def test_scan_fills_placeholders_and_keeps_edited_cells(
    handler, scan_ui, make_target, monkeypatch
):
    import vmix_ui_handler

    handler.updatevMixTable(
        [
            make_target("主队得分"),
            make_target("客队得分"),
            make_target("比赛时间"),
            make_target("节次"),
            make_target("24秒"),
            make_target("主队犯规"),
            make_target("客队犯规"),
            make_target("备注"),
            make_target("game clock"),
        ]
    )
    model = scan_ui.tableView_vmixMapping.model()
    assert _rows(model)["主队得分"] == "主队得分"
    # This cell is a real mapping, and it already uses CLOCK.Text, so the
    # other clock box must not be given that field as well.
    for row in range(model.rowCount()):
        if model.item(row, 0).text() == "比赛时间":
            model.item(row, 1).setText("CLOCK.Text")

    monkeypatch.setattr(vmix_ui_handler.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        vmix_ui_handler,
        "fetch_vmix_api_xml",
        lambda host, port: FIXTURE.read_text(encoding="utf-8"),
    )
    scan_ui.inputLineEdit_vmix.setText("2")
    scan_ui.btn_scan_vmix_fields.click()

    mapped = _rows(scan_ui.tableView_vmixMapping.model())
    assert mapped["主队得分"] == "Home Score.Text"
    assert mapped["客队得分"] == "Away Score.Text"
    assert mapped["比赛时间"] == "CLOCK.Text"
    assert mapped["节次"] == "Period.Text"
    assert mapped["24秒"] == "ShotClock.Text"
    assert mapped["主队犯规"] == "Home Fouls.Text"
    assert mapped["客队犯规"] == "Away Fouls.Text"
    assert mapped["备注"] == "备注"
    assert mapped["game clock"] == "game clock"
    assert scan_ui.inputLineEdit_vmix.text() == "2"
    status = scan_ui.label_vmix_scan_status.text()
    assert "Scoreboard" in status
    assert "已经改过的" in status
    assert "没猜到" in status
    assert handler._field_delegate.field_names == list(GT_FIELDS)

    from storage import fetch_data

    stored = fetch_data("scoresight.json", "vmix_mapping")
    assert stored["主队得分"] == "Home Score.Text"
    assert stored["比赛时间"] == "CLOCK.Text"

    # A second scan must not replace what the first one (or the user) wrote.
    scan_ui.btn_scan_vmix_fields.click()
    again = _rows(scan_ui.tableView_vmixMapping.model())
    assert again == mapped
    assert "没有盖掉" in scan_ui.label_vmix_scan_status.text()


@pytest.mark.qt
def test_delegate_lists_scanned_names_and_accepts_typed_text(
    handler, scan_ui, make_target
):
    from PySide6.QtWidgets import QComboBox

    handler.updatevMixTable([make_target("主队得分")])
    handler._field_delegate.field_names = ["Home Score.Text", "CLOCK.Text"]
    model = scan_ui.tableView_vmixMapping.model()
    editor = handler._field_delegate.createEditor(
        scan_ui.tableView_vmixMapping, None, model.index(0, 1)
    )
    assert isinstance(editor, QComboBox)
    assert editor.isEditable()
    texts = [editor.itemText(i) for i in range(editor.count())]
    assert texts == ["Home Score.Text", "CLOCK.Text"]
    editor.setCurrentText("手打的字段")
    handler._field_delegate.setModelData(editor, model, model.index(0, 1))
    assert model.item(0, 1).text() == "手打的字段"
    editor.deleteLater()


@pytest.mark.qt
def test_scan_errors_are_chinese_and_a_cancel_does_not_change_mappings(
    handler, scan_ui, make_target, monkeypatch
):
    import vmix_ui_handler

    handler.updatevMixTable([make_target("主队得分")])
    monkeypatch.setattr(vmix_ui_handler.threading, "Thread", _ImmediateThread)

    scan_ui.lineEdit_vmixHost.setText("  ")
    handler.scan_title_fields()
    assert "请先填写" in scan_ui.label_vmix_scan_status.text()

    scan_ui.lineEdit_vmixHost.setText("localhost")
    scan_ui.lineEdit_vmixPort.setText("nope")
    handler.scan_title_fields()
    assert "8088" in scan_ui.label_vmix_scan_status.text()

    scan_ui.lineEdit_vmixPort.setText("8088")

    def refuse(host, port):
        raise VmixScanError("connection")

    monkeypatch.setattr(vmix_ui_handler, "fetch_vmix_api_xml", refuse)
    scan_ui.inputLineEdit_vmix.setText("2")
    handler.scan_title_fields()
    assert "连不上" in scan_ui.label_vmix_scan_status.text()
    assert scan_ui.btn_scan_vmix_fields.isEnabled()
    assert _rows(scan_ui.tableView_vmixMapping.model()) == {"主队得分": "主队得分"}

    def html(host, port):
        return "<html>not vmix</html>"

    monkeypatch.setattr(vmix_ui_handler, "fetch_vmix_api_xml", html)
    handler.scan_title_fields()
    assert "状态 XML" in scan_ui.label_vmix_scan_status.text()

    monkeypatch.setattr(
        vmix_ui_handler,
        "fetch_vmix_api_xml",
        lambda host, port: FIXTURE.read_text(encoding="utf-8"),
    )
    scan_ui.inputLineEdit_vmix.setText("")
    monkeypatch.setattr(handler, "_ask_which_title", lambda choice: None)
    handler.scan_title_fields()
    assert "已取消" in scan_ui.label_vmix_scan_status.text()
    assert _rows(scan_ui.tableView_vmixMapping.model()) == {"主队得分": "主队得分"}
    assert handler._field_delegate.field_names == []


@pytest.mark.qt
def test_picking_a_title_writes_its_input_number(
    handler, scan_ui, make_target, monkeypatch
):
    import vmix_ui_handler

    handler.updatevMixTable([make_target("主队得分")])
    monkeypatch.setattr(vmix_ui_handler.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        vmix_ui_handler,
        "fetch_vmix_api_xml",
        lambda host, port: FIXTURE.read_text(encoding="utf-8"),
    )
    scan_ui.inputLineEdit_vmix.setText("1")

    def pick(choice):
        assert choice.reason == "no_match"
        return next(item for item in choice.options if item.number == "2")

    monkeypatch.setattr(handler, "_ask_which_title", pick)
    handler.scan_title_fields()
    assert scan_ui.inputLineEdit_vmix.text() == "2"
    assert _rows(scan_ui.tableView_vmixMapping.model()) == {
        "主队得分": "Home Score.Text"
    }


@pytest.mark.qt
def test_empty_input_with_one_title_is_filled_in(
    handler, scan_ui, make_target, monkeypatch
):
    import vmix_ui_handler

    xml = """
    <vmix><inputs>
      <input key="only" number="4" type="Xaml" title="Only.xaml" shortTitle="Only">
        <text name="Headline"/>
      </input>
    </inputs></vmix>
    """
    handler.updatevMixTable([make_target("备注")])
    monkeypatch.setattr(vmix_ui_handler.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(vmix_ui_handler, "fetch_vmix_api_xml", lambda host, port: xml)
    scan_ui.inputLineEdit_vmix.setText("")
    asked = []
    monkeypatch.setattr(
        handler, "_ask_which_title", lambda choice: asked.append(choice)
    )
    handler.scan_title_fields()
    assert asked == []
    assert scan_ui.inputLineEdit_vmix.text() == "4"
    assert handler._field_delegate.field_names == ["Headline"]
    # No guess for 备注, and XAML must not grow a .Text suffix.
    assert _rows(scan_ui.tableView_vmixMapping.model()) == {"备注": "备注"}
    assert "Only" in scan_ui.label_vmix_scan_status.text()

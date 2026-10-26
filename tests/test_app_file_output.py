"""file_output.FileOutput: state-based, atomic .txt/.csv/.xml outputs (#6, #29)."""

import csv
import json
import os
import xml.etree.ElementTree as ET

import pytest

import file_output
from file_output import (
    APPEND_BOTH,
    APPEND_CSV,
    APPEND_NONE,
    APPEND_TXT,
    FileOutput,
    is_good_reading,
    text_file_name,
)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def out(tmp_path):
    folder = tmp_path / "out"
    folder.mkdir()
    return folder


def read(path):
    return path.read_text(encoding="utf-8")


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


# --- good readings ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "text", "good"),
    [
        ("Success", "12", True),
        ("SameNoChange", "12", True),
        ("FailedFilter", "8B", False),
        ("Empty", "", False),
        ("Success", None, False),
    ],
)
def test_is_good_reading(make_result, state, text, good):
    assert is_good_reading(make_result("Home Score", text, state)) is good


def test_empty_text_is_good(make_result):
    # a box that "When blank" cleared; the 0.1.x setting skip_empty is ignored
    assert is_good_reading(make_result("Home Score", ""))
    assert is_good_reading(make_result("Home Score", "", skip_empty=True))


def test_text_file_name_replaces_invalid_characters():
    assert text_file_name("Home Score") == "Home Score.txt"
    assert text_file_name('Home/Away: "1st"?') == "Home_Away_ _1st__.txt"


# --- per-box .txt files ------------------------------------------------------------


def test_txt_written_for_good_readings_only(make_result, out):
    output = FileOutput()
    output.record(
        [
            make_result("Home Score", "12", "Success"),
            make_result("Away Score", "7", "SameNoChange"),
            make_result("Time", "1:23", "FailedFilter"),
            make_result("Period", "", "Empty"),
        ]
    )
    output.flush(str(out), save_per_box_txt=True)
    assert read(out / "Home Score.txt") == "12"
    assert read(out / "Away Score.txt") == "7"
    assert not (out / "Time.txt").exists()
    assert not (out / "Period.txt").exists()


def test_success_recorded_while_throttled_is_written_later(make_result, out):
    # #6: the only Success arrives between two flushes and later readings are
    # SameNoChange. The old edge-based writer never created the file.
    clock = FakeClock()
    output = FileOutput(clock)
    output.record([make_result("Home Score", "3", "Success")])
    output.flush(str(out), save_per_box_txt=True)
    clock.now += 0.05
    output.record([make_result("Home Score", "4", "Success")])
    assert output.seconds_until_flush(0.2) == pytest.approx(0.15)
    clock.now += 0.05
    output.record([make_result("Home Score", "4", "SameNoChange")])
    clock.now += 0.2
    assert output.seconds_until_flush(0.2) == 0
    output.flush(str(out), save_per_box_txt=True)
    assert read(out / "Home Score.txt") == "4"


def test_rejected_reading_never_overwrites_the_last_good_value(make_result, out):
    output = FileOutput()
    output.record([make_result("Home Score", "12")])
    output.flush(str(out), save_per_box_txt=True)
    output.record([make_result("Home Score", "8B", "FailedFilter")])
    output.flush(str(out), save_per_box_txt=True)
    output.record([make_result("Home Score", "", "Empty")])
    output.flush(str(out), save_per_box_txt=True)
    assert read(out / "Home Score.txt") == "12"
    assert output.latest_text("Home Score") == "12"


def test_unchanged_value_is_not_rewritten(make_result, out, monkeypatch):
    writes = []
    real_write = file_output.write_text_atomic
    monkeypatch.setattr(
        file_output,
        "write_text_atomic",
        lambda path, *a, **k: writes.append(path) or real_write(path, *a, **k),
    )
    output = FileOutput()
    for state in ("Success", "SameNoChange", "SameNoChange"):
        output.record([make_result("Home Score", "12", state)])
        output.flush(str(out), save_per_box_txt=True)
    assert len(writes) == 1


def test_missing_file_is_rewritten(make_result, out):
    output = FileOutput()
    output.record([make_result("Home Score", "12")])
    output.flush(str(out), save_per_box_txt=True)
    os.remove(out / "Home Score.txt")
    output.record([make_result("Home Score", "12", "SameNoChange")])
    output.flush(str(out), save_per_box_txt=True)
    assert read(out / "Home Score.txt") == "12"


def test_file_with_the_same_content_is_left_alone(make_result, out, monkeypatch):
    (out / "Home Score.txt").write_text("12", encoding="utf-8")
    monkeypatch.setattr(
        file_output,
        "write_text_atomic",
        lambda *a, **k: pytest.fail("the file already holds the value"),
    )
    output = FileOutput()
    output.record([make_result("Home Score", "12")])
    output.flush(str(out), save_per_box_txt=True)


def test_overwrite_is_atomic(make_result, out, monkeypatch):
    replaced = []
    real_replace = os.replace

    def spy_replace(src, dst):
        # the new content is complete in a temp file in the same folder
        assert os.path.dirname(src) == os.path.dirname(dst)
        assert read_path(src) == "13"
        replaced.append(dst)
        real_replace(src, dst)

    def read_path(p):
        with open(p, encoding="utf-8") as f:
            return f.read()

    output = FileOutput()
    output.record([make_result("Home Score", "12")])
    output.flush(str(out), save_per_box_txt=True)
    monkeypatch.setattr(file_output.os, "replace", spy_replace)
    output.record([make_result("Home Score", "13")])
    output.flush(str(out), save_per_box_txt=True)
    assert replaced == [os.path.join(str(out), "Home Score.txt")]
    assert os.listdir(out) == ["Home Score.txt"]


def test_failed_replace_keeps_the_old_file_and_retries(make_result, out, monkeypatch):
    output = FileOutput()
    output.record([make_result("Home Score", "12")])
    output.flush(str(out), save_per_box_txt=True)

    real_replace = os.replace
    reader = {"has_file_open": True}

    def replace(src, dst):
        if reader["has_file_open"]:
            raise PermissionError("locked by a reader")
        real_replace(src, dst)

    monkeypatch.setattr(file_output.os, "replace", replace)
    monkeypatch.setattr(file_output.time, "sleep", lambda s: None)
    output.record([make_result("Home Score", "13")])
    output.flush(str(out), save_per_box_txt=True)
    assert read(out / "Home Score.txt") == "12"
    assert os.listdir(out) == ["Home Score.txt"]  # no temp file left behind

    reader["has_file_open"] = False
    output.flush(str(out), save_per_box_txt=True)  # the pending value is written by the next flush
    assert read(out / "Home Score.txt") == "13"


@pytest.mark.parametrize("append_method", [APPEND_TXT, APPEND_BOTH])
def test_txt_append_mode_adds_a_line_per_change(make_result, out, append_method):
    output = FileOutput()
    for text, state in [
        ("1", "Success"),
        ("1", "SameNoChange"),
        ("2", "Success"),
        ("2", "SameNoChange"),
        ("X", "FailedFilter"),
        ("3", "Success"),
    ]:
        output.record([make_result("Home Score", text, state)])
        output.flush(str(out), append_method, save_per_box_txt=True)
    assert read(out / "Home Score.txt").splitlines() == ["1", "2", "3"]

    # a new session doesn't repeat the value that is already the last line
    output = FileOutput()
    output.record([make_result("Home Score", "3", "SameNoChange")])
    output.flush(str(out), append_method, save_per_box_txt=True)
    assert read(out / "Home Score.txt").splitlines() == ["1", "2", "3"]


def test_switching_to_overwrite_mode_replaces_an_appended_log(make_result, out):
    output = FileOutput()
    for text in ("1", "2"):
        output.record([make_result("Home Score", text)])
        output.flush(str(out), APPEND_TXT, save_per_box_txt=True)
    output.record([make_result("Home Score", "2", "SameNoChange")])
    output.flush(str(out), APPEND_NONE, save_per_box_txt=True)
    assert read(out / "Home Score.txt") == "2"


def test_non_ascii_values_are_utf8(make_result, out):
    output = FileOutput()
    output.record([make_result("Équipe", "Zürich", type=2, format_regex="^.*$")])
    output.flush(str(out), save_csv=True, save_xml=True, save_per_box_txt=True)
    assert read(out / "Équipe.txt") == "Zürich"
    assert read_csv(out / "results.csv")[1][1:] == ["Zürich"]


# --- results.csv ----------------------------------------------------------------------


def test_csv_overwrite_mode_is_a_single_row_snapshot(make_result, out):
    # #29: unchanged (SameNoChange) values are present, rejected ones keep the
    # last good value, and there is one header and one row.
    output = FileOutput()
    output.record(
        [
            make_result("Home Score", "12", "Success"),
            make_result("Away Score", "7", "SameNoChange"),
            make_result("Time", "1:2X", "FailedFilter"),
        ]
    )
    output.flush(str(out), APPEND_NONE, save_csv=True, save_per_box_txt=True)
    output.record(
        [
            make_result("Home Score", "8B", "FailedFilter"),
            make_result("Away Score", "9", "Success"),
            make_result("Time", "1:23", "Success"),
        ]
    )
    output.flush(str(out), APPEND_NONE, save_csv=True, save_per_box_txt=True)
    rows = read_csv(out / "results.csv")
    assert rows[0] == ["Timestamp", "Home Score", "Away Score", "Time"]
    assert rows[1][1:] == ["12", "9", "1:23"]
    assert len(rows) == 2


def test_csv_quotes_values_with_commas(make_result, out):
    output = FileOutput()
    output.record(
        [
            make_result("Home Score", "12", "SameNoChange"),
            make_result("Away Team", 'Smith, "J"', type=2),
        ]
    )
    output.flush(str(out), APPEND_NONE, save_csv=True, save_per_box_txt=True)
    rows = read_csv(out / "results.csv")
    assert rows[0] == ["Timestamp", "Home Score", "Away Team"]
    assert rows[1][1:] == ["12", 'Smith, "J"']


@pytest.mark.parametrize("append_method", [APPEND_CSV, APPEND_BOTH])
def test_csv_append_mode_has_one_header_and_a_row_per_change(
    make_result, out, append_method
):
    output = FileOutput()
    for home, away in [("0", "0"), ("0", "0"), ("2", "0"), ("2", "0"), ("2", "3")]:
        output.record(
            [
                make_result("Home Score", home, "SameNoChange"),
                make_result("Away Score", away, "SameNoChange"),
            ]
        )
        output.flush(str(out), append_method, save_csv=True, save_per_box_txt=True)
    rows = read_csv(out / "results.csv")
    assert rows[0] == ["Timestamp", "Home Score", "Away Score"]
    assert [row[1:] for row in rows[1:]] == [["0", "0"], ["2", "0"], ["2", "3"]]

    # the next session appends to the same table without another header
    output = FileOutput()
    output.record(
        [
            make_result("Away Score", "5"),
            make_result("Home Score", "2", "SameNoChange"),
        ]
    )
    output.flush(str(out), append_method, save_csv=True, save_per_box_txt=True)
    rows = read_csv(out / "results.csv")
    assert [row[0] for row in rows].count("Timestamp") == 1
    assert rows[-1][1:] == ["2", "5"]  # in the file's column order


def test_csv_append_rotates_the_log_when_the_boxes_change(make_result, out):
    output = FileOutput()
    output.record([make_result("Home Score", "1")])
    output.flush(str(out), APPEND_CSV, save_csv=True, save_per_box_txt=True)
    output.record([make_result("Home Score", "1"), make_result("Custom 1", "9")])
    output.flush(str(out), APPEND_CSV, save_csv=True, save_per_box_txt=True)

    rotated = [name for name in os.listdir(out) if name.startswith("results-")]
    assert len(rotated) == 1
    assert read_csv(out / rotated[0]) == [
        ["Timestamp", "Home Score"],
        [read_csv(out / rotated[0])[1][0], "1"],
    ]
    rows = read_csv(out / "results.csv")
    assert rows[0] == ["Timestamp", "Home Score", "Custom 1"]
    assert rows[1][1:] == ["1", "9"]


def test_csv_append_keeps_a_log_written_by_an_old_version(make_result, out):
    # 0.1.x wrote a trailing comma and repeated the header before every row
    old = "Timestamp,Home Score,\n2024-01-01T00:00:00,1,\nTimestamp,Home Score,\n"
    (out / "results.csv").write_text(old, encoding="utf-8")
    output = FileOutput()
    output.record([make_result("Home Score", "2")])
    output.flush(str(out), APPEND_CSV, save_csv=True, save_per_box_txt=True)
    rotated = [name for name in os.listdir(out) if name.startswith("results-")]
    assert len(rotated) == 1
    assert read(out / rotated[0]) == old
    rows = read_csv(out / "results.csv")
    assert rows[0] == ["Timestamp", "Home Score"]
    assert len(rows) == 2


def test_csv_not_written_when_disabled(make_result, out):
    output = FileOutput()
    output.record([make_result("Home Score", "1")])
    output.flush(str(out), APPEND_CSV, save_csv=False, save_xml=False, save_per_box_txt=True)
    assert sorted(os.listdir(out)) == ["Home Score.txt"]


# --- results.xml --------------------------------------------------------------------------


def test_xml_is_escaped_and_well_formed(make_result, out):
    output = FileOutput()
    output.record(
        [
            make_result("Home & Away", "<3>", type=2, format_regex="^.*$"),
            make_result("Time", "1:2X", "FailedFilter", rect=(10, 20, 30, 40)),
        ]
    )
    output.flush(str(out), save_xml=True, save_per_box_txt=True)
    root = ET.parse(out / "results.xml").getroot()
    assert root.tag == "data"
    first, second = root.findall("result")
    assert first.findtext("name") == "Home & Away"
    assert first.findtext("text") == "<3>"
    assert first.findtext("state") == "Success"
    # a box without a good reading is listed with an empty text
    assert second.findtext("name") == "Time"
    assert second.findtext("text") == ""
    assert second.findtext("state") == "FailedFilter"
    rect = second.find("rect")
    assert [rect.findtext(k) for k in ("x", "y", "width", "height")] == [
        "10.0",
        "20.0",
        "30.0",
        "40.0",
    ]


def test_xml_rewritten_only_on_change(make_result, out, monkeypatch):
    writes = []
    real_write = file_output.write_text_atomic
    monkeypatch.setattr(
        file_output,
        "write_text_atomic",
        lambda path, *a, **k: (
            writes.append(os.path.basename(path)) or real_write(path, *a, **k)
        ),
    )
    output = FileOutput()
    for text, state in [("1", "Success"), ("1", "SameNoChange"), ("2", "Success")]:
        output.record([make_result("Home Score", text, state)])
        output.flush(str(out), save_xml=True, save_per_box_txt=True)
    assert writes.count("results.xml") == 2
    root = ET.parse(out / "results.xml").getroot()
    assert root.find("result").findtext("text") == "2"


# --- results.json and results.txt (key=value) --------------------------------------


def test_json_and_key_value_files_have_every_box_with_its_last_good_value(
    make_result, out
):
    output = FileOutput()
    output.record(
        [
            make_result("Home Score", "12"),
            make_result("Time", "1:2X", "FailedFilter"),  # no good reading yet
            make_result("Période", "2ème"),
        ]
    )
    output.flush(str(out), save_json=True, save_key_value=True, save_per_box_txt=True)
    assert json.loads(read(out / "results.json")) == [{
        "home_score": "12",
        "game_clock": "",
        "Priode": "2ème",
    }]
    assert read(out / "results.txt").splitlines() == [
        "Home Score=12",
        "Time=",
        "Période=2ème",
    ]
    # a rejected reading never replaces a good value
    output.record(
        [
            make_result("Home Score", "13", "FailedFilter"),
            make_result("Time", "4:59"),
            make_result("Période", "2ème", "SameNoChange"),
        ]
    )
    output.flush(str(out), save_json=True, save_key_value=True, save_per_box_txt=True)
    assert json.loads(read(out / "results.json")) == [{
        "home_score": "12",
        "game_clock": "4:59",
        "Priode": "2ème",
    }]
    assert "Time=4:59" in read(out / "results.txt").splitlines()


def test_json_and_key_value_files_are_off_by_default(make_result, out):
    output = FileOutput()
    output.record([make_result("Home Score", "12")])
    output.flush(str(out), save_csv=True, save_xml=True)
    assert sorted(os.listdir(out)) == ["results.csv", "results.xml"]


def test_json_and_key_value_files_are_written_atomically_on_change(
    make_result, out, monkeypatch
):
    writes = []
    real_write = file_output.write_text_atomic
    monkeypatch.setattr(
        file_output,
        "write_text_atomic",
        lambda path, *a, **k: (
            writes.append(os.path.basename(path)) or real_write(path, *a, **k)
        ),
    )
    output = FileOutput()
    for text, state in [("1", "Success"), ("1", "SameNoChange"), ("2", "Success")]:
        output.record([make_result("Home Score", text, state)])
        output.flush(str(out), save_json=True, save_key_value=True, save_per_box_txt=True)
    assert writes.count("results.json") == 2
    assert writes.count("results.txt") == 2
    # a deleted file is written again
    os.remove(out / "results.json")
    output.flush(str(out), save_json=True, save_key_value=True, save_per_box_txt=True)
    assert writes.count("results.json") == 3
    assert json.loads(read(out / "results.json")) == [{"home_score": "2"}]
    assert not [name for name in os.listdir(out) if name.endswith(".tmp")]


def test_key_value_lines_stay_parseable(make_result, out):
    output = FileOutput()
    output.record(
        [
            make_result("a=b", "x=y", type=2, format_regex="^.*$"),
            make_result("Two\nLines", "1\n2", type=2, format_regex="^.*$"),
        ]
    )
    output.flush(str(out), save_key_value=True, save_per_box_txt=True)
    lines = read(out / "results.txt").splitlines()
    assert lines == ["a_b=x=y", "Two Lines=1 2"]
    # split at the first "=": the key, then the value
    assert [line.split("=", 1) for line in lines] == [
        ["a_b", "x=y"],
        ["Two Lines", "1 2"],
    ]


def test_a_box_named_results_keeps_its_own_txt_file(make_result, out):
    output = FileOutput()
    output.record([make_result("Results", "7"), make_result("Time", "1:00")])
    output.flush(str(out), save_key_value=True, save_per_box_txt=True)
    # the box's file (its value only), not the key=value file
    assert read(out / text_file_name("Results")) == "7"
    status = output.status()
    assert status.failing == ("results.txt",)
    assert "Results" in status.last_error


def test_flush_without_folder_or_results_writes_nothing(make_result, out):
    output = FileOutput()
    output.flush(str(out), save_csv=True, save_xml=True, save_per_box_txt=True)
    assert os.listdir(out) == []
    output.record([make_result("Home Score", "1")])
    output.flush("", save_csv=True, save_xml=True, save_per_box_txt=True)
    assert os.listdir(out) == []


# --- status() (the window's Text files destination) --------------------------------


def test_status_tells_what_was_written_and_when(make_result, out, tmp_path):
    clock = FakeClock()
    output = FileOutput(clock=clock)
    assert output.status().last_write_at is None

    output.record([make_result("Home Score", "1"), make_result("Time", "1:00")])
    output.flush(str(out), save_csv=True, save_per_box_txt=True)
    status = output.status()
    assert (status.folder, status.files, status.last_write_at) == (str(out), 3, 1000.0)
    assert status.failing == () and status.last_error is None

    # unchanged values are not written: the time of the last write stays
    clock.now = 1005.0
    output.record(
        [
            make_result("Home Score", "1", "SameNoChange"),
            make_result("Time", "1:00", "SameNoChange"),
        ]
    )
    output.flush(str(out), save_csv=True, save_per_box_txt=True)
    assert output.status().last_write_at == 1000.0

    # another folder starts over
    other = tmp_path / "other"
    other.mkdir()
    output.flush(str(other), save_per_box_txt=True)
    status = output.status()
    assert (status.folder, status.files, status.last_write_at) == (
        str(other),
        2,  # the two .txt files (no .csv this time)
        1005.0,
    )


def test_status_reports_failing_files_until_they_work_again(
    make_result, out, monkeypatch
):
    output = FileOutput()
    output.record([make_result("Home Score", "12")])
    real_replace = os.replace
    locked = {"on": True}

    def replace(src, dst):
        if locked["on"]:
            raise PermissionError("locked by a reader")
        real_replace(src, dst)

    monkeypatch.setattr(file_output.os, "replace", replace)
    monkeypatch.setattr(file_output.time, "sleep", lambda s: None)
    output.flush(str(out), save_per_box_txt=True)
    status = output.status()
    assert status.failing == ("Home Score.txt",)
    assert "locked by a reader" in status.last_error
    assert status.last_write_at is None

    locked["on"] = False
    output.flush(str(out), save_per_box_txt=True)
    status = output.status()
    assert status.failing == () and status.last_error is None
    assert status.files == 1


def test_per_box_txt_off_by_default(make_result, out):
    output = FileOutput()
    output.record([make_result("Home Score", "12")])
    output.flush(str(out), save_json=True)
    assert sorted(os.listdir(out)) == ["results.json"]
    output.flush(str(out), save_json=True, save_per_box_txt=True)
    assert sorted(os.listdir(out)) == ["Home Score.txt", "results.json"]

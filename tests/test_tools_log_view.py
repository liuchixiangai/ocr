"""The log viewer dialog (src/log_view.py), headless."""

import os

import pytest

import log_view
import sc_logging

pytestmark = pytest.mark.qt


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    """A log file the dialog shows instead of this session's real log."""
    path = tmp_path / "scoresight_test.log"
    path.write_text("first line\n", encoding="utf-8")
    monkeypatch.setattr(sc_logging, "log_file_path", str(path))
    return path


@pytest.fixture
def make_dialog(qapp):
    dialogs = []

    def _make():
        dialog = log_view.LogViewerDialog()
        dialogs.append(dialog)
        return dialog

    yield _make
    for dialog in dialogs:
        dialog.hide()
        dialog.deleteLater()
    qapp.processEvents()


def append(path, text):
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def test_no_log_file(make_dialog, monkeypatch, qapp):
    # sc_logging falls back to stderr (log_file_path None) when the log file
    # can't be created; the viewer used to raise TypeError every second.
    monkeypatch.setattr(sc_logging, "log_file_path", None)
    opened = []
    monkeypatch.setattr(
        log_view.QDesktopServices, "openUrl", lambda url: opened.append(url) or True
    )

    dialog = make_dialog()
    dialog.show()
    qapp.processEvents()
    dialog.update_ui()

    assert not dialog.timer.isActive()
    assert "console" in dialog.ui.textEdit_log.toPlainText()

    # "Open Log Folder" still opens the log directory
    dialog.ui.pushButton_openlogfolder.click()
    assert len(opened) == 1
    assert os.path.samefile(opened[0].toLocalFile(), sc_logging.get_log_dir())


def test_timer_runs_only_while_visible(make_dialog, log_file, qapp):
    dialog = make_dialog()
    assert not dialog.timer.isActive()

    dialog.show()
    qapp.processEvents()
    assert dialog.timer.isActive()
    assert dialog.ui.textEdit_log.toPlainText() == "first line"

    dialog.hide()
    assert not dialog.timer.isActive()

    append(log_file, "second line\n")
    dialog.show()
    qapp.processEvents()
    assert dialog.timer.isActive()
    # refreshed on show, without waiting for the timer
    assert dialog.ui.textEdit_log.toPlainText() == "first line\nsecond line"


def test_reads_utf8_and_survives_invalid_bytes(make_dialog, log_file):
    with open(log_file, "wb") as f:
        f.write("Données שלום\n".encode("utf-8") + b"bad \xff\x81 bytes\n")

    dialog = make_dialog()
    dialog.update_ui()

    text = dialog.ui.textEdit_log.toPlainText()
    assert text.startswith("Données שלום\n")
    assert "bad �� bytes" in text


def test_shows_the_last_lines_only(make_dialog, log_file, monkeypatch):
    with open(log_file, "w", encoding="utf-8") as f:
        f.writelines(f"line {i}\n" for i in range(1500))

    dialog = make_dialog()
    dialog.update_ui()

    lines = dialog.ui.textEdit_log.toPlainText().split("\n")
    assert len(lines) == log_view.MAX_LINES
    assert lines[0] == "line 500"
    assert lines[-1] == "line 1499"


def test_reads_only_the_end_of_a_big_log(log_file, monkeypatch):
    monkeypatch.setattr(log_view, "MAX_TAIL_BYTES", 100)
    with open(log_file, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(f"line number {i:04d}\n" for i in range(1000))

    lines = log_view.read_log_tail(str(log_file)).split("\n")

    # only whole lines from the last 100 bytes
    assert lines[-1] == "line number 0999"
    assert all(line.startswith("line number ") for line in lines)
    assert len(lines) == 100 // len("line number 0000\n")


def test_unchanged_log_is_not_read_again(make_dialog, log_file, monkeypatch):
    reads = []
    real_read = log_view.read_log_tail
    monkeypatch.setattr(
        log_view, "read_log_tail", lambda path: reads.append(path) or real_read(path)
    )
    dialog = make_dialog()

    dialog.update_ui()
    dialog.update_ui()
    assert len(reads) == 1

    append(log_file, "more\n")
    dialog.update_ui()
    assert len(reads) == 2
    assert dialog.ui.textEdit_log.toPlainText().endswith("more")


def test_missing_log_file_shows_an_error(make_dialog, log_file):
    os.remove(log_file)

    dialog = make_dialog()
    dialog.update_ui()

    assert "Can't read the log file" in dialog.ui.textEdit_log.toPlainText()

    # and recovers when it is back
    append(log_file, "back\n")
    dialog.update_ui()
    assert dialog.ui.textEdit_log.toPlainText() == "back"


def test_scroll_position_kept_without_auto_scroll(make_dialog, log_file, qapp):
    with open(log_file, "w", encoding="utf-8") as f:
        f.writelines(f"line {i}\n" for i in range(300))
    dialog = make_dialog()
    dialog.resize(400, 200)
    dialog.show()
    qapp.processEvents()
    scrollbar = dialog.ui.textEdit_log.verticalScrollBar()
    assert scrollbar.value() == scrollbar.maximum() > 0  # auto scroll is on

    dialog.ui.checkBox_autoScroll.setChecked(False)
    scrollbar.setValue(10)
    append(log_file, "new line\n")
    dialog.update_ui()

    assert dialog.ui.textEdit_log.toPlainText().endswith("new line")
    assert scrollbar.value() == 10

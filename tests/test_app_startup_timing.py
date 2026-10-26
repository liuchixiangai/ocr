"""startup_timing: the startup milestones, the "Startup: ..." log line and the
SCORESIGHT_STARTUP_REPORT file."""

import json
import logging
import sys
import time

import pytest

import startup_timing

MILESTONES = {
    "args_parsed": 0.01,
    "modules_imported": 1.25,
    "qapplication_created": 1.35,
    "main_window_created": 2.0,
    "window_shown": 2.05,
    "event_loop_idle": 2.1,
}


def startup_records(caplog):
    return [r for r in caplog.records if r.getMessage().startswith("Startup:")]


def test_only_the_first_time_a_milestone_is_reached_counts(timing):
    timing.mark("sources_discovered")
    first = timing.elapsed("sources_discovered")
    time.sleep(0.01)
    timing.mark("sources_discovered")  # e.g. Refresh sources
    assert timing.elapsed("sources_discovered") == first
    assert timing.elapsed("event_loop_idle") is None


def test_the_phases_run_from_milestone_to_milestone(timing, monkeypatch):
    timing._milestones.update(MILESTONES, http_server_ready=1.6)
    monkeypatch.setattr(timing, "_before_main", lambda: 0.5)
    report = timing.report()
    assert report["phases"] == pytest.approx(
        {
            "arguments": 0.01,
            "imports": 1.24,
            "QApplication": 0.1,
            "main window": 0.65,
            "show": 0.05,
            "first event loop pass": 0.05,
        }
    )
    assert list(report["phases"]) == [label for _, label in timing.PHASES]
    # the total and the background milestones count from the process start
    assert report["total"] == pytest.approx(2.6)
    assert report["ready_at"] == pytest.approx({"HTTP server ready": 2.1})
    assert report["before_main"] == 0.5
    assert report["milestones"]["modules_imported"] == 1.25
    assert not timing.background_done()
    timing.mark("sources_discovered")
    assert timing.background_done()


def test_a_phase_whose_milestone_was_missed_is_left_out(timing, monkeypatch):
    milestones = dict(MILESTONES)
    del milestones["qapplication_created"]
    timing._milestones.update(milestones)
    monkeypatch.setattr(timing, "_before_main", lambda: None)
    report = timing.report()
    assert "QApplication" not in report["phases"]
    # the main window phase starts at the previous milestone that was reached
    assert report["phases"]["main window"] == pytest.approx(0.75)
    # without the process start, times count from main.py
    assert report["total"] == pytest.approx(2.1)


def test_summary_line():
    report = {
        "before_main": 0.412,
        "phases": {"arguments": 0.004, "imports": 1.234, "main window": 0.5},
        "total": 2.5,
        "ready_at": {"HTTP server ready": 1.1},
    }
    assert startup_timing.summary_line(report) == (
        "Startup: before main.py 0.41 s, arguments 0.00 s, imports 1.23 s, "
        "main window 0.50 s; total 2.50 s; HTTP server ready at 1.10 s, "
        "video sources discovered: not yet"
    )


def test_summary_line_without_the_process_start_and_before_ready():
    report = {
        "before_main": None,
        "phases": {"arguments": 0.01},
        "total": None,
        "ready_at": {},
    }
    assert startup_timing.summary_line(report) == (
        "Startup: arguments 0.01 s; HTTP server ready: not yet, "
        "video sources discovered: not yet"
    )


@pytest.mark.skipif(
    not sys.platform.startswith(("win32", "linux", "darwin")),
    reason="the process start time is read on Windows, Linux and macOS",
)
def test_the_time_before_main_py_is_measured(timing):
    # this test process started before startup_timing was imported
    age = timing._process_age_now()
    assert age is not None and 0 < age < 24 * 3600
    before_main = timing._before_main()
    assert before_main is not None and 0 <= before_main < age


def test_finish_logs_one_line_once(timing, caplog):
    timing._milestones.update(MILESTONES)
    with caplog.at_level(logging.INFO):
        timing.finish()
        timing.finish()
    [record] = startup_records(caplog)
    assert record.levelno == logging.INFO
    assert "imports 1.24 s" in record.getMessage()


def test_finish_writes_the_report_when_asked(timing, tmp_path, monkeypatch):
    path = tmp_path / "reports" / "startup.json"
    monkeypatch.setenv(timing.REPORT_ENV, str(path))
    timing._milestones.update(MILESTONES, sources_discovered=2.2)
    timing.finish()
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["milestones"] == MILESTONES | {"sources_discovered": 2.2}
    assert list(report["phases"]) == [label for _, label in timing.PHASES]
    assert set(report) >= {"version", "frozen", "before_main", "total", "ready_at"}
    assert report["main_started_at"] == pytest.approx(time.time(), abs=24 * 3600)
    # written atomically: no temporary file is left behind
    assert [p.name for p in path.parent.iterdir()] == ["startup.json"]


def test_no_report_without_the_environment_variable(timing, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    timing.finish()
    assert list(tmp_path.iterdir()) == []


def test_milestones_after_the_summary_are_logged(timing, caplog):
    timing._milestones.update(MILESTONES)
    with caplog.at_level(logging.INFO):
        timing.finish()
        timing.mark("sources_discovered")
    messages = [r.getMessage() for r in startup_records(caplog)]
    assert len(messages) == 2
    assert messages[1].startswith("Startup: video sources discovered at ")


def test_writing_the_report_never_raises(timing, tmp_path):
    # a folder can't be replaced by the report
    assert timing.write_report(str(tmp_path)) is False
    assert timing.write_report(str(tmp_path / "x.json"), {"bad": object()}) is False


def test_finish_never_raises(timing, monkeypatch, caplog):
    def broken():
        raise RuntimeError("broken")

    monkeypatch.setattr(timing, "report", broken)
    timing.finish()
    assert startup_records(caplog) == []

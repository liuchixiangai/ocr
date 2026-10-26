"""scripts/ci/e2e_test.py, boot_benchmark.py and app_harness.py: the synthetic
scoreboard video and its OCR, the settings they write against the app's own
code, their parsers against the real outputs, and the process handling with a
fake app. The real end-to-end run is a CI step (see docs/development/testing.md).
"""

import importlib.util
import json
import re
import sys
import textwrap
import time
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
CI_SCRIPTS = REPO_ROOT / "scripts" / "ci"


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, CI_SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# e2e_test and boot_benchmark import app_harness by name, as when run as scripts
app_harness = _load_script("app_harness")
e2e_test = _load_script("e2e_test")
boot_benchmark = _load_script("boot_benchmark")


# --- the synthetic scoreboard ---------------------------------------------------------


def test_timeline():
    timeline = e2e_test.build_timeline()
    homes = e2e_test.collapse([state.home for state, _ in timeline])
    assert homes == ["0", "1", "2"]
    assert {state.away for state, _ in timeline} == {"3"}
    clocks = [state.time for state, _ in timeline]
    assert len(set(clocks)) == len(clocks), "every clock value is shown once"
    final, frames = timeline[-1]
    assert final == e2e_test.State("2", "3", "11:51")
    # long enough to be seen at a few readings per second, even on a slow runner
    assert frames >= 5 * e2e_test.FPS


def test_video_round_trip(tmp_path):
    # MJPG in .avi: the OpenCV wheels write and read it on every OS
    timeline = e2e_test.build_timeline()
    path = tmp_path / "scoreboard.avi"
    frames = e2e_test.write_video(path, timeline)
    assert frames == sum(count for _, count in timeline)
    assert e2e_test.count_video_frames(path) == frames


@pytest.mark.ocr
@pytest.mark.parametrize(
    "model",
    [
        e2e_test.OCR_MODEL_GENERAL_SCOREBOARD,
        pytest.param(e2e_test.OCR_MODEL_PPOCR_ACCURATE, marks=pytest.mark.ppocr),
        # PP-OCR (automatic) reads with it on a computer too slow for accurate
        pytest.param("ppocr:tiny", marks=pytest.mark.ppocr),
    ],
)
def test_the_app_reads_every_frame_of_the_video(tmp_path, model):
    # What makes the end-to-end test deterministic: with the e2e boxes and
    # either engine's models, the OCR reads each scoreboard state of the video
    # correctly (PP-OCR from the colour frame, as the capture thread gives it).
    import cv2

    from defaults import default_info_for_box_name, normalize_settings_dict
    from tesseract import TextDetector
    from text_detection_target import TextDetectionTarget

    timeline = e2e_test.build_timeline()
    # one frame per state, through the same MJPG encoding as the video
    states = [state for state, _ in timeline]
    path = tmp_path / "states.avi"
    e2e_test.write_video(path, [(state, 1) for state in states])
    capture = cv2.VideoCapture(str(path))
    targets = [
        TextDetectionTarget(
            *rect,
            name,
            normalize_settings_dict(
                dict(e2e_test.BOX_SETTINGS), default_info_for_box_name(name)
            ),
            [],
        )
        for name, rect in e2e_test.BOXES.items()
    ]
    detector = TextDetector(model)
    try:
        assert detector.model == model
        assert detector.wait_for_engine(60)
        wrong = []
        for state in states:
            ok, frame = capture.read()
            assert ok
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            results = detector.detect_multi_text(None, gray, targets, color=frame)
            read = {t.name: (r.text, r.state.name) for t, r in zip(targets, results)}
            expected = {name: (v, "Success") for name, v in state.values().items()}
            if read != expected:
                wrong.append((state, read))
        assert wrong == []
    finally:
        capture.release()
        if detector.api is not None:
            detector.api.End()


# --- the app's settings ------------------------------------------------------------


def settings():
    return e2e_test.app_settings(
        Path("/videos/scoreboard.avi"), Path("/out"), "http://127.0.0.1:1/x"
    )


def test_every_setting_is_one_the_app_reads():
    sources = "\n".join(p.read_text(encoding="utf-8") for p in SRC_DIR.glob("*.py"))
    for key in settings():
        assert re.search(rf"""["']{re.escape(key)}["']""", sources), key


def test_settings_values_match_the_app():
    import api_output
    import file_output
    import ocr_models
    import ppocr_engine
    import storage
    import update_check
    from camera_info import CameraInfo
    from tesseract import TextDetector

    values = settings()
    assert values["source_selected"]["type"] == CameraInfo.CameraType.FILE
    assert values["boxes_schema_version"] == storage.BOXES_SCHEMA_VERSION
    # PP-OCR: a new setup, without an OCR model, whose default is PP-OCR
    # (accurate); Tesseract: General Scoreboard
    assert "ocr_model" not in values
    assert e2e_test.OCR_MODEL_PPOCR_DEFAULT == ocr_models.DEFAULT_MODEL
    assert e2e_test.OCR_MODEL_PPOCR_ACCURATE == ocr_models.PPOCR_ACCURATE
    tesseract_values = e2e_test.app_settings(
        Path("/videos/scoreboard.avi"),
        Path("/out"),
        "http://127.0.0.1:1/x",
        "tesseract",
    )
    assert (
        tesseract_values["ocr_model"]
        == e2e_test.OCR_MODEL_GENERAL_SCOREBOARD
        == TextDetector.OcrModelIndex.SCOREBOARD_GENERAL
    )
    assert {k: v for k, v in tesseract_values.items() if k != "ocr_model"} == values
    with pytest.raises(ValueError):
        e2e_test.app_settings(Path("/v.avi"), Path("/out"), "http://x", "paddle")
    # the log lines the engine checks look for
    sources = "\n".join(p.read_text(encoding="utf-8") for p in SRC_DIR.glob("*.py"))
    assert "PP-OCR: loaded %s" in sources
    # automatic loads accurate (and fast)
    assert ppocr_engine.models_of(ocr_models.DEFAULT_MODEL)[0] == (
        ocr_models.PPOCR_ACCURATE
    )
    assert (
        ppocr_engine.MODELS[ocr_models.PPOCR_ACCURATE].folder
        in (e2e_test.ENGINE_LOG_LINES["ppocr"])
    )
    assert "Setting OCR model to {ocr_model}" in sources
    assert e2e_test.PPOCR_FALLBACK_LOG_LINE in sources
    assert e2e_test.PPOCR_AUTO_FAST_LOG_LINE in sources
    assert values["append_method"] == file_output.APPEND_CSV
    assert values["out_api_encoding"] in api_output.ENCODINGS
    assert values["out_api_method"] in api_output.METHODS
    assert update_check.DISABLE_UPDATE_CHECKS_KEY in values
    # within the ranges of the main window's sliders
    assert 1 <= values["detection_cadence"] <= 15
    assert 1 <= values["aggs_per_second"] <= 10


def test_the_app_loads_the_boxes_and_the_source(memory_storage, data_dir, monkeypatch):
    from mainwindow import stored_source_value

    monkeypatch.setenv("SCORESIGHT_PROFILE", e2e_test.PROFILE)
    data_dir.mkdir(parents=True)
    video = data_dir / "scoreboard.avi"
    video.write_bytes(b"")
    path = e2e_test.settings_path(data_dir)
    path.write_text(json.dumps(e2e_test.app_settings(video, data_dir, "x")))

    import storage

    assert storage.get_storage_path("scoresight.json") == str(path)
    assert memory_storage.loadBoxesFromStorage()
    boxes = {box.name: box for box in memory_storage.get_data()}
    assert list(boxes) == list(e2e_test.BOXES)
    for name, (x, y, width, height) in e2e_test.BOXES.items():
        box = boxes[name]
        assert (box.x(), box.y(), box.width(), box.height()) == (x, y, width, height)
        assert box.settings["autocrop"] is True and box.settings["dilate"] == 0
    assert boxes["Time"].settings["type"] == 1  # FieldType.TIME, by its name
    source = stored_source_value(
        storage.fetch_data("scoresight.json", "source_selected")
    )
    assert source == {"type": "File", "value": str(video)}
    # not migrated: the schema version is current
    assert json.loads(path.read_text())["boxes"][0]["settings"] == e2e_test.BOX_SETTINGS


# --- parsing the outputs ---------------------------------------------------------------


@pytest.fixture
def http_port():
    import http_server

    http_server.start_http_server(port=0)
    yield http_server.get_http_server_port()
    http_server.stop_http_server()
    http_server.update_http_server([])


def test_parsers_read_the_http_server(http_port, make_result):
    import http_server

    http_server.update_http_server(
        [
            make_result("Home Score", "2"),
            make_result("Away Score", "3", "SameNoChange"),
            make_result("Time", "11:5.1", "FailedFilter"),
        ]
    )

    def get(path):
        status, body = app_harness.http_get(http_port, path)
        assert status == 200
        return body

    expected = {
        "Home Score": ("2", "Success"),
        "Away Score": ("3", "SameNoChange"),
        "Time": ("11:5.1", "FailedFilter"),
    }
    assert e2e_test.parse_json_readings(get("/json")) == expected
    assert e2e_test.parse_xml_readings(get("/xml")) == expected
    # only good readings
    assert e2e_test.parse_csv_pivot_readings(get("/csv?pivot")) == {
        "Home Score": ("2", "Success"),
        "Away Score": ("3", "Success"),
    }
    rows = e2e_test.parse_csv_rows(get("/csv"))
    assert rows[0] == ["Name", "Text", "X", "Y", "Width", "Height"]
    assert [row[:2] for row in rows[1:]] == [
        ["Home Score", "2"],
        ["Away Score", "3"],
        ["Time", "11:5.1"],
    ]


def test_the_overlays_live_values_are_followed(http_port, make_result):
    import http_server

    timeline = e2e_test.build_timeline()
    allowed, final = e2e_test.allowed_values(timeline), timeline[-1][0].values()
    http_server.update_http_server(
        [make_result("Home Score", "0"), make_result("Time", "x", "FailedFilter")]
    )
    status, body = app_harness.http_get(http_port, "/overlay/state")
    assert status == 200
    assert e2e_test.parse_overlay_state(body) == {
        "Home Score": ("0", "Success"),
        "Time": ("", "Empty"),
    }

    events = e2e_test.EventStreamHistory(allowed, final)
    events.start(http_port)
    try:
        deadline = time.monotonic() + 5
        while events.events < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        for home in ("1", "2"):
            http_server.update_http_server(
                [
                    make_result("Home Score", home),
                    make_result("Away Score", "3"),
                    make_result("Time", "11:51"),
                ]
            )
            expected = events.events + 1
            while events.events < expected and time.monotonic() < deadline:
                time.sleep(0.02)
    finally:
        http_server.stop_http_server()  # ends the stream
        events.stop()
    assert events.events == 3, events.error
    assert events.sequence_seen("Home Score", ["0", "1", "2"])
    assert events.final_seen_at is not None
    assert all(check.passed for check in events.checks(["0", "1", "2"]))


def test_parsers_read_the_output_files(tmp_path, make_result):
    import file_output

    output = file_output.FileOutput()
    for home in ("0", "1"):
        output.record(
            [
                make_result("Home Score", home),
                make_result("Away Score", "3"),
                make_result("Time", "", "Empty"),
            ]
        )
        output.flush(str(tmp_path), file_output.APPEND_CSV, True, True)
    assert e2e_test.read_file_readings(tmp_path) == {
        "Home Score": ("1", "Success"),
        "Away Score": ("3", "Success"),
    }
    assert e2e_test.read_xml_file_readings(tmp_path) == {
        "Home Score": ("1", "Success"),
        "Away Score": ("3", "Success"),
    }
    header, rows = e2e_test.read_csv_file_rows(tmp_path / "results.csv")
    assert header == ["Timestamp", "Home Score", "Away Score", "Time"]
    assert rows == [
        {"Home Score": "0", "Away Score": "3", "Time": ""},
        {"Home Score": "1", "Away Score": "3", "Time": ""},
    ]


def test_output_history():
    timeline = e2e_test.build_timeline()
    history = e2e_test.OutputHistory(
        "test", e2e_test.allowed_values(timeline), timeline[-1][0].values()
    )

    def add(home, time_text, state="Success"):
        history.add(
            {
                "Home Score": (home, state),
                "Away Score": ("3", state),
                "Time": (time_text, state),
            }
        )

    add("0", "12:00")
    add("", "", "Empty")  # not read: transient, not invalid
    add("0", "12:00", "SameNoChange")
    add("8", "1:5.1", "FailedFilter")  # rejected: transient
    add("1", "11:57")
    add("2", "11:51")
    checks = {check.name: check.passed for check in history.checks(["0", "1", "2"])}
    assert all(checks.values()), checks
    assert history.transient == 6
    assert history.values["Home Score"] == ["0", "1", "2"]

    add("7", "11:51")  # a good reading the video never shows
    add("0", "99:99", "SameNoChange")
    [valid, sequence, final] = history.checks(["0", "1", "2"])
    assert not valid.passed
    assert "Home Score = '7' (Success)" in valid.detail
    assert "Time = '99:99' (SameNoChange)" in valid.detail
    assert sequence.passed and final.passed


def test_output_history_needs_the_sequence_and_the_final_state():
    timeline = e2e_test.build_timeline()
    history = e2e_test.OutputHistory(
        "test", e2e_test.allowed_values(timeline), timeline[-1][0].values()
    )
    history.add({"Home Score": ("2", "Success"), "Away Score": ("3", "Success")})
    history.add({"Home Score": ("1", "Success"), "Time": ("11:51", "Success")})
    [valid, sequence, final] = history.checks(["0", "1", "2"])
    assert valid.passed
    assert not sequence.passed
    assert not final.passed  # never all three boxes at once


def test_mock_receiver_records_requests():
    receiver = e2e_test.MockReceiver()
    receiver.start()
    try:
        request = urllib.request.Request(receiver.url, data=b"[]", method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200
        [(_, method, path, body)] = receiver.snapshot()
        assert (method, path, body) == ("POST", "/scoresight", b"[]")
    finally:
        receiver.stop()


def test_e2e_needs_an_app_command(capsys):
    with pytest.raises(SystemExit) as error:
        e2e_test.main([])
    assert error.value.code == 2
    assert e2e_test.main(["--", str(REPO_ROOT / "no-such-app.exe")]) == 1
    assert "does not exist" in capsys.readouterr().err


def test_e2e_summary(monkeypatch, tmp_path):
    result = e2e_test.Result(timings={"http_ready": 1.234}, exit_code=0)
    result.check("works", True)
    result.check("broken", False, "details")
    summary = e2e_test.format_summary(result, "linux-x64")
    assert summary.startswith("### End-to-end test (linux-x64): FAILED")
    assert "| HTTP server answers | 1.23 s |" in summary
    assert "1/2 checks passed." in summary
    assert "- FAILED: broken: details" in summary
    path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
    app_harness.append_step_summary(summary)
    assert path.read_text(encoding="utf-8") == summary + "\n\n"


# --- process handling, with a fake app -------------------------------------------------

FAKE_APP = textwrap.dedent(
    """
    import http.server, os, signal, sys, threading

    port = int(os.environ.get("FAKE_APP_PORT") or sys.argv[sys.argv.index("--port") + 1])
    if os.environ.get("FAKE_APP_NO_HTTP"):
        threading.Event().wait(60)
    report = os.environ.get("SCORESIGHT_STARTUP_REPORT")
    if report:
        with open(report, "w") as f:
            f.write('{"total": 0.5, "before_main": 0.1, "phases": {"imports": 0.3}, '
                    '"ready_at": {}, "main_started_at": 0}')

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"[]")

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stop = threading.Event()
    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *a: stop.set())
    while not stop.wait(0.05):
        pass
    server.shutdown()
    print("fake app stopped")
    """
)


@pytest.fixture
def fake_app(tmp_path):
    path = tmp_path / "fake_app.py"
    path.write_text(FAKE_APP, encoding="utf-8")
    return [sys.executable, str(path)]


def test_app_process_starts_and_stops_like_a_user_would(fake_app, tmp_path):
    port = app_harness.free_port()
    env = app_harness.app_env(tmp_path / "data", tmp_path / "logs")
    assert env["QT_QPA_PLATFORM"] == "offscreen"
    assert env["SCORESIGHT_DATA_DIR"] == str(tmp_path / "data")
    with app_harness.AppProcess(
        [*fake_app, "--port", str(port)], env, tmp_path / "out.txt"
    ) as app:
        ready = app_harness.wait_for_http(app, port, timeout=60)
        assert ready is not None and ready < 60
        assert app_harness.http_get(port, "/json") == (200, b"[]")
        # SIGTERM, or Ctrl+Break on Windows
        assert app.stop(timeout=30) == 0
        assert not app.killed
    assert "fake app stopped" in app.output()


def test_app_process_kills_an_app_that_does_not_quit(tmp_path):
    script = tmp_path / "stubborn.py"
    script.write_text(
        "import signal, time\n"
        "for name in ('SIGTERM', 'SIGINT', 'SIGBREAK'):\n"
        "    if hasattr(signal, name):\n"
        "        signal.signal(getattr(signal, name), signal.SIG_IGN)\n"
        "print('started', flush=True)\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    env = app_harness.app_env(tmp_path / "data", tmp_path / "logs")
    with app_harness.AppProcess(
        [sys.executable, str(script)], env, tmp_path / "out.txt"
    ) as app:
        port = app_harness.free_port()
        # nothing listens: no answer, and the app is still running
        assert app_harness.wait_for_http(app, port, timeout=1) is None
        assert app.stop(timeout=2) is None
        assert app.killed and not app.running()


def test_resolve_command(tmp_path, monkeypatch):
    program = tmp_path / "dist" / "app.exe"
    program.parent.mkdir()
    program.write_bytes(b"")
    monkeypatch.chdir(tmp_path)
    # a relative path, as the workflows give it (CreateProcess can't use it)
    [absolute, option] = app_harness.resolve_command(["dist/app.exe", "-x"])
    assert Path(absolute).is_absolute() and Path(absolute).samefile(program)
    assert option == "-x"
    # a symbolic link (a venv's bin/python) is kept, not resolved
    link = tmp_path / "venv-python"
    try:
        link.symlink_to(program)
    except OSError:
        pass  # Windows without the symlink privilege
    else:
        [kept] = app_harness.resolve_command(["venv-python"])
        assert Path(kept).name == "venv-python"
    # a program on PATH
    python = Path(sys.executable)
    monkeypatch.setenv("PATH", str(python.parent))
    [found] = app_harness.resolve_command([python.stem])
    assert Path(found).samefile(python)
    with pytest.raises(FileNotFoundError, match="does not exist"):
        app_harness.resolve_command(["dist/missing.exe"])


def test_print_diagnostics(tmp_path, capsys):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "scoresight_1.log").write_text("line 1\nTraceback: boom\n")
    app_harness.print_diagnostics(None, logs, lines=1)
    output = capsys.readouterr().out
    assert "scoresight_1.log" in output
    assert "Traceback: boom" in output and "line 1" not in output


# --- the boot benchmark --------------------------------------------------------------


def test_benchmark(fake_app, tmp_path, capsys):
    out = tmp_path / "boot.json"
    code = boot_benchmark.main(
        [
            "--runs",
            "3",
            "--label",
            "fake",
            "--json",
            str(out),
            "--max-warm-seconds",
            "60",
            "--",
            *fake_app,
        ]
    )
    assert code == 0, capsys.readouterr().out
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["passed"] and result["failures"] == []
    assert [run["kind"] for run in result["runs"]] == ["cold", "warm", "warm"]
    assert all(run["exit_code"] == 0 for run in result["runs"])
    summary = result["summary"]
    assert summary["runs"] == 3 and summary["failed_runs"] == 0
    assert summary["warm_min"] <= summary["warm_median"] <= summary["warm_max"]
    # the fake app's startup report
    assert summary["in_app"]["in-app: imports"] == {"cold": 0.3, "warm_median": 0.3}
    assert "### Boot time: fake" in capsys.readouterr().out


def test_legacy_benchmark_uses_the_fixed_port(fake_app, tmp_path, monkeypatch):
    port = app_harness.free_port()
    monkeypatch.setattr(app_harness, "LEGACY_HTTP_PORT", port)
    monkeypatch.setenv("FAKE_APP_PORT", str(port))
    out = tmp_path / "legacy.json"
    assert (
        boot_benchmark.main(
            ["--legacy", "--runs", "1", "--json", str(out), "--", *fake_app]
        )
        == 0
    )
    result = json.loads(out.read_text(encoding="utf-8"))
    [run] = result["runs"]
    assert run["port"] == port and run["ready"] is not None
    # no --port option and no startup report for 0.1.x
    assert "startup_report" not in run
    assert "in_app" not in result["summary"]


def test_benchmark_fails_when_the_app_never_gets_ready(fake_app, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_APP_NO_HTTP", "1")
    monkeypatch.setattr(boot_benchmark, "STOP_TIMEOUT_SECONDS", 5)
    out = tmp_path / "boot.json"
    code = boot_benchmark.main(
        ["--runs", "1", "--timeout", "2", "--json", str(out), "--", *fake_app]
    )
    assert code == 1
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["failures"] == ["1 run(s) never got ready"]
    assert "didn't answer within 2 s" in result["runs"][0]["error"]


def test_benchmark_limits():
    summary = boot_benchmark.summarize(
        [{"ready": 30.0}, {"ready": 2.0}, {"ready": 4.0}, {"ready": 3.0}]
    )
    assert (summary["cold"], summary["warm_median"]) == (30.0, 3.0)
    assert (summary["warm_min"], summary["warm_max"]) == (2.0, 4.0)
    assert boot_benchmark.check_limits(summary, 60, 20) == []
    assert boot_benchmark.check_limits(summary, 10, 2.5) == [
        "cold start 30.00 s > 10 s",
        "warm start median 3.00 s > 2.5 s",
    ]
    failed = boot_benchmark.summarize([{"ready": None}, {"ready": 1.0}])
    assert failed["cold"] is None and failed["failed_runs"] == 1
    assert boot_benchmark.check_limits(failed, None, None) == [
        "1 run(s) never got ready"
    ]


def test_benchmark_comparison(tmp_path, capsys, monkeypatch):
    summary = boot_benchmark.summarize([{"ready": 3.0}, {"ready": 1.0}])
    new = tmp_path / "new.json"
    new.write_text(json.dumps({"label": "0.2.0", "summary": summary}))
    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    assert boot_benchmark.main(["--compare", str(new), str(tmp_path / "x.json")]) == 0
    table = summary_path.read_text(encoding="utf-8")
    assert "| 0.2.0 | 3.00 s | 1.00 s | 1.00-1.00 s | 2 (0) |" in table
    assert "| x.json | no result | | | |" in table


# --- the e2e test's engine checks ---------------------------------------------------


def write_log(log_dir, *lines):
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "scoresight_2026-10-11_00-00-00.log").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def checks(result):
    return {check.name: check.passed for check in result.checks}


def test_the_e2e_test_needs_the_engine_in_the_log(tmp_path):
    result = e2e_test.Result()
    write_log(tmp_path / "a", "INFO - PP-OCR: loaded PP-OCRv6_small_rec (onnx...)")
    e2e_test.check_engine(result, tmp_path / "a", "ppocr")
    assert all(checks(result).values())
    assert result.details["ppocr_auto_switched_to_fast"] is False

    # PP-OCR fell back to Tesseract: a failure, even though Tesseract read
    result = e2e_test.Result()
    write_log(
        tmp_path / "b",
        "INFO - Setting OCR model to scoreboard_general from tesseract",
        "ERROR - PP-OCR (ppocr:small) couldn't load (x); reading the boxes with "
        "Tesseract",
    )
    e2e_test.check_engine(result, tmp_path / "b", "ppocr")
    assert not all(checks(result).values())

    result = e2e_test.Result()
    e2e_test.check_engine(result, tmp_path / "b", "tesseract")
    assert all(checks(result).values())

    result = e2e_test.Result()
    e2e_test.check_engine(result, tmp_path / "missing", "tesseract")
    assert not all(checks(result).values())


@pytest.mark.parametrize(
    ("engine", "stored", "passed"),
    [
        ("ppocr", "ppocr:auto", True),
        ("ppocr", 1, False),
        ("tesseract", 1, True),
        ("tesseract", "ppocr:auto", False),
    ],
)
def test_the_e2e_test_checks_the_stored_model(tmp_path, engine, stored, passed):
    settings = e2e_test.app_settings(Path("/v.avi"), Path("/out"), "http://x", engine)
    settings["ocr_model"] = stored
    e2e_test.settings_path(tmp_path).write_text(json.dumps(settings), encoding="utf-8")
    result = e2e_test.Result()
    e2e_test.check_settings(result, tmp_path, engine)
    name = f"the settings file has the OCR model {e2e_test.OCR_MODEL_PPOCR_DEFAULT!r}"
    if engine == "tesseract":
        name = f"the settings file has the OCR model {e2e_test.OCR_MODEL_GENERAL_SCOREBOARD!r}"
    assert checks(result)[name] is passed

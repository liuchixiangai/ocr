"""main.py: command-line options, translation lookup and --self-test."""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import main

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"


# --- command line ----------------------------------------------------------------


def test_defaults():
    args, qt_args = main.parse_args([])
    assert args.profile is None
    assert args.port == 18099
    assert not args.self_test
    assert qt_args == []


def test_options():
    args, qt_args = main.parse_args(
        ["--profile", "court2", "--port", "18100", "--self-test"]
    )
    assert (args.profile, args.port, args.self_test) == ("court2", 18100, True)
    assert qt_args == []


def test_qt_options_are_passed_through():
    args, qt_args = main.parse_args(
        ["-platform", "offscreen", "--port=18101", "-psn_0_12345"]
    )
    assert args.port == 18101
    assert qt_args == ["-platform", "offscreen", "-psn_0_12345"]


@pytest.mark.parametrize("port", ["0", "65536", "-1", "http", ""])
def test_invalid_port_is_rejected(port):
    with pytest.raises(SystemExit) as error:
        main.parse_args(["--port", port])
    assert error.value.code == 2


def test_empty_profile_is_rejected():
    with pytest.raises(SystemExit):
        main.parse_args(["--profile", "  "])


def test_main_applies_the_profile_before_starting_the_app(monkeypatch):
    monkeypatch.delenv("SCORESIGHT_PROFILE", raising=False)
    calls = []

    def fake_run_app(args, qt_args):
        calls.append((os.environ.get("SCORESIGHT_PROFILE"), args.port, qt_args))
        return 7

    monkeypatch.setattr(main, "run_app", fake_run_app)
    assert main.main(["--profile", "court2", "--port", "18100", "-reverse"]) == 7
    assert calls == [("court2", 18100, ["-reverse"])]


def test_main_without_profile_keeps_the_environment(monkeypatch):
    monkeypatch.setenv("SCORESIGHT_PROFILE", "from-env")
    monkeypatch.setattr(main, "run_app", lambda args, qt_args: 0)
    main.main([])
    assert os.environ["SCORESIGHT_PROFILE"] == "from-env"


def test_main_runs_the_self_test(monkeypatch):
    monkeypatch.setattr(main, "run_self_test", lambda: 3)
    monkeypatch.setattr(main, "run_app", lambda *a: pytest.fail("app started"))
    assert main.main(["--self-test"]) == 3


# --- translations ------------------------------------------------------------------


@pytest.fixture
def translations(tmp_path):
    for code in ["de_DE", "en_US", "fr_FR", "pt_BR", "pt_PT", "zh_CN", "ko_KR"]:
        (tmp_path / f"scoresight_{code}.qm").write_bytes(b"")
    (tmp_path / "scoresight_fr_FR.ts").write_text("not a .qm")
    return tmp_path


@pytest.mark.parametrize(
    ("locales", "expected"),
    [
        (["fr_FR"], "fr_FR"),
        (["de_AT"], "de_DE"),  # regional locale -> same language
        (["de-CH"], "de_DE"),  # QLocale.uiLanguages() uses dashes
        (["fr_CA"], "fr_FR"),
        (["pt_BR"], "pt_BR"),
        (["pt_AO"], "pt_PT"),  # prefer the language's home region
        (["ko"], "ko_KR"),
        (["en_GB"], "en_US"),
        (["xx_YY", "de_AT"], "de_DE"),  # first locale with a translation wins
        ([None, "", "fr_BE"], "fr_FR"),
        (["xx_YY"], None),
        (["C"], None),
    ],
)
def test_find_translation_file(translations, locales, expected):
    found = main.find_translation_file(locales, str(translations))
    if expected is None:
        assert found is None
    else:
        assert found == str(translations / f"scoresight_{expected}.qm")


def test_find_translation_file_without_folder(tmp_path):
    assert main.find_translation_file(["de_DE"], str(tmp_path / "missing")) is None


def test_bundled_translations_cover_regional_locales():
    folder = str(REPO_ROOT / "translations")
    for locale, expected in [
        ("de_AT", "de_DE"),
        ("es_MX", "es_ES"),
        ("fr_CA", "fr_FR"),
    ]:
        found = main.find_translation_file([locale], folder)
        assert found is not None and found.endswith(f"scoresight_{expected}.qm")


# --- self-test ------------------------------------------------------------------------


def test_self_test_imports_every_app_module():
    platform_specific = {"screen_capture_source_mac", "screen_capture_source_windows"}
    modules = {p.stem for p in SRC_DIR.glob("*.py")} - platform_specific - {"main"}
    assert set(main.SELF_TEST_MODULES) == modules


def test_self_test_reports_failure(monkeypatch, capsys):
    monkeypatch.setattr(main, "SELF_TEST_MODULES", ("no_such_scoresight_module",))
    assert main.run_self_test() == 1
    out = capsys.readouterr().out
    assert "SELF-TEST FAILED" in out
    assert "SELF-TEST OK" not in out


def _run_self_test(tmp_path, *args):
    env = dict(os.environ)
    env.update(
        QT_QPA_PLATFORM="offscreen",
        SCORESIGHT_DATA_DIR=str(tmp_path / "data"),
        SCORESIGHT_LOG_DIR=str(tmp_path / "logs"),
    )
    env.pop("SCORESIGHT_PROFILE", None)
    return subprocess.run(
        [sys.executable, str(SRC_DIR / "main.py"), "--self-test", *args],
        cwd=tmp_path,  # works from any working directory
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.mark.qt
@pytest.mark.ocr
def test_self_test_passes(tmp_path):
    process = _run_self_test(tmp_path, "--profile", "selftest")
    assert process.returncode == 0, process.stdout + process.stderr
    lines = process.stdout.splitlines()
    assert lines[-1] == "SELF-TEST OK"
    assert "eng='45'" in process.stdout
    # --profile is applied before the settings are touched
    settings = [line for line in lines if line.startswith("settings file: ")]
    assert settings == [
        f"settings file: {tmp_path / 'data' / 'scoresight-selftest.json'}"
    ]
    # the self-test only reads settings
    assert not (tmp_path / "data").exists() or not any((tmp_path / "data").iterdir())


@pytest.mark.ocr
@pytest.mark.parametrize("broken", ["daktronics", "scoreboard_general_large"])
def test_self_test_fails_when_a_bundled_model_does_not_load(monkeypatch, broken):
    # setOcrModel keeps the previous model when one fails to load: reading with
    # that one must not pass for the broken model
    import tesseract

    real_api = tesseract.PyTessBaseAPI

    def api(*args, lang=None, **kwargs):
        if lang == broken:
            raise RuntimeError("Failed to init API, possibly an invalid tessdata path")
        return real_api(*args, lang=lang, **kwargs)

    monkeypatch.setattr(tesseract, "PyTessBaseAPI", api)
    with pytest.raises(RuntimeError, match=f"the OCR model '{broken}' did not load"):
        main._self_test_ocr()


# --- shutdown on signals ----------------------------------------------------------


@pytest.mark.qt
def test_quit_signals_close_the_main_window(qapp, monkeypatch):
    # Ctrl+C (or SIGTERM) used to do nothing while Qt's event loop ran, and an
    # offscreen app has no window to close: the process could only be killed,
    # skipping the shutdown (boxes saved, threads and servers stopped).
    import signal

    from PySide6.QtWidgets import QWidget

    installed = {}
    monkeypatch.setattr(
        signal, "signal", lambda s, handler: installed.update({s: handler})
    )
    window = QWidget()
    window.show()
    timer = main.install_quit_signal_handlers(qapp, window)
    try:
        assert {signal.SIGINT, signal.SIGTERM} <= set(installed)
        # the interpreter gets control regularly to run the handlers
        assert timer.isActive() and timer.interval() <= 500
        installed[signal.SIGINT](signal.SIGINT, None)
        assert window.isVisible()  # closed from the event loop, not the handler
        qapp.processEvents()
        assert not window.isVisible()
    finally:
        timer.stop()
        timer.deleteLater()
        window.deleteLater()


@pytest.mark.qt
def test_quit_signals_end_the_app_with_a_dialog_open(tmp_path):
    # A dialog without a parent (Video Settings, Connect to OBS) is a window of
    # its own: closing the main window didn't end the event loop, so the process
    # kept running (with the HTTP server up) after Ctrl+C or SIGTERM. Run in a
    # process of its own: quitting the event loop affects the whole QApplication.
    script = textwrap.dedent(
        """
        import signal
        import sys

        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QDialog, QWidget

        import main

        app = QApplication([])
        window = QWidget()
        window.show()
        dialog = QDialog()  # no parent, like Connect to OBS
        dialog.show()
        main.install_quit_signal_handlers(app, window)
        QTimer.singleShot(100, lambda: signal.raise_signal(signal.SIGINT))
        QTimer.singleShot(10000, lambda: app.exit(3))  # still running: give up
        code = app.exec()
        print("closed" if not window.isVisible() else "visible")
        sys.exit(code)
        """
    )
    env = dict(os.environ)
    env.update(
        QT_QPA_PLATFORM="offscreen",
        PYTHONPATH=str(SRC_DIR),
        SCORESIGHT_DATA_DIR=str(tmp_path / "data"),
        SCORESIGHT_LOG_DIR=str(tmp_path / "logs"),
    )
    process = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert process.stdout.split() == ["closed"]


class FakeApp:
    def __init__(self, platform_name):
        self._platform_name = platform_name

    def platformName(self):
        return self._platform_name


def test_a_visible_window_does_not_attach_to_the_console(monkeypatch):
    import ctypes

    def no_console_calls(*args):
        pytest.fail("the console API was used")

    monkeypatch.setattr(ctypes, "WinDLL", no_console_calls, raising=False)
    assert main.attach_console_for_quit_signals(FakeApp("windows")) is False
    assert main.attach_console_for_quit_signals(FakeApp("cocoa")) is False


@pytest.mark.skipif(sys.platform == "win32", reason="consoles are a Windows thing")
def test_only_windows_attaches_to_the_console():
    assert main.attach_console_for_quit_signals(FakeApp("offscreen")) is False


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows console API")
def test_the_attached_console_forwards_ctrl_break_to_the_signal_handlers(
    monkeypatch,
):
    # The windowed build had no console: Ctrl+Break never reached a headless
    # instance. Attached to its parent's console, the C runtime's handler is
    # gone (Ctrl+Break ended the process with STATUS_CONTROL_C_EXIT, skipping
    # the shutdown), so the app forwards the events to its signal handlers.
    import ctypes
    import signal

    calls = {}

    class FakeKernel32:
        def AttachConsole(self, process_id):
            calls["attach"] = process_id
            return 1

        def SetConsoleCtrlHandler(self, handler, add):
            calls["handler"] = (handler, add)
            return 1

    monkeypatch.setattr(ctypes, "WinDLL", lambda name: FakeKernel32())
    monkeypatch.setattr(main, "_console_ctrl_handlers", [])
    received = []
    previous = signal.signal(
        signal.SIGBREAK, lambda signum, frame: received.append(signum)
    )
    try:
        assert main.attach_console_for_quit_signals(FakeApp("offscreen")) is True
        assert calls["attach"] == -1  # ATTACH_PARENT_PROCESS
        handler, add = calls["handler"]
        assert add is True and main._console_ctrl_handlers == [handler]
        assert handler(1)  # CTRL_BREAK_EVENT: handled
        for _ in range(100):  # the handler runs on the main thread, soon
            if received:
                break
            time.sleep(0.01)
        assert received == [signal.SIGBREAK]
        assert not handler(2)  # CTRL_CLOSE_EVENT: the default handling
    finally:
        signal.signal(signal.SIGBREAK, previous)


def test_no_console_to_attach_to(monkeypatch):
    import ctypes

    class FakeKernel32:
        def AttachConsole(self, process_id):
            return 0  # the process has a console, or its parent has none

        def SetConsoleCtrlHandler(self, handler, add):
            pytest.fail("a handler was registered")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda name: FakeKernel32(), raising=False)
    assert main.attach_console_for_quit_signals(FakeApp("offscreen")) is False


# --- startup timing -----------------------------------------------------------------


def test_main_marks_the_parsed_arguments(timing, monkeypatch):
    monkeypatch.setattr(main, "run_app", lambda args, qt_args: 0)
    main.main([])
    assert timing.elapsed("args_parsed") is not None


@pytest.mark.qt
def test_startup_timing_is_reported_once_the_sources_are_discovered(
    qapp, qtbot, timing, monkeypatch
):
    monkeypatch.setattr(main, "STARTUP_REPORT_WAIT_S", 30.0)
    timer = main.report_startup_when_ready(qapp)
    try:
        qtbot.waitUntil(lambda: timing.elapsed("event_loop_idle") is not None)
        assert not timing._finished  # waiting for the background work
        timing.mark("http_server_ready")
        timing.mark("sources_discovered")
        qtbot.waitUntil(lambda: timing._finished, timeout=2000)
    finally:
        timer.stop()


@pytest.mark.qt
def test_startup_timing_does_not_wait_long_for_the_sources(
    qapp, qtbot, timing, monkeypatch
):
    monkeypatch.setattr(main, "STARTUP_REPORT_WAIT_S", 0.2)
    timer = main.report_startup_when_ready(qapp)
    try:
        qtbot.waitUntil(lambda: timing._finished, timeout=3000)
        assert timing.elapsed("sources_discovered") is None
    finally:
        timer.stop()


@pytest.mark.qt
def test_startup_timing_is_reported_right_away_when_ready(qapp, qtbot, timing):
    timing.mark("http_server_ready")
    timing.mark("sources_discovered")
    timer = main.report_startup_when_ready(qapp)
    qtbot.waitUntil(lambda: timing._finished, timeout=2000)
    assert not timer.isActive()


# --- splash screen ----------------------------------------------------------------


def test_splash_screen_is_closed_only_when_the_bootloader_started_one(monkeypatch):
    # Only the Windows build has a splash screen. Importing PyInstaller's
    # pyi_splash (bundled in every build) without one printed a warning with a
    # KeyError traceback on every start of the Linux build.
    import types

    closed = []

    def close():
        closed.append(True)

    fake = types.ModuleType("pyi_splash")
    fake.close = close
    monkeypatch.setitem(sys.modules, "pyi_splash", fake)
    monkeypatch.delenv("_PYI_SPLASH_IPC", raising=False)
    main.close_splash_screen()
    assert closed == []

    monkeypatch.setenv("_PYI_SPLASH_IPC", "52123")
    main.close_splash_screen()
    assert closed == [True]

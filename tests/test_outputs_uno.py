import json
import logging
import threading
import time
from types import SimpleNamespace

import pytest

import storage
from output_worker import HTTP_TIMEOUT
from storage import fetch_data, store_data
from uno_output import UNOAPI

SETTINGS = "scoresight.json"
URL = "https://app.overlays.uno/apiv2/controlapps/abc/api"


class FakeResponse:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.text = ""
        self.headers = headers or {}


class FakeSession:
    def __init__(self, status_code=200, delay=0.0, responses=(), headers=None):
        self.status_code = status_code
        self.delay = delay
        self.headers = headers or {}
        # responses to give first, in order (then status_code)
        self.responses = list(responses)
        self.calls = []
        self.lock = threading.Lock()

    def put(self, url, json=None, timeout=None):
        with self.lock:
            self.calls.append(
                {"url": url, "json": json, "timeout": timeout, "at": time.monotonic()}
            )
            response = (
                self.responses.pop(0)
                if self.responses
                else FakeResponse(self.status_code, self.headers)
            )
        if self.delay:
            time.sleep(self.delay)
        return response


@pytest.fixture
def make_uno():
    created = []

    def _make(endpoint=URL, mapping=None, session=None):
        api = UNOAPI(endpoint, mapping or {})
        api._session = session or FakeSession()
        created.append(api)
        return api

    yield _make
    for api in created:
        api.close()


def flush(api):
    assert api._worker.wait_idle(10)


def test_sends_put_with_timeout(make_uno, make_result):
    api = make_uno(mapping={"Home Score": "SetGoalsHome", "Time": "SetMatchTime"})
    api.start()
    api.update_uno(
        [
            make_result("Home Score", "3", "Success"),
            make_result("Time", "12:00", "SameNoChange"),
            make_result("Away Score", "1", "Success"),  # not mapped
        ]
    )
    flush(api)
    [call] = api._session.calls
    del call["at"]
    assert call == {
        "url": URL,
        "json": {"command": "SetGoalsHome", "value": "3"},
        "timeout": HTTP_TIMEOUT,
    }


def test_unchanged_values_are_sent_on_request(make_uno, make_result):
    # after the outputs were paused (see MainWindow.ocrResult)
    api = make_uno(mapping={"Time": "SetMatchTime", "Home Score": "SetGoalsHome"})
    api.start()
    results = [
        make_result("Time", "12:00", "SameNoChange"),
        make_result("Home Score", "3", "SameNoChange"),  # not asked for
    ]
    api.update_uno(results, resend_unchanged={"Time"})
    api.update_uno(results)
    flush(api)
    assert [call["json"] for call in api._session.calls] == [
        {"command": "SetMatchTime", "value": "12:00"}
    ]


def test_essentials_payload(make_uno, make_result):
    store_data(SETTINGS, "uno_essentials", True)
    store_data(SETTINGS, "uno_essentials_id", "overlay-1")
    api = make_uno(mapping={"Time": "clock"})
    api.start()
    api.update_uno([make_result("Time", "1:00", "Success")])
    flush(api)
    assert api._session.calls[0]["json"] == {
        "command": "SetOverlayContentField",
        "value": "1:00",
        "fieldId": "clock",
        "id": "overlay-1",
    }


def test_update_never_blocks_and_keeps_latest_per_command(make_uno, make_result):
    session = FakeSession(delay=0.25)
    api = make_uno(mapping={"Time": "SetMatchTime", "Home Score": "SetGoalsHome"})
    api._session = session
    api.start()

    start = time.monotonic()
    api.update_uno([make_result("Home Score", "3", "Success")])
    for second in range(59, -1, -1):
        api.update_uno([make_result("Time", f"0:{second:02}", "Success")])
    assert time.monotonic() - start < 1.0  # blocking would take 15 s+

    flush(api)
    sent = [(c["json"]["command"], c["json"]["value"]) for c in session.calls]
    assert ("SetGoalsHome", "3") in sent  # an older change is never dropped
    assert sent[-1] == ("SetMatchTime", "0:00")
    assert len(sent) <= 10  # of 61 submitted (typically 3)


@pytest.mark.parametrize("status", [200, 201, 204])
def test_any_2xx_is_success(make_uno, make_result, caplog, status):
    api = make_uno(mapping={"Time": "SetMatchTime"}, session=FakeSession(status))
    api.start()
    with caplog.at_level(logging.ERROR):
        api.update_uno([make_result("Time", "1:00", "Success")])
        flush(api)
    assert caplog.records == []


def test_error_status_is_logged(make_uno, make_result, caplog):
    api = make_uno(mapping={"Time": "SetMatchTime"}, session=FakeSession(500))
    api.start()
    with caplog.at_level(logging.ERROR):
        api.update_uno([make_result("Time", "1:00", "Success")])
        flush(api)
    assert "returned HTTP 500" in caplog.text


def wait_for(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def uno_destination(api):
    import output_status

    return output_status.uno_status(
        api.running, api.endpoint, api.field_mapping, api.status(), api.quota()
    )


def test_status_of_the_rate_limit_and_a_new_url(make_uno, make_result, caplog):
    session = FakeSession(responses=[FakeResponse(429, {"Retry-After": "120"})])
    api = make_uno(mapping={"Time": "SetMatchTime"}, session=session)
    api.start()
    with caplog.at_level(logging.ERROR):
        api.update_uno([make_result("Time", "1:00", "Success")])
        wait_for(lambda: "returned HTTP 429" in caplog.text)
    assert api.status().retrying
    status = uno_destination(api)
    assert (status.state, status.detail) == (
        "retrying",
        "HTTP 429: overlays.uno's call limit reached · retrying",
    )
    assert "Also resend unchanged values" in status.hint
    assert "Next try in 2 min." in status.hint
    assert "abc" not in status.technical  # the URL's token
    # kept for the retry, not dropped
    assert api._worker.pending() == {
        "SetMatchTime": {"command": "SetMatchTime", "value": "1:00"}
    }

    # another URL: the status starts over, and the kept value goes there now
    # instead of waiting out the old URL's limit
    other = "https://app.overlays.uno/apiv2/controlapps/other/api"
    api.set_endpoint(other)
    assert uno_destination(api).state in ("sending", "ok")
    flush(api)
    assert session.calls[-1]["url"] == other
    assert uno_destination(api).state == "ok"
    api.stop()
    assert uno_destination(api).state == "off"


def test_a_value_refused_with_429_is_sent_after_the_wait(make_uno, make_result):
    # a 429 used to drop the value: with "update on change" the score stayed
    # wrong on air until it changed again
    session = FakeSession(
        responses=[FakeResponse(429, {"Retry-After": "0.3"})],
    )
    api = make_uno(
        mapping={"Home Score": "SetGoalsHome", "Time": "SetMatchTime"},
        session=session,
    )
    api.start()
    api.update_uno([make_result("Home Score", "3", "Success")])
    wait_for(lambda: api.status().retrying)
    # newer values arrive meanwhile: the latest of each field is sent
    api.update_uno([make_result("Home Score", "4", "Success")])
    api.update_uno([make_result("Time", "9:59", "Success")])
    flush(api)
    sent = [(c["json"]["command"], c["json"]["value"]) for c in session.calls]
    assert sent == [
        ("SetGoalsHome", "3"),
        ("SetGoalsHome", "4"),
        ("SetMatchTime", "9:59"),
    ]
    assert session.calls[1]["at"] - session.calls[0]["at"] >= 0.25
    status = api.status()
    assert not status.retrying and status.failing_keys == 0
    assert uno_destination(api).state == "ok"


def test_without_retry_after_the_wait_comes_from_the_limit_headers(
    make_uno, make_result
):
    reset = time.time() + 0.9
    used_up = {
        "X-Singular-Ratelimit-Daily-Calls": json.dumps(
            {"limit": 10000, "remaining": 0, "reset": reset}
        )
    }
    session = FakeSession(responses=[FakeResponse(429, used_up)])
    api = make_uno(mapping={"Time": "SetMatchTime"}, session=session)
    api.start()
    api.update_uno([make_result("Time", "1:00", "Success")])
    wait_for(lambda: api.status().retrying)
    # (more than the worker's first backoff, 0.5 s)
    assert 0.6 < api.status().retry_delay <= 0.9
    flush(api)
    assert len(session.calls) == 2
    assert session.calls[1]["at"] - session.calls[0]["at"] >= 0.6


def test_an_answer_from_the_previous_url_does_not_set_the_limits(make_uno):
    api = make_uno(endpoint=URL)
    headers = {
        "X-Singular-Ratelimit-Burst-Calls": json.dumps({"limit": 60, "remaining": 1})
    }
    api.set_endpoint("https://app.overlays.uno/apiv2/controlapps/other/api")
    api.check_rate_limits(headers, URL)  # in flight while the URL changed
    assert api.quota().burst is None and api._worker.min_interval == 0
    api.check_rate_limits(headers, api.endpoint)
    assert api.quota().burst.limit == 60 and api._worker.min_interval == 1.0


def test_fields_without_a_command_are_mentioned(make_uno, make_result):
    import output_status

    status = output_status.uno_status(
        True, URL, {"Time": "SetMatchTime", "Logo": "", "Shot Clock": ""}, None
    )
    assert status.state == "sending"
    assert status.hint.startswith("2 field(s) have no UNO command")


def test_calls_are_spaced_out_to_the_burst_limit(make_uno, make_result):
    # 600 calls a minute: at least 0.1 s between calls
    headers = {
        "X-Singular-Ratelimit-Burst-Calls": json.dumps(
            {"limit": 600, "remaining": 599, "reset": time.time() + 60}
        ),
        "X-Singular-Ratelimit-Daily-Calls": json.dumps(
            {"limit": 10000, "remaining": 9512, "reset": time.time() + 3600}
        ),
    }
    session = FakeSession(headers=headers)
    mapping = {f"Box {i}": f"SetThing{i}" for i in range(4)}
    api = make_uno(mapping=mapping, session=session)
    api.start()
    api.update_uno([make_result("Box 0", "x", "Success")])
    flush(api)
    assert api._worker.min_interval == pytest.approx(0.1)
    api.update_uno([make_result(f"Box {i}", "y", "Success") for i in range(4)])
    flush(api)
    times = [call["at"] for call in session.calls[1:]]
    assert len(times) == 4
    assert min(b - a for a, b in zip(times, times[1:])) >= 0.09

    quota = api.quota()
    assert (quota.daily.limit, quota.daily.remaining) == (10000, 9512)
    status = uno_destination(api)
    assert status.state == "ok"
    assert status.detail == "Sending to app.overlays.uno · 9512 calls left today"


def test_few_daily_calls_left_is_a_warning(make_uno, make_result):
    headers = {
        "X-Singular-Ratelimit-Daily-Calls": json.dumps(
            {"limit": 10000, "remaining": 312, "reset": time.time() + 3600}
        )
    }
    api = make_uno(
        mapping={"Time": "SetMatchTime"}, session=FakeSession(headers=headers)
    )
    api.start()
    api.update_uno([make_result("Time", "1:00", "Success")])
    flush(api)
    status = uno_destination(api)
    assert (status.state, status.detail) == (
        "warning",
        "Only 312 overlays.uno calls left today",
    )
    # another URL (maybe another account): the limits are forgotten
    api.set_endpoint("https://app.overlays.uno/apiv2/controlapps/other/api")
    assert api.quota().daily is None
    assert api._worker.min_interval == 0


def test_unknown_command_is_explained(make_uno, make_result):
    api = make_uno(mapping={"Shot Clock": "Shot Clock"}, session=FakeSession(400))
    api.start()
    api.update_uno([make_result("Shot Clock", "24", "Success")])
    flush(api)
    status = uno_destination(api)
    assert (status.state, status.detail) == (
        "error",
        "HTTP 400: overlays.uno doesn't know a command",
    )
    assert "daily limit" in status.hint


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        ("not json", None),
        ("[1, 2]", None),
        ("{}", None),
        ('{"limit": 100, "remaining": 97, "reset": 2000000000}', (100, 97, 2e9)),
        ('{"Limit": "100", "Remaining": "3"}', (100, 3, None)),
        ('{"limit": 100, "used": 40, "resetIn": 30}', (100, 60, 1030.0)),
        ('{"max": 50, "current": 50, "reset_at": 2000000000000}', (50, 0, 2e9)),
        ('{"remaining": 5, "resetAt": "2033-05-18T03:33:20Z"}', (None, 5, 2e9)),
    ],
)
def test_parse_rate_window(value, expected):
    from uno_output import parse_rate_window

    window = parse_rate_window(value, now=1000.0)
    if expected is None:
        assert window is None
    else:
        assert (window.limit, window.remaining, window.reset_at) == expected


def test_429_from_a_real_server_is_retried(make_uno, make_result):
    # through requests, with real (case-insensitive) response headers
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_PUT(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(body)
            refuse = len(received) == 1
            self.send_response(429 if refuse else 200)
            self.send_header("retry-after", "0.2")
            self.send_header(
                "x-singular-ratelimit-daily-calls",
                json.dumps({"limit": 10000, "remaining": 9000 - len(received)}),
            )
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        import requests

        with requests.Session() as session:
            api = make_uno(
                endpoint=f"http://127.0.0.1:{server.server_address[1]}/api",
                mapping={"Home Score": "SetGoalsHome"},
                session=session,
            )
            api.start()
            api.update_uno([make_result("Home Score", "21", "Success")])
            flush(api)
    finally:
        server.shutdown()
        server.server_close()
    assert [json.loads(body)["value"] for body in received] == ["21", "21"]
    assert api.quota().daily.remaining == 8998


def test_set_endpoint_keeps_mapping_and_running_state(make_uno, make_result):
    api = make_uno(mapping={"Time": "SetMatchTime"})
    api.start()
    api.set_endpoint("https://example.test/api")
    assert api.running
    assert api.field_mapping == {"Time": "SetMatchTime"}
    api.update_uno([make_result("Time", "1:00", "Success")])
    flush(api)
    assert api._session.calls[0]["url"] == "https://example.test/api"


def test_close_unsubscribes_from_settings(make_uno):
    api = make_uno()
    api.close()
    subscribers = storage.data_subscribers.get(SETTINGS, {})
    for name in ("uno_send_same", "uno_essentials", "uno_essentials_id"):
        assert name not in subscribers


# --- the UNO tab -------------------------------------------------------------


@pytest.fixture
def uno_ui(qapp):
    from PySide6.QtWidgets import (
        QCheckBox,
        QLineEdit,
        QTableView,
        QToolButton,
        QWidget,
    )

    ui = SimpleNamespace(
        lineEdit_unoUrl=QLineEdit(),
        toolButton_toggleUno=QToolButton(),
        tableView_unoMapping=QTableView(),
        checkBox_uno_send_same=QCheckBox(),
        checkBox_uno_essentials=QCheckBox(),
        widget_uno_essentials_details=QWidget(),
        lineEdit_uno_essentials_id=QLineEdit(),
    )
    ui.toolButton_toggleUno.setCheckable(True)
    return ui


@pytest.fixture
def handler(uno_ui):
    from uno_ui_handler import UNOUIHandler

    store_data(SETTINGS, "uno_url", URL)
    handler = UNOUIHandler(uno_ui)
    handler.unoUpdater._session = FakeSession()
    yield handler
    handler.unoUpdater.close()


@pytest.mark.qt
def test_url_edit_keeps_running_state_and_mapping(handler, uno_ui, make_target):
    updater = handler.unoUpdater
    handler.updateUNOTable([make_target("Home Score"), make_target("Time")])
    uno_ui.toolButton_toggleUno.setChecked(True)
    assert updater.running

    uno_ui.lineEdit_unoUrl.setText("https://example.test/new ")
    uno_ui.lineEdit_unoUrl.editingFinished.emit()

    assert handler.unoUpdater is updater
    assert updater.running  # used to be reset while the button showed "stop"
    assert uno_ui.toolButton_toggleUno.isChecked()
    assert updater.endpoint == "https://example.test/new"
    assert updater.field_mapping == {
        "Home Score": "SetGoalsHome",
        "Time": "SetMatchTime",
    }
    assert fetch_data(SETTINGS, "uno_url") == "https://example.test/new"


@pytest.mark.qt
def test_only_standard_boxes_get_a_command_by_default(
    handler, uno_ui, make_target, make_result
):
    # a custom box used to get a command named after itself, which templates
    # answer with HTTP 400 while the call still counts against the quota
    handler.updateUNOTable(
        [make_target("Home Score"), make_target("Shot Clock"), make_target("Logo")]
    )
    model = uno_ui.tableView_unoMapping.model()
    shown = {model.item(i, 0).text(): model.item(i, 1).text() for i in range(3)}
    assert shown == {"Home Score": "SetGoalsHome", "Shot Clock": "", "Logo": ""}

    updater = handler.unoUpdater
    updater.start()
    updater.update_uno(
        [
            make_result("Home Score", "2", "Success"),
            make_result("Shot Clock", "24", "Success"),
            make_result("Logo", "x", "Success"),
        ]
    )
    flush(updater)
    assert [c["json"]["command"] for c in updater._session.calls] == ["SetGoalsHome"]

    # an explicit mapping still applies
    model.item(1, 1).setText("SetShotClock")
    assert updater.field_mapping["Shot Clock"] == "SetShotClock"


@pytest.mark.qt
def test_typing_alone_does_not_reconfigure(handler, uno_ui):
    uno_ui.lineEdit_unoUrl.setText("https://exa")
    assert handler.unoUpdater.endpoint == URL
    assert fetch_data(SETTINGS, "uno_url") == URL


@pytest.mark.qt
def test_editing_a_mapping_cell_stores_and_applies_it(handler, uno_ui, make_target):
    handler.updateUNOTable([make_target("Time")])
    assert fetch_data(SETTINGS, "uno_mapping") is None
    uno_ui.tableView_unoMapping.model().item(0, 1).setText("SetClock")
    assert fetch_data(SETTINGS, "uno_mapping") == {"Time": "SetClock"}
    assert handler.unoUpdater.field_mapping == {"Time": "SetClock"}


@pytest.mark.qt
def test_restore_ui_state(handler, uno_ui):
    uno_ui.toolButton_toggleUno.setChecked(True)
    uno_ui.lineEdit_unoUrl.setText("https://app.overlays.uno/apiv2/controlapps/.../api")
    uno_ui.toolButton_toggleUno.setText("x")

    handler.restoreUiState()
    assert uno_ui.lineEdit_unoUrl.text() == URL
    # a text and an icon, no emoji (#M4)
    assert uno_ui.toolButton_toggleUno.text() == "Stop sending"
    assert not uno_ui.toolButton_toggleUno.icon().isNull()
    assert handler.unoUpdater.running
    uno_ui.toolButton_toggleUno.setChecked(False)
    assert uno_ui.toolButton_toggleUno.text() == "Start sending"

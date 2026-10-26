"""The outbound HTTP API's options (Only changes, Resend all, Fields per request,
{field}/{value} URLs, Rate, safe names, Headers, Digest logins) and its
presets (TriCaster DataLink, Epiphan Pearl), against a real HTTP server."""

import base64
import hashlib
import json
import logging
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import pytest
import requests

import api_output
from api_output import ENCODING_JSON_FULL, ENCODING_JSON_SIMPLE

# --- a receiver that records what arrives -------------------------------------------


class Receiver:
    """A local HTTP server that records every request (the raw path, so the
    exact encoding can be checked) and can ask for a Basic or Digest login."""

    def __init__(self):
        self.requests = []  # dicts: method, path, headers, body, at
        self.lock = threading.Lock()
        self.status = 200
        self.login = None  # ("basic" | "digest", user, password)
        self.nonce = "dcd98b7102dd2f0e8b11d0f600bfb0c093"
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                entry = {
                    "method": self.command,
                    "path": self.path,
                    "headers": dict(self.headers.items()),
                    "body": body,
                    "at": time.monotonic(),
                }
                with receiver.lock:
                    receiver.requests.append(entry)
                status, extra = receiver.answer(self.command, self.path, self.headers)
                entry["status"] = status
                self.send_response(status)
                for name, value in extra.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST = do_PUT = _handle

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, path_and_query, login=""):
        at = f"{login}@" if login else ""
        return f"http://{at}127.0.0.1:{self.port}{path_and_query}"

    def answer(self, method, path, headers):
        if self.login is None:
            return self.status, {}
        kind, user, password = self.login
        auth = headers.get("Authorization") or ""
        if kind == "basic":
            expected = base64.b64encode(f"{user}:{password}".encode()).decode()
            if auth == f"Basic {expected}":
                return self.status, {}
            return 401, {"WWW-Authenticate": 'Basic realm="pearl"'}
        # Digest (RFC 2617, qop=auth, MD5)
        challenge = {
            "WWW-Authenticate": f'Digest realm="tc", nonce="{self.nonce}", '
            'qop="auth", algorithm=MD5'
        }
        if not auth.startswith("Digest "):
            return 401, challenge
        fields = dict(re.findall(r'(\w+)="?([^",]*)"?', auth[len("Digest ") :]))

        def md5(text):
            return hashlib.md5(text.encode()).hexdigest()

        ha1 = md5(f"{user}:tc:{password}")
        ha2 = md5(f"{method}:{fields.get('uri')}")
        expected = md5(
            f"{ha1}:{self.nonce}:{fields.get('nc')}:{fields.get('cnonce')}:auth:{ha2}"
        )
        if fields.get("username") == user and fields.get("response") == expected:
            return self.status, {}
        return 401, challenge

    def accepted(self):
        """The requests that got a 2xx answer."""
        with self.lock:
            return [r for r in self.requests if 200 <= r.get("status", 0) < 300]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def receiver():
    server = Receiver()
    yield server
    server.close()


@pytest.fixture
def configure(monkeypatch):
    """Set the outbound API's settings (as their subscriptions would)."""
    monkeypatch.setattr(api_output, "_session", requests.Session())
    monkeypatch.setattr(api_output, "_settings_log", api_output.ThrottledErrorLog("x"))

    def _configure(
        url,
        method="GET",
        encoding=ENCODING_JSON_SIMPLE,
        only_changes=False,
        refresh_s=0,
        fields_per_request=0,
        max_rate=0,
        safe_names=False,
        headers="",
    ):
        settings = {
            "url": url,
            "method": method,
            "encoding": encoding,
            "only_changes": only_changes,
            "refresh_s": refresh_s,
            "fields_per_request": fields_per_request,
            "max_rate": max_rate,
            "safe_names": safe_names,
            "headers": headers,
        }
        for name, value in settings.items():
            monkeypatch.setattr(api_output, f"out_api_{name}", value)
        api_output._restart()

    yield _configure
    worker = api_output._worker
    worker.clear()
    assert worker.wait_idle(10)
    worker.max_batch = None
    worker.min_interval = 0.0
    worker._retry_failures = 0
    worker.error_log.reset()
    api_output._forget_sent()
    api_output._last_refresh_at = None
    api_output._digest_auths.clear()


def cycle(*readings, make_result):
    api_output.update_out_api([make_result(*reading) for reading in readings])
    assert api_output._worker.wait_idle(10)


@pytest.fixture
def send(make_result):
    def _send(*readings):
        cycle(*readings, make_result=make_result)

    return _send


def query(entry):
    return parse_qsl(urlsplit(entry["path"]).query, keep_blank_values=True)


# --- defaults --------------------------------------------------------------------


def test_defaults_send_every_cycle(receiver, configure, send):
    # the 0.2.0 behavior: a request per detection cycle, changed or not
    configure(receiver.url("/scores"), method="POST")
    for _ in range(3):
        send(("Home Score", "12", "SameNoChange"))
    assert len(receiver.accepted()) == 3
    assert json.loads(receiver.accepted()[0]["body"]) == {"Home Score": "12"}


# --- Only changes -------------------------------------------------------------------


def test_only_changes_sends_a_cycle_when_a_value_changed(receiver, configure, send):
    configure(receiver.url("/scores"), method="POST", only_changes=True)
    send(("Home Score", "12", "Success"), ("Time", "5:00", "Success"))
    send(("Home Score", "12", "SameNoChange"), ("Time", "5:00", "SameNoChange"))
    send(("Home Score", "12", "SameNoChange"), ("Time", "X", "FailedFilter"))
    assert len(receiver.accepted()) == 1
    send(("Home Score", "13", "Success"), ("Time", "5:00", "SameNoChange"))
    bodies = [json.loads(r["body"]) for r in receiver.accepted()]
    assert bodies == [
        {"Home Score": "12", "Time": "5:00"},
        {"Home Score": "13", "Time": "5:00"},
    ]


def test_only_changes_sends_again_until_it_arrives(receiver, configure, send):
    configure(receiver.url("/scores"), method="POST", only_changes=True)
    receiver.status = 500  # refused: not sent
    send(("Home Score", "12", "Success"))
    receiver.status = 200
    send(("Home Score", "12", "SameNoChange"))
    send(("Home Score", "12", "SameNoChange"))
    assert len(receiver.requests) == 2
    assert len(receiver.accepted()) == 1


def test_only_changes_per_field_sends_the_changed_fields(receiver, configure, send):
    configure(
        receiver.url("/set?key={field}&value={value}"),
        only_changes=True,
    )
    send(("Home Score", "12", "Success"), ("Away Score", "7", "Success"))
    send(("Home Score", "12", "SameNoChange"), ("Away Score", "8", "Success"))
    paths = [r["path"] for r in receiver.accepted()]
    assert paths == [
        "/set?key=Home%20Score&value=12",
        "/set?key=Away%20Score&value=7",
        "/set?key=Away%20Score&value=8",
    ]


def test_resend_all_sends_every_value_again(receiver, configure, send):
    configure(
        receiver.url("/set?key={field}&value={value}"),
        only_changes=True,
        refresh_s=1,
    )
    send(("Home Score", "12", "Success"))
    send(("Home Score", "12", "SameNoChange"))
    assert len(receiver.accepted()) == 1
    time.sleep(1.05)
    send(("Home Score", "12", "SameNoChange"))
    assert len(receiver.accepted()) == 2


def test_everything_is_sent_again_after_the_receiver_was_unreachable(
    receiver, configure, send, monkeypatch
):
    # an Epiphan Pearl forgets its variables when it restarts
    monkeypatch.setattr(api_output._worker, "_initial_backoff", 0.01)
    monkeypatch.setattr(api_output._worker, "_max_backoff", 0.01)
    configure(receiver.url("/set?key={field}&value={value}"), only_changes=True)
    send(("Home Score", "12", "Success"), ("Time", "5:00", "Success"))
    assert len(receiver.accepted()) == 2
    api_output._forget_sent()  # what a refused connection does
    send(("Home Score", "12", "SameNoChange"), ("Time", "5:00", "SameNoChange"))
    assert len(receiver.accepted()) == 4


def test_a_request_in_flight_during_a_settings_change_is_not_counted_as_sent(
    receiver, configure, send, make_result, monkeypatch
):
    # it was sent to the old URL: the new one must still get the value
    gate = threading.Event()
    real_request = api_output._session.request

    def slow_request(*args, **kwargs):
        assert gate.wait(5)
        return real_request(*args, **kwargs)

    configure(receiver.url("/old?key={field}&value={value}"), only_changes=True)
    monkeypatch.setattr(api_output._session, "request", slow_request)
    api_output.update_out_api([make_result("Home Score", "21", "Success")])
    time.sleep(0.1)  # in flight, waiting for the gate
    # the URL changes (as its subscription does)
    monkeypatch.setattr(
        api_output, "out_api_url", receiver.url("/new?key={field}&value={value}")
    )
    api_output._restart()
    gate.set()
    assert api_output._worker.wait_idle(10)
    assert api_output._sent_fields == {}
    send(("Home Score", "21", "SameNoChange"))
    assert [r["path"] for r in receiver.accepted()] == [
        "/old?key=Home%20Score&value=21",
        "/new?key=Home%20Score&value=21",
    ]


def test_a_refused_connection_forgets_what_was_sent(configure, make_result):
    configure("http://127.0.0.1:9/set?key={field}&value={value}", only_changes=True)
    api_output._sent_fields["Home Score"] = "12"
    with pytest.raises(requests.ConnectionError):
        api_output._send_request(
            api_output.ApiRequest("GET", "http://127.0.0.1:9/", {}, None)
        )
    assert api_output._sent_fields == {}


# --- Headers ------------------------------------------------------------------------


def test_custom_headers_are_sent(receiver, configure, send, caplog):
    configure(
        receiver.url("/scores"),
        method="POST",
        headers="Authorization: Bearer s3cret\n\nX-Api-Key:  k1 \nnot a header s3cret",
    )
    with caplog.at_level(logging.ERROR):
        send(("Home Score", "12", "Success"))
    [entry] = receiver.accepted()
    assert entry["headers"]["Authorization"] == "Bearer s3cret"
    assert entry["headers"]["X-Api-Key"] == "k1"
    assert entry["headers"]["Content-Type"] == "application/json"
    # the bad line is reported, never shown (it may hold a token)
    assert "1 header line(s)" in caplog.text
    assert "s3cret" not in caplog.text


def test_a_custom_content_type_wins(receiver, configure, send):
    configure(
        receiver.url("/scores"),
        method="PUT",
        encoding=ENCODING_JSON_FULL,
        headers="content-type: application/vnd.example+json",
    )
    send(("Home Score", "12", "Success"))
    [entry] = receiver.accepted()
    assert entry["headers"]["content-type"] == "application/vnd.example+json"
    assert "Content-Type" not in entry["headers"] or (
        entry["headers"]["Content-Type"] == "application/vnd.example+json"
    )


def test_parse_headers():
    assert api_output.parse_headers("A: 1\r\nB-c:2\n: x\nno colon\nbad name: 3") == (
        {"A": "1", "B-c": "2"},
        3,
    )
    assert api_output.parse_headers(None) == ({}, 0)


# --- {field} / {value} and Fields per request -------------------------------------


def test_url_with_placeholders_sends_a_request_per_field(receiver, configure, send):
    configure(receiver.url("/set/{field}?v={value}"))
    send(
        ("Home Score", "Red Sox + 1", "Success"),
        ("Time", "4:59", "SameNoChange"),
        ("Period", "?", "FailedFilter"),  # not a good reading: not sent
    )
    assert [r["path"] for r in receiver.accepted()] == [
        "/set/Home%20Score?v=Red%20Sox%20%2B%201",
        "/set/Time?v=4%3A59",
    ]


def test_placeholders_with_post_send_one_field_per_body(receiver, configure, send):
    configure(receiver.url("/fields/{field}"), method="POST")
    send(("Home Score", "12", "Success"), ("Time", "4:59", "Success"))
    entries = receiver.accepted()
    assert [r["path"] for r in entries] == ["/fields/Home%20Score", "/fields/Time"]
    assert [json.loads(r["body"]) for r in entries] == [
        {"Home Score": "12"},
        {"Time": "4:59"},
    ]


def test_fields_per_request_batches_the_fields(receiver, configure, send):
    configure(receiver.url("/vars?token=a"), fields_per_request=2)
    send(*[(f"Box {i}", str(i), "Success") for i in range(5)])
    assert [query(r) for r in receiver.accepted()] == [
        [("token", "a"), ("Box 0", "0"), ("Box 1", "1")],
        [("token", "a"), ("Box 2", "2"), ("Box 3", "3")],
        [("token", "a"), ("Box 4", "4")],
    ]


def test_fields_per_request_with_json_full(receiver, configure, send):
    configure(
        receiver.url("/batch"),
        method="POST",
        encoding=ENCODING_JSON_FULL,
        fields_per_request=3,
    )
    send(("Home Score", "12", "Success", (1, 2, 3, 4)))
    [entry] = receiver.accepted()
    assert json.loads(entry["body"]) == [
        {
            "name": "Home Score",
            "text": "12",
            "state": "Success",
            "rect": {"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0},
        }
    ]


# --- Rate ---------------------------------------------------------------------------


def test_rate_spaces_out_the_requests(receiver, configure, send):
    configure(receiver.url("/set?key={field}&value={value}"), max_rate=10)
    send(*[(f"Box {i}", str(i), "Success") for i in range(4)])
    times = [r["at"] for r in receiver.accepted()]
    assert len(times) == 4
    assert min(b - a for a, b in zip(times, times[1:])) >= 0.08  # ~0.1 s apart


# --- safe names ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, safe",
    [
        ("Home Score", "home_score"),
        ("home_score", "home_score"),
        ("Période", "periode"),
        ("1st Half", "_1st_half"),
        ("Score (H)", "score_h"),
        ("***", "field"),
        ("A" * 40, "a" * 32),
    ],
)
def test_safe_name(name, safe):
    assert api_output.safe_name(name) == safe
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", api_output.safe_name(name))


def test_safe_names_never_collide():
    names = api_output.safe_names(["Home Score", "home_score", "HOME SCORE", "B" * 40])
    assert names == {
        "Home Score": "home_score",
        "home_score": "home_score_2",
        "HOME SCORE": "home_score_3",
        "B" * 40: "b" * 32,
    }
    long = api_output.safe_names(["C" * 40, "c" * 40])
    assert long["c" * 40] == "c" * 30 + "_2"
    assert len(set(names.values())) == len(names)


def test_safe_names_are_sent(receiver, configure, send):
    configure(receiver.url("/vars"), safe_names=True)
    send(("Home Score", "12", "Success"), ("Période", "2ème", "Success"))
    [entry] = receiver.accepted()
    assert entry["path"] == "/vars?home_score=12&periode=2%C3%A8me"


# --- logins -------------------------------------------------------------------------


def test_a_url_login_is_sent_with_basic_auth(receiver, configure, send):
    receiver.login = ("basic", "admin", "p@ss word")
    configure(receiver.url("/vars", login="admin:p%40ss%20word"))
    send(("Home Score", "12", "Success"))
    assert len(receiver.requests) == 1 and len(receiver.accepted()) == 1


def test_digest_is_used_when_the_endpoint_asks_for_it(receiver, configure, send):
    receiver.login = ("digest", "admin", "admin")
    configure(
        receiver.url("/v1/datalink?key={field}&value={value}", login="admin:admin")
    )
    send(("Home Score", "12", "Success"))
    send(("Home Score", "13", "Success"))
    accepted = receiver.accepted()
    assert [r["path"] for r in accepted] == [
        "/v1/datalink?key=Home%20Score&value=12",
        "/v1/datalink?key=Home%20Score&value=13",
    ]
    assert all(r["headers"]["Authorization"].startswith("Digest ") for r in accepted)
    # Basic first, then Digest from then on (no second Basic attempt)
    basic = [
        r
        for r in receiver.requests
        if r["headers"].get("Authorization", "").startswith("Basic ")
    ]
    assert len(basic) == 1


def test_a_wrong_login_is_reported_without_the_password(
    receiver, configure, send, caplog
):
    receiver.login = ("basic", "admin", "right")
    configure(receiver.url("/vars", login="admin:wrong-pw"))
    with caplog.at_level(logging.ERROR):
        send(("Home Score", "12", "Success"))
    assert "returned HTTP 401" in caplog.text
    assert "wrong-pw" not in caplog.text
    status = api_output.out_api_status()
    assert status.failing_keys == 1 and status.last_error.code == 401
    assert "wrong-pw" not in status.last_error.message


# --- presets ------------------------------------------------------------------------


def apply_preset(configure, key, url):
    preset = api_output.PRESETS[key]
    configure(
        preset.url(url),
        method=preset.method,
        encoding=preset.encoding,
        only_changes=preset.only_changes,
        refresh_s=preset.refresh_s,
        fields_per_request=preset.fields_per_request,
        max_rate=preset.max_rate,
        safe_names=preset.safe_names,
    )
    assert api_output.matching_preset(api_output.current_settings()) == key


def test_tricaster_datalink_preset(receiver, configure, send):
    # GET /v1/datalink?key=<field>&value=<value>, one per changed field, with
    # the default admin/admin login (Digest here)
    receiver.login = ("digest", "admin", "admin")
    apply_preset(configure, "tricaster", receiver.url("/"))
    assert api_output.out_api_url == receiver.url(
        "/v1/datalink?key={field}&value={value}", login="admin:admin"
    )
    send(
        ("Home Score", "21", "Success"),
        ("Time", "4:59", "Success"),
        ("Period", "2nd Half", "Success"),
    )
    send(
        ("Home Score", "21", "SameNoChange"),  # unchanged: not sent again
        ("Time", "4:58", "Success"),
        ("Period", "2nd Half", "SameNoChange"),
    )
    accepted = receiver.accepted()
    assert [r["path"] for r in accepted] == [
        "/v1/datalink?key=Home%20Score&value=21",
        "/v1/datalink?key=Time&value=4%3A59",
        "/v1/datalink?key=Period&value=2nd%20Half",
        "/v1/datalink?key=Time&value=4%3A58",
    ]
    assert {r["method"] for r in accepted} == {"GET"}
    assert all(r["body"] == b"" for r in accepted)
    # at most 10 requests a second
    times = [r["at"] for r in accepted]
    assert min(b - a for a, b in zip(times, times[1:])) >= 0.08


def test_epiphan_pearl_preset(receiver, configure, send):
    # GET /admin/set_variables.cgi?<name>=<value>&... with Basic auth, at most 6
    # variables a request and 5 requests a second, names of letters, digits, _
    receiver.login = ("basic", "admin", "secret")
    apply_preset(configure, "pearl", receiver.url("/", login="admin:secret"))
    assert api_output.out_api_url == receiver.url(
        "/admin/set_variables.cgi", login="admin:secret"
    )
    boxes = [
        ("Home Score", "21"),
        ("Away Score", "14"),
        ("Time", "4:59"),
        ("Period", "2nd Half"),
        ("Home Fouls", "3"),
        ("Away Fouls", "1"),
        ("Shot Clock", "24"),
        ("Possession", "<"),
    ]
    send(*[(name, value, "Success") for name, value in boxes])
    accepted = receiver.accepted()
    assert [r["path"] for r in accepted] == [
        "/admin/set_variables.cgi?home_score=21&away_score=14&time=4%3A59"
        "&period=2nd%20Half&home_fouls=3&away_fouls=1",
        "/admin/set_variables.cgi?shot_clock=24&possession=%3C",
    ]
    assert len(receiver.requests) == 2  # Basic auth up front: no 401 round trip
    assert accepted[1]["at"] - accepted[0]["at"] >= 0.18  # at most 5 a second

    # only the changed variables, batched again
    send(
        *[(name, value, "SameNoChange") for name, value in boxes[:2]],
        ("Time", "4:58", "Success"),
    )
    assert receiver.accepted()[-1]["path"] == "/admin/set_variables.cgi?time=4%3A58"


def test_preset_urls_keep_the_address_and_login():
    tricaster = api_output.PRESETS["tricaster"]
    pearl = api_output.PRESETS["pearl"]
    assert tricaster.url("") == (
        "http://admin:admin@TRICASTER-IP/v1/datalink?key={field}&value={value}"
    )
    assert pearl.url(None) == "http://admin:PASSWORD@PEARL-IP/admin/set_variables.cgi"
    # the address of the URL there was, and its login if it had one
    assert pearl.url("http://192.168.1.50/whatever?x=1") == (
        "http://admin:PASSWORD@192.168.1.50/admin/set_variables.cgi"
    )
    assert tricaster.url("http://me:pw@10.0.0.9:8080/old") == (
        "http://me:pw@10.0.0.9:8080/v1/datalink?key={field}&value={value}"
    )
    # another preset's placeholder is replaced
    assert pearl.url(tricaster.url("")) == pearl.url("")
    assert api_output.PLACEHOLDER_HOSTS == {"TRICASTER-IP", "PEARL-IP"}


def test_a_changed_setting_is_no_longer_the_preset():
    preset = api_output.PRESETS["pearl"]
    settings = api_output.ApiSettings(
        url=preset.url("http://10.0.0.2/"),
        method="GET",
        encoding=ENCODING_JSON_FULL,  # not used by GET
        fields_per_request=6,
        only_changes=True,
        max_rate=5.0,
        refresh_s=10,
        safe_names=True,
    )
    assert api_output.matching_preset(settings) == "pearl"
    for change in (
        {"max_rate": 10},
        {"fields_per_request": 1},
        {"safe_names": False},
        {"url": "http://10.0.0.2/other"},
        {"method": "POST"},
    ):
        changed = api_output.ApiSettings(**{**settings.__dict__, **change})
        assert api_output.matching_preset(changed) is None, change


def test_send_mode_and_status(configure):
    import output_status

    configure(api_output.PRESETS["tricaster"].url(""), only_changes=True)
    mode = api_output.send_mode()
    assert mode.needs_address and mode.per_field and mode.fields_per_request == 1
    status = output_status.api_status(
        True, api_output.out_api_url, "GET", ENCODING_JSON_SIMPLE, None, mode
    )
    assert (status.state, status.detail) == (
        "warning",
        "Enter your device's address in the URL",
    )

    configure(
        "http://10.0.0.9/v1/datalink?key={field}&value={value}", only_changes=True
    )
    status = output_status.api_status(
        True,
        api_output.out_api_url,
        "GET",
        ENCODING_JSON_SIMPLE,
        None,
        api_output.send_mode(),
    )
    assert status.detail == (
        "Waiting for readings: GET to 10.0.0.9 · a request per field · only changes"
    )
    configure("http://10.0.0.9/admin/set_variables.cgi", fields_per_request=6)
    status = output_status.api_status(
        True,
        api_output.out_api_url,
        "GET",
        ENCODING_JSON_SIMPLE,
        None,
        api_output.send_mode(),
    )
    assert (
        status.detail
        == "Waiting for readings: GET to 10.0.0.9 · up to 6 fields a request"
    )


# --- the API page --------------------------------------------------------------------


@pytest.fixture
def api_ui(qapp):
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QLabel,
        QLineEdit,
        QPlainTextEdit,
        QSpinBox,
    )

    from api_ui_handler import OUT_API_ENCODINGS, OUT_API_METHODS

    def combo(items):
        box = QComboBox()
        box.addItems(list(items))
        return box

    def spin(maximum):
        box = QSpinBox()
        box.setMaximum(maximum)
        return box

    return SimpleNamespace(
        lineEdit_api_url=QLineEdit(),
        comboBox_outApiMethod=combo(OUT_API_METHODS),
        comboBox_api_encode=combo(OUT_API_ENCODINGS),
        comboBox_outApiPreset=combo(["Custom", "TriCaster DataLink", "Epiphan Pearl"]),
        label_outApiPresetHelp=QLabel(),
        checkBox_outApiOnlyChanges=QCheckBox(),
        spinBox_outApiRefresh=spin(3600),
        spinBox_outApiFieldsPerRequest=spin(99),
        spinBox_outApiMaxRate=spin(100),
        checkBox_outApiSafeNames=QCheckBox(),
        label_outApiNamesSent=QLabel(),
        plainTextEdit_outApiHeaders=QPlainTextEdit(),
    )


@pytest.mark.qt
def test_api_page_options_are_stored(api_ui):
    from api_ui_handler import APIUIHandler
    from storage import fetch_data

    APIUIHandler(api_ui)
    api_ui.checkBox_outApiOnlyChanges.setChecked(True)
    api_ui.spinBox_outApiFieldsPerRequest.setValue(4)
    api_ui.spinBox_outApiMaxRate.setValue(3)
    api_ui.spinBox_outApiRefresh.setValue(30)
    api_ui.checkBox_outApiSafeNames.setChecked(True)
    api_ui.plainTextEdit_outApiHeaders.setPlainText("Authorization: Bearer x")
    assert fetch_data("scoresight.json", "out_api_only_changes") is True
    assert fetch_data("scoresight.json", "out_api_fields_per_request") == 4
    assert fetch_data("scoresight.json", "out_api_max_rate") == 3
    assert fetch_data("scoresight.json", "out_api_refresh_s") == 30
    assert fetch_data("scoresight.json", "out_api_safe_names") is True
    assert fetch_data("scoresight.json", "out_api_headers") == "Authorization: Bearer x"


@pytest.mark.qt
def test_api_page_defaults_are_the_old_behavior(api_ui):
    from api_ui_handler import APIUIHandler

    handler = APIUIHandler(api_ui)
    settings = handler.settings()
    assert (
        settings.fields_per_request,
        settings.only_changes,
        settings.max_rate,
        settings.refresh_s,
        settings.safe_names,
    ) == (0, False, 0, 0, False)
    assert api_ui.comboBox_outApiPreset.currentIndex() == 0  # Custom
    assert not api_ui.label_outApiPresetHelp.isVisibleTo(api_ui.label_outApiPresetHelp)
    assert api_ui.label_outApiNamesSent.text() == ""


@pytest.mark.qt
def test_picking_a_preset_fills_the_settings_in(api_ui):
    from api_ui_handler import APIUIHandler

    handler = APIUIHandler(api_ui)
    handler.setBoxNames(["Home Score", "Time"])
    api_ui.lineEdit_api_url.setText("http://192.168.1.20/")

    api_ui.comboBox_outApiPreset.activated.emit(1)  # TriCaster DataLink
    assert api_ui.lineEdit_api_url.text() == (
        "http://admin:admin@192.168.1.20/v1/datalink?key={field}&value={value}"
    )
    assert api_ui.comboBox_outApiMethod.currentText() == "GET"
    assert api_ui.checkBox_outApiOnlyChanges.isChecked()
    assert api_ui.spinBox_outApiMaxRate.value() == 10
    assert api_ui.comboBox_outApiPreset.currentIndex() == 1
    assert "%Home Score%" in api_ui.label_outApiPresetHelp.text()
    assert api_ui.label_outApiNamesSent.text() == "DataLink keys: %Home Score%, %Time%"

    api_ui.comboBox_outApiPreset.activated.emit(2)  # Epiphan Pearl
    assert api_ui.lineEdit_api_url.text() == (
        "http://admin:admin@192.168.1.20/admin/set_variables.cgi"
    )
    assert api_ui.spinBox_outApiFieldsPerRequest.value() == 6
    assert api_ui.spinBox_outApiMaxRate.value() == 5
    assert api_ui.spinBox_outApiRefresh.value() == 10
    assert api_ui.checkBox_outApiSafeNames.isChecked()
    assert api_ui.label_outApiNamesSent.text() == (
        "Pearl variables: %(home_score), %(time)"
    )

    # every setting can still be changed; the list then says Custom
    api_ui.spinBox_outApiMaxRate.setValue(2)
    assert api_ui.comboBox_outApiPreset.currentIndex() == 0
    assert api_ui.label_outApiNamesSent.text() == (
        "Sent as: home_score (Home Score), time (Time)"
    )
    api_ui.checkBox_outApiSafeNames.setChecked(False)
    assert api_ui.label_outApiNamesSent.text() == ""

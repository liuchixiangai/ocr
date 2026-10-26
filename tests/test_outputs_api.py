import csv
import io
import json
import logging
import socket
import threading
import time
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

import api_output
from output_worker import HTTP_TIMEOUT


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class FakeSession:
    """Stands in for api_output's requests.Session."""

    def __init__(self, status_code=200, delay=0.0):
        self.status_code = status_code
        self.delay = delay
        self.requests = []
        self.lock = threading.Lock()

    def request(self, method, url, headers=None, data=None, timeout=None):
        with self.lock:
            self.requests.append(
                {
                    "method": method,
                    "url": url,
                    "headers": headers,
                    "data": data,
                    "timeout": timeout,
                }
            )
        if self.delay:
            time.sleep(self.delay)
        return FakeResponse(self.status_code)


@pytest.fixture
def session(monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr(api_output, "_session", fake)
    monkeypatch.setattr(api_output, "_settings_log", api_output.ThrottledErrorLog("x"))
    api_output._worker.error_log.reset()
    yield fake
    api_output._worker.clear()
    assert api_output._worker.wait_idle(10)


@pytest.fixture
def configure(monkeypatch):
    def _configure(url="http://receiver.test/scores", method="POST", encoding=None):
        monkeypatch.setattr(api_output, "out_api_url", url)
        monkeypatch.setattr(api_output, "out_api_method", method)
        monkeypatch.setattr(api_output, "out_api_encoding", encoding)

    return _configure


@pytest.fixture
def readings(make_result):
    return [
        make_result("Home Score", "12", "Success", rect=(1, 2, 3, 4)),
        make_result("Away Score", "7", "SameNoChange", rect=(5, 6, 7, 8)),
        make_result("Time", "SIM", "FailedFilter"),
        make_result("Period", "", "Empty"),
    ]


def send(data):
    api_output.update_out_api(data)
    assert api_output._worker.wait_idle(10)


def test_simple_key_value_sends_only_good_readings(session, configure, readings):
    configure(encoding="JSON (Simple key-value)")
    send(readings)
    [request] = session.requests
    assert request["method"] == "POST"
    assert request["headers"] == {"Content-Type": "application/json"}
    assert json.loads(request["data"]) == {"Home Score": "12", "Away Score": "7"}


def test_get_sends_only_good_readings(session, configure, readings):
    configure(url="http://receiver.test/scores?token=abc", method="GET")
    send(readings)
    [request] = session.requests
    assert request["method"] == "GET"
    assert request["data"] is None
    parts = urlsplit(request["url"])
    assert parts.path == "/scores"
    assert parse_qs(parts.query) == {
        "token": ["abc"],
        "Home Score": ["12"],
        "Away Score": ["7"],
    }


def test_get_sends_spaces_as_percent_20(session, configure, make_result):
    # urlencode() sent "Home+Score=Red+Sox"; not every receiver decodes "+"
    configure(url="http://receiver.test/scores", method="GET")
    send([make_result("Home Score", "Red Sox + 1", "Success")])
    [request] = session.requests
    assert urlsplit(request["url"]).query == "Home%20Score=Red%20Sox%20%2B%201"


@pytest.mark.parametrize(
    "method, encoding", [("GET", None), ("PUT", "JSON (Simple key-value)")]
)
def test_nothing_is_sent_without_good_readings(
    session, configure, make_result, method, encoding
):
    configure(method=method, encoding=encoding)
    send(
        [make_result("Time", "SIM", "FailedFilter"), make_result("Period", "", "Empty")]
    )
    assert session.requests == []


def test_json_full_sends_every_box_with_its_state(session, configure, readings):
    configure(method="PUT", encoding="JSON (Full)")
    send(readings)
    [request] = session.requests
    assert request["method"] == "PUT"
    body = json.loads(request["data"])
    assert [(r["name"], r["text"], r["state"]) for r in body] == [
        ("Home Score", "12", "Success"),
        ("Away Score", "7", "SameNoChange"),
        ("Time", "SIM", "FailedFilter"),
        ("Period", "", "Empty"),
    ]
    assert body[0]["rect"] == {"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0}


def test_xml_and_csv_payloads(session, configure, readings):
    configure(encoding="XML")
    send(readings)
    configure(encoding="CSV")
    send(readings)
    xml_request, csv_request = session.requests

    assert xml_request["headers"] == {"Content-Type": "application/xml"}
    root = ET.fromstring(xml_request["data"])
    assert root.tag == "root"
    assert [el.get("result_state") for el in root] == [
        "Success",
        "SameNoChange",
        "FailedFilter",
        "Empty",
    ]
    assert root[0].attrib["x"] == "1.0"

    assert csv_request["headers"] == {"Content-Type": "text/csv"}
    rows = list(csv.reader(io.StringIO(csv_request["data"].decode("utf-8"))))
    assert rows[0] == ["Name", "Text", "State", "X", "Y", "Width", "Height"]
    assert rows[1] == ["Home Score", "12", "Success", "1.0", "2.0", "3.0", "4.0"]
    assert len(rows) == 5


@pytest.mark.parametrize("encoding", ["", None, "JSON", "Something else"])
def test_unknown_encoding_falls_back_to_json_full(
    session, configure, readings, encoding
):
    # e.g. '' stored by the API tab on a fresh install
    configure(encoding=encoding)
    send(readings)
    [request] = session.requests
    assert request["headers"] == {"Content-Type": "application/json"}
    assert len(json.loads(request["data"])) == 4


def test_every_request_has_a_timeout(session, configure, readings):
    configure(method="GET")
    send(readings)
    configure(method="POST", encoding="CSV")
    send(readings)
    assert [r["timeout"] for r in session.requests] == [HTTP_TIMEOUT, HTTP_TIMEOUT]


@pytest.mark.parametrize("status", [200, 201, 202, 204])
def test_any_2xx_is_success(session, configure, readings, caplog, status):
    session.status_code = status
    configure(encoding="JSON (Full)")
    with caplog.at_level(logging.ERROR):
        send(readings)
    assert caplog.records == []


def test_error_status_is_logged(session, configure, readings, caplog):
    session.status_code = 500
    configure(encoding="JSON (Full)")
    with caplog.at_level(logging.ERROR):
        send(readings)
    assert "returned HTTP 500" in caplog.text


def test_slow_endpoint_never_blocks_or_piles_up(session, configure, make_result):
    session.delay = 0.5
    configure(encoding="JSON (Simple key-value)")
    threads_before = threading.active_count()

    start = time.monotonic()
    for i in range(50):
        api_output.update_out_api([make_result("Time", str(i), "Success")])
    elapsed = time.monotonic() - start

    assert elapsed < 1.0  # the old code started one thread per call...
    assert threading.active_count() <= threads_before + 1  # ...this has one worker
    assert api_output._worker.wait_idle(10)
    # the first request was in flight, the rest coalesced into the latest one
    sent = [json.loads(r["data"])["Time"] for r in session.requests]
    assert sent[-1] == "49"
    assert len(sent) <= 10  # of 50 submitted (typically 2)


def test_unreachable_endpoint_is_logged_and_does_not_block(
    configure, readings, caplog, monkeypatch
):
    # a real request to a port nobody listens on (a refused connection to
    # localhost takes ~2 s on Windows, so use a shorter timeout here)
    monkeypatch.setattr(api_output, "HTTP_TIMEOUT", (0.3, 0.3))
    api_output._worker.error_log.reset()
    with requests.Session() as real_session:
        monkeypatch.setattr(api_output, "_session", real_session)
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()
        configure(url=f"http://127.0.0.1:{port}/x", encoding="JSON (Full)")
        with caplog.at_level(logging.ERROR):
            start = time.monotonic()
            api_output.update_out_api(readings)
            assert time.monotonic() - start < 1.0
            deadline = time.monotonic() + 10
            while "Output API" not in caplog.text and time.monotonic() < deadline:
                time.sleep(0.05)
        api_output._worker.clear()
        assert api_output._worker.wait_idle(10)
    assert f"127.0.0.1:{port}" in caplog.text or f"port={port}" in caplog.text


@pytest.mark.parametrize("url", [None, "", "not a url", "receiver.test/scores"])
def test_invalid_settings_are_logged_throttled(
    session, configure, readings, caplog, url
):
    configure(url=url)
    with caplog.at_level(logging.ERROR):
        for _ in range(10):
            api_output.update_out_api(readings)
    assert session.requests == []
    assert len(caplog.records) == 1


def test_settings_subscribers(data_dir):
    from storage import store_data, subscribe_to_data

    # import-time subscriptions live in the global registry, which the data_dir
    # fixture swaps out; re-register them on the per-test registry
    subscribe_to_data("scoresight.json", "out_api_url", api_output.setup_out_api_url)
    original = api_output.out_api_url
    try:
        store_data("scoresight.json", "out_api_url", "http://changed.test/")
        assert api_output.out_api_url == "http://changed.test/"
    finally:
        api_output.setup_out_api_url(original)


class UnreachableSession(FakeSession):
    def request(self, *args, **kwargs):
        super().request(*args, **kwargs)
        raise requests.ConnectionError("refused")


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("enable_out_api", False),
        ("out_api_url", "http://localhost.test/scores"),
        ("out_api_method", "PUT"),
        ("out_api_encoding", "XML"),
    ],
)
def test_turning_the_output_off_or_changing_it_drops_the_retried_request(
    data_dir, configure, readings, monkeypatch, setting, value
):
    # A request to an unreachable endpoint is retried until it is sent. It used
    # to be retried (and logged) for the rest of the session after the output
    # was turned off, and sent with the old settings if the endpoint came back.
    from storage import store_data, subscribe_to_data

    unreachable = UnreachableSession()
    monkeypatch.setattr(api_output, "_session", unreachable)
    monkeypatch.setattr(api_output._worker, "_initial_backoff", 0.01)
    monkeypatch.setattr(api_output._worker, "_max_backoff", 0.01)
    configure(url="http://localahost.test/scores", encoding="JSON (Full)")
    # import-time subscriptions live in the global registry, which the data_dir
    # fixture swaps out; re-register them on the per-test registry
    for name, callback in (
        ("enable_out_api", api_output.setup_out_api_enabled),
        ("out_api_url", api_output.setup_out_api_url),
        ("out_api_method", api_output.setup_out_api_method),
        ("out_api_encoding", api_output.setup_out_api_encoding),
    ):
        subscribe_to_data("scoresight.json", name, callback)
    try:
        api_output.update_out_api(readings)
        deadline = time.monotonic() + 5
        while len(unreachable.requests) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(unreachable.requests) >= 3  # retried

        store_data("scoresight.json", setting, value)
        assert api_output._worker.wait_idle(5)
        assert api_output._worker.pending() == {}
        attempts = len(unreachable.requests)
        time.sleep(0.1)
        assert len(unreachable.requests) == attempts  # no more retries
    finally:
        api_output._worker.clear()
        assert api_output._worker.wait_idle(10)
        api_output._worker._retry_failures = 0
        api_output._worker.error_log.reset()

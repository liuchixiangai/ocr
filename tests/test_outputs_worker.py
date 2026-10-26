import logging
import threading
import time
import weakref
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

import output_worker
from output_worker import (
    ErrorInfo,
    OutputError,
    OutputWorker,
    SendSkipped,
    ThrottledErrorLog,
    check_http_response,
    stop_all_workers,
)


class Recorder:
    """send() callable that records calls and can be held on an Event."""

    def __init__(self, gate: threading.Event | None = None, errors=None):
        self.calls = []
        self.gate = gate
        self.started = threading.Event()
        # {call index: exception to raise}
        self.errors = dict(errors or {})
        self.lock = threading.Lock()

    def __call__(self, key, item):
        with self.lock:
            index = len(self.calls)
            self.calls.append((key, item))
        self.started.set()
        if self.gate is not None:
            assert self.gate.wait(5), "test gate never opened"
        if index in self.errors:
            raise self.errors[index]


@pytest.fixture
def make_worker():
    workers = []

    def _make(send, **kwargs):
        worker = OutputWorker("test", send, **kwargs)
        workers.append(worker)
        return worker

    yield _make
    for worker in workers:
        worker.stop(2)


def test_submit_does_not_block_on_a_slow_endpoint(make_worker):
    gate = threading.Event()
    send = Recorder(gate)
    worker = make_worker(send)

    start = time.monotonic()
    for i in range(200):
        worker.submit("field", i)
    elapsed = time.monotonic() - start
    assert send.started.wait(2)
    assert elapsed < 1.0  # the "endpoint" is stuck, the caller is not

    gate.set()
    assert worker.wait_idle(5)


def test_latest_value_wins_per_key(make_worker):
    gate = threading.Event()
    send = Recorder(gate)
    worker = make_worker(send)

    worker.submit("a", 0)
    assert send.started.wait(2)  # the worker is now stuck sending a=0
    for i in range(1, 100):
        worker.submit("a", i)
    worker.submit("b", "only")
    assert worker.pending() == {"a": 99, "b": "only"}  # bounded backlog

    gate.set()
    assert worker.wait_idle(5)
    assert send.calls == [("a", 0), ("a", 99), ("b", "only")]


def test_other_keys_are_not_lost_when_one_key_is_overwritten(make_worker):
    # vMix/UNO only send changed fields: a field changed in an older cycle must
    # still be sent even if a newer cycle (without that field) replaced it.
    gate = threading.Event()
    send = Recorder(gate)
    worker = make_worker(send)

    worker.submit("Time", "1:00")
    assert send.started.wait(2)
    worker.submit("Home Score", "12")  # cycle 2: score and clock changed
    worker.submit("Time", "0:59")
    worker.submit("Time", "0:58")  # cycle 3: only the clock changed
    gate.set()
    assert worker.wait_idle(5)
    assert send.calls == [("Time", "1:00"), ("Home Score", "12"), ("Time", "0:58")]


def test_unreachable_endpoint_is_retried_with_the_latest_value(make_worker, caplog):
    send = Recorder(errors={0: ConnectionError("refused")})
    worker = make_worker(
        send, retry_on=(ConnectionError,), initial_backoff=0.01, max_backoff=0.01
    )

    with caplog.at_level(logging.INFO):
        worker.submit("a", 1)
        worker.submit("b", 2)
        assert worker.wait_idle(5)

    # a=1 failed as "unreachable", so a and b were both kept and retried
    assert send.calls == [("a", 1), ("a", 1), ("b", 2)]
    messages = [r.getMessage() for r in caplog.records]
    assert any("ConnectionError: refused" in m for m in messages)
    assert any("connection restored" in m for m in messages)


def test_a_long_outage_does_not_end_the_worker(make_worker):
    # Every "unreachable" failure doubles the backoff up to max_backoff, for as
    # long as the endpoint is down: after ~1025 of them (a few hours) computing
    # the backoff overflowed a float and the thread died, so nothing was ever
    # sent again, even after the endpoint came back.
    down = threading.Event()
    down.set()
    calls = []

    def send(key, item):
        calls.append(item)
        if down.is_set():
            raise ConnectionError("refused")

    worker = make_worker(
        send, retry_on=(ConnectionError,), initial_backoff=0.001, max_backoff=0.001
    )
    worker._retry_failures = 1020
    worker.submit("a", 1)
    deadline = time.monotonic() + 5
    while worker._retry_failures < 1030 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert worker._retry_failures >= 1030
    assert worker.is_running
    assert worker._backoff_delay() == 0.001

    down.clear()  # the endpoint is back
    worker.submit("a", 2)
    assert worker.wait_idle(5)
    assert calls[-1] == 2
    assert worker._retry_failures == 0


def test_backoff_doubles_up_to_the_maximum(make_worker):
    worker = make_worker(lambda key, item: None, initial_backoff=0.5, max_backoff=5.0)
    delays = []
    for failures in (1, 2, 3, 4, 5, 6, 1025, 10**6):
        worker._retry_failures = failures
        delays.append(worker._backoff_delay())
    assert delays == [0.5, 1.0, 2.0, 4.0, 5.0, 5.0, 5.0, 5.0]


def test_newer_value_replaces_a_failed_one(make_worker):
    gate = threading.Event()
    send = Recorder(gate, errors={0: ConnectionError("down")})
    worker = make_worker(
        send, retry_on=(ConnectionError,), initial_backoff=0.01, max_backoff=0.01
    )

    worker.submit("a", "old")
    assert send.started.wait(2)
    worker.submit("a", "new")  # arrives while "old" is failing
    gate.set()
    assert worker.wait_idle(5)
    assert send.calls == [("a", "old"), ("a", "new")]


def test_other_errors_drop_only_that_item(make_worker, caplog):
    send = Recorder(errors={0: ValueError("HTTP 500")})
    worker = make_worker(send, retry_on=(ConnectionError,))

    with caplog.at_level(logging.ERROR):
        worker.submit("a", 1)
        worker.submit("b", 2)
        assert worker.wait_idle(5)

    assert send.calls == [("a", 1), ("b", 2)]
    assert any("HTTP 500" in r.getMessage() for r in caplog.records)


def test_worker_survives_errors_and_keeps_sending(make_worker):
    send = Recorder(errors={i: RuntimeError("boom") for i in range(5)})
    worker = make_worker(send)
    for i in range(10):
        worker.submit(i, i)
        assert worker.wait_idle(5)
    assert [item for _, item in send.calls] == list(range(10))
    assert worker.is_running


def test_clear_drops_pending_items_and_the_rest_of_the_batch(make_worker):
    # e.g. the user clicks "Stop vMix" while a batch of fields is being sent
    x_gate, a_started, a_gate = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def send(key, item):
        calls.append(key)
        if key == "x":
            assert x_gate.wait(5)
        if key == "a":
            a_started.set()
            assert a_gate.wait(5)

    worker = make_worker(send)
    worker.submit("x", 0)
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    for key in "abc":
        worker.submit(key, 1)  # the next batch: a, b, c
    x_gate.set()
    assert a_started.wait(5)

    worker.clear()
    a_gate.set()
    assert worker.wait_idle(5)
    assert calls == ["x", "a"]


def test_thread_starts_lazily_is_daemon_and_stops(make_worker):
    send = Recorder()
    worker = make_worker(send)
    assert not worker.is_running

    worker.submit("a", 1)
    assert worker.wait_idle(5)
    thread = worker._thread
    assert thread.daemon
    assert thread.name == "output-test"

    assert worker.stop(2)
    assert not thread.is_alive()
    worker.submit("a", 2)  # ignored after stop
    assert worker.pending() == {}
    assert send.calls == [("a", 1)]


def test_stop_is_bounded_while_a_request_hangs(make_worker):
    gate = threading.Event()
    send = Recorder(gate)
    worker = make_worker(send)
    worker.submit("a", 1)
    assert send.started.wait(2)
    try:
        start = time.monotonic()
        assert worker.stop(0.2) is False
        assert time.monotonic() - start < 1.0
    finally:
        gate.set()
    worker._thread.join(2)
    assert not worker._thread.is_alive()


def test_stop_all_workers(make_worker, monkeypatch):
    # only the workers made here: the module-level ones of api_output and
    # obs_websocket must keep working for the other tests
    monkeypatch.setattr(output_worker, "_workers", weakref.WeakSet())
    first = make_worker(Recorder())
    second = make_worker(Recorder())
    first.submit("a", 1)
    second.submit("a", 1)
    assert first.wait_idle(5) and second.wait_idle(5)

    assert stop_all_workers(2)
    assert not first.is_running
    assert not second.is_running


def test_throttled_error_log(caplog):
    now = [100.0]
    log = ThrottledErrorLog("vMix", interval=30, clock=lambda: now[0])

    with caplog.at_level(logging.ERROR):
        assert log.error("down")
        for _ in range(5):
            now[0] += 1
            assert not log.error("down")
        now[0] += 30
        assert log.error("still down")
        log.reset()
        assert log.error("down again")

    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "vMix: down",
        "vMix: still down (5 more error(s) since the last report were not logged)",
        "vMix: down again",
    ]


# --- status() ---------------------------------------------------------------------


def wait_for(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_status_of_a_new_worker(make_worker):
    status = make_worker(Recorder()).status()
    assert (status.name, status.sent, status.failed, status.pending) == (
        "test",
        0,
        0,
        0,
    )
    assert not status.retrying and status.retry_delay is None
    assert status.last_success_at is None and status.last_error is None


def test_status_counts_what_was_sent_and_what_failed(make_worker):
    now = [100.0]
    send = Recorder(errors={1: OutputError("returned HTTP 401", status_code=401)})
    worker = make_worker(send, clock=lambda: now[0])
    worker.submit("Home", "1")
    assert worker.wait_idle(5)
    now[0] = 105.0
    worker.submit("Away", "2")  # fails
    assert worker.wait_idle(5)

    status = worker.status()
    assert (status.sent, status.failed, status.consecutive_failures) == (1, 1, 1)
    assert (status.ok_keys, status.failing_keys) == (1, 1)
    assert (status.last_success_at, status.last_failure_at) == (100.0, 105.0)
    assert not status.retrying  # the endpoint answered
    error = status.last_error
    assert (error.type, error.code, error.unreachable) == (OutputError, 401, False)
    assert str(error) == "OutputError: returned HTTP 401"

    # the failing key succeeds again
    worker.submit("Away", "3")
    assert worker.wait_idle(5)
    status = worker.status()
    assert (status.ok_keys, status.failing_keys, status.consecutive_failures) == (
        2,
        0,
        0,
    )


def test_status_while_the_endpoint_is_unreachable(make_worker):
    gate = threading.Event()
    send = Recorder(gate, errors={i: ConnectionError("refused") for i in range(3)})
    worker = make_worker(
        send, retry_on=(ConnectionError,), initial_backoff=0.05, max_backoff=0.05
    )
    gate.set()
    worker.submit("a", 1)
    wait_for(lambda: worker.status().retrying)
    status = worker.status()
    assert status.retry_delay == 0.05
    assert status.last_error.unreachable
    assert status.last_error.type is ConnectionError
    assert status.failing_keys == 0  # kept for a retry, not dropped

    wait_for(lambda: worker.status().sent == 1)  # the 4th attempt succeeds
    status = worker.status()
    assert not status.retrying and status.failed == 3


def test_a_recovered_outage_does_not_hide_a_failing_field(make_worker):
    # key "a" is rejected by the endpoint (dropped), then the endpoint is
    # unreachable for a while: once it is back, the status is about "a" again,
    # not about the outage that is over
    send = Recorder(
        errors={
            0: OutputError("returned HTTP 500", status_code=500),
            2: ConnectionError("refused"),
        }
    )
    worker = make_worker(
        send, retry_on=(ConnectionError,), initial_backoff=0.01, max_backoff=0.01
    )
    worker.submit("a", 1)  # fails: HTTP 500
    assert worker.wait_idle(5)
    worker.submit("b", 1)  # ok
    assert worker.wait_idle(5)
    worker.submit("b", 2)  # unreachable, then sent on the retry
    wait_for(lambda: worker.status().sent == 2)
    status = worker.status()
    assert not status.retrying
    assert (status.ok_keys, status.failing_keys) == (1, 1)
    assert status.last_error.code == 500 and not status.last_error.unreachable


def test_reset_status_ends_the_backoff_and_ignores_the_old_target(make_worker):
    # e.g. a new vMix host while the old one is unreachable: the values are
    # sent to the new host right away, and the old host's failure in flight
    # isn't the new host's status
    gate = threading.Event()
    target = {"host": "old"}
    calls = []

    def send(key, item):
        calls.append(target["host"])
        if target["host"] == "old":
            assert gate.wait(5)
            raise ConnectionError("refused")

    worker = make_worker(
        send, retry_on=(ConnectionError,), initial_backoff=30, max_backoff=30
    )
    worker.submit("a", 1)
    wait_for(lambda: calls)
    target["host"] = "new"
    worker.reset_status()  # while the request to "old" is in flight
    gate.set()
    start = time.monotonic()
    wait_for(lambda: worker.status().sent == 1)
    assert time.monotonic() - start < 5  # not the 30 s backoff
    assert calls == ["old", "new"]
    status = worker.status()
    assert (status.failed, status.retrying, status.last_error) == (0, False, None)


def test_reset_status(make_worker):
    send = Recorder(errors={0: ValueError("bad")})
    worker = make_worker(send)
    worker.submit("a", 1)
    worker.submit("b", 2)
    assert worker.wait_idle(5)
    assert worker.status().failed == 1
    worker.reset_status()
    status = worker.status()
    assert (status.sent, status.failed, status.ok_keys, status.failing_keys) == (
        0,
        0,
        0,
        0,
    )
    assert status.last_error is None and status.last_success_at is None


def test_forget_keys(make_worker):
    send = Recorder(errors={0: ValueError("gone")})
    worker = make_worker(send)
    worker.submit(("client 1", "Clock"), "1:00")
    worker.submit(("client 2", "Clock"), "1:00")
    assert worker.wait_idle(5)
    assert (worker.status().ok_keys, worker.status().failing_keys) == (1, 1)
    worker.forget_keys(lambda key: key[0] == "client 1")
    status = worker.status()
    assert (status.ok_keys, status.failing_keys) == (1, 0)
    assert status.last_error is None


def test_a_skipped_item_is_neither_sent_nor_failed(make_worker):
    def send(key, item):
        raise SendSkipped()

    worker = make_worker(send)
    worker.submit("a", 1)
    assert worker.wait_idle(5)
    status = worker.status()
    assert (status.sent, status.failed, status.ok_keys) == (0, 0, 0)


def test_the_result_of_a_cleared_request_does_not_count(make_worker):
    gate = threading.Event()
    send = Recorder(gate, errors={0: ValueError("late")})
    worker = make_worker(send)
    worker.submit("a", 1)
    assert send.started.wait(5)
    worker.clear()  # e.g. the output was turned off
    worker.reset_status()
    gate.set()
    assert worker.wait_idle(5)
    assert worker.status().failed == 0


def test_error_info():
    error = ErrorInfo.of(OutputError("SetText returned HTTP 404", status_code=404))
    assert (error.code, error.unreachable) == (404, False)
    assert ErrorInfo.of(ValueError()).message == ""
    assert str(ErrorInfo.of(ValueError())) == "ValueError"
    assert ErrorInfo.of(ConnectionError("x"), unreachable=True).unreachable


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass


def _serve(port=0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), _OkHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_status_follows_a_real_endpoint_that_goes_down_and_comes_back(make_worker):
    server = _serve()
    port = server.server_address[1]
    session = requests.Session()

    def send(key, item):
        # like the outputs: one session, short timeouts, 2xx or OutputError
        response = session.get(f"http://127.0.0.1:{port}/{key}", timeout=(0.5, 0.5))
        check_http_response(response, f"GET {key}")

    worker = make_worker(
        send,
        retry_on=(requests.ConnectionError, requests.Timeout),
        initial_backoff=0.05,
        max_backoff=0.2,
    )
    try:
        # ok
        worker.submit("Time", "1:00")
        assert worker.wait_idle(10)
        status = worker.status()
        assert status.sent == 1 and not status.retrying
        assert status.last_success_at is not None and status.last_error is None

        # unreachable: the server is gone
        server.shutdown()
        server.server_close()
        worker.submit("Time", "0:59")
        wait_for(lambda: worker.status().retrying)
        status = worker.status()
        assert status.last_error.unreachable
        assert issubclass(status.last_error.type, requests.RequestException)
        assert status.retry_delay is not None

        # restored: the server is back on the same port, the value arrives
        server = _serve(port)
        wait_for(lambda: worker.status().sent == 2)
        status = worker.status()
        assert not status.retrying and status.consecutive_failures == 0
        assert status.pending == 0
    finally:
        server.shutdown()
        server.server_close()
        session.close()


# --- RetryLater (HTTP 429), batches and pacing -------------------------------


class FakeHttpResponse:
    def __init__(self, status_code, headers=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text


def test_http_429_raises_retry_later_with_its_retry_after():
    with pytest.raises(output_worker.RetryLater) as raised:
        check_http_response(FakeHttpResponse(429, {"Retry-After": "7"}), "PUT x")
    assert raised.value.status_code == 429
    assert raised.value.delay == 7.0
    assert "returned HTTP 429" in str(raised.value)
    # still an OutputError (an HTTP status) for the status texts
    assert isinstance(raised.value, OutputError)
    with pytest.raises(output_worker.RetryLater) as raised:
        check_http_response(FakeHttpResponse(429), "PUT x")
    assert raised.value.delay is None


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        ("", None),
        ("soon", None),
        ("12", 12.0),
        ("1.5", 1.5),
        ("-3", 0.0),
        ("999999", output_worker.MAX_RETRY_AFTER),
        ("nan", None),
        # an HTTP date, 30 s after "now"
        ("Wed, 21 Oct 2015 07:28:30 GMT", 30.0),
    ],
)
def test_retry_after_seconds(value, expected):
    now = 1445412480.0  # Wed, 21 Oct 2015 07:28:00 GMT
    assert output_worker.retry_after_seconds(value, now=now) == expected


def test_retry_later_keeps_the_latest_value_and_resends_it(make_worker):
    # a 429 used to drop the value: with "update on change" a score that hit
    # the limit stayed wrong until it changed again
    gate = threading.Event()
    calls = []

    def send(key, item):
        calls.append((key, item))
        if len(calls) == 1:
            raise output_worker.RetryLater("HTTP 429", delay=0.3)
        assert gate.wait(5)

    worker = make_worker(send, initial_backoff=0.05)
    worker.submit("Home Score", "3")
    wait_for(lambda: worker.status().retrying)
    status = worker.status()
    assert status.retry_delay == 0.3  # as asked (more than the backoff)
    assert status.last_error.code == 429 and status.last_error.unreachable
    assert status.failing_keys == 0  # kept, not dropped
    # a newer value for the key replaces the kept one; another key waits too
    worker.submit("Home Score", "4")
    worker.submit("Away Score", "1")
    started = time.monotonic()
    gate.set()
    assert worker.wait_idle(10)
    assert time.monotonic() - started >= 0.2  # waited as asked
    assert calls == [
        ("Home Score", "3"),
        ("Home Score", "4"),
        ("Away Score", "1"),
    ]
    status = worker.status()
    assert not status.retrying and status.retry_delay is None
    assert (status.ok_keys, status.failing_keys) == (2, 0)


@pytest.mark.parametrize("delay", [0.0, None])
def test_retry_later_never_retries_in_a_busy_loop(make_worker, delay):
    # "Retry-After: 0", or an HTTP date already past by this computer's clock:
    # the worker still waits its backoff
    calls = []

    def send(key, item):
        calls.append(time.monotonic())
        raise output_worker.RetryLater("HTTP 429", delay=delay)

    worker = make_worker(send, initial_backoff=0.1, max_backoff=0.2)
    worker.submit("k", 1)
    time.sleep(0.5)
    assert 2 <= len(calls) <= 6
    assert worker.status().retry_delay >= 0.1


def test_the_status_counts_down_to_the_next_try(make_worker):
    worker = make_worker(
        Recorder(errors={0: output_worker.RetryLater("HTTP 429", delay=60)})
    )
    worker.submit("k", 1)
    wait_for(lambda: worker.status().retry_in is not None)
    first = worker.status().retry_in
    assert 59 < first <= 60
    time.sleep(0.2)
    status = worker.status()
    assert status.retry_delay == 60  # the whole wait
    assert status.retry_in < first  # the time left


def test_recovery_from_a_rate_limit_is_logged(make_worker, caplog):
    errors = {0: output_worker.RetryLater("HTTP 429", delay=0.05)}
    worker = make_worker(Recorder(errors=errors))
    with caplog.at_level(logging.INFO, logger=output_worker.logger.name):
        worker.submit("k", 1)
        wait_for(lambda: "after the rate limit" in caplog.text)
    messages = [r.getMessage() for r in caplog.records]
    assert not any("connection restored" in m for m in messages)


def test_stop_ends_a_long_retry_later_wait(make_worker):
    worker = make_worker(
        Recorder(errors={0: output_worker.RetryLater("HTTP 429", delay=3000)})
    )
    worker.submit("k", 1)
    wait_for(lambda: worker.status().retrying)
    assert worker.status().retry_delay == 3000
    started = time.monotonic()
    assert worker.stop(5)
    assert time.monotonic() - started < 2


def test_send_many_gets_up_to_max_batch_items_per_call(make_worker):
    calls = []
    gate = threading.Event()

    def send_many(chunk):
        calls.append(list(chunk))
        assert gate.wait(5)

    worker = OutputWorker("batch", send_many=send_many, max_batch=2)
    try:
        worker.submit("hold", 0)  # in flight while the others queue up
        wait_for(lambda: calls)
        for key in ("a", "b", "c", "d", "e"):
            worker.submit(key, key.upper())
        worker.submit("c", "C2")  # coalesced per key
        gate.set()
        assert worker.wait_idle(10)
        assert calls == [
            [("hold", 0)],
            [("a", "A"), ("b", "B")],
            [("c", "C2"), ("d", "D")],
            [("e", "E")],
        ]
        assert worker.status().sent == 6 and worker.status().ok_keys == 6

        # None: everything pending in one call
        calls.clear()
        gate.clear()
        worker.max_batch = None
        worker.submit("hold", 1)
        wait_for(lambda: calls)
        for key in ("x", "y", "z"):
            worker.submit(key, 1)
        gate.set()
        assert worker.wait_idle(10)
        assert [len(chunk) for chunk in calls] == [1, 3]
    finally:
        worker.stop(2)


def test_a_failed_batch_fails_or_retries_all_of_its_items(make_worker):
    attempts = []
    holding = threading.Event()
    gate = threading.Event()

    def send_many(chunk):
        if [key for key, _ in chunk] == ["hold"]:
            # keep the worker busy while "a" and "b" are queued, so they
            # always go out in one batch (it would take "a" alone otherwise)
            holding.set()
            assert gate.wait(5)
            return
        attempts.append([key for key, _ in chunk])
        if len(attempts) == 1:
            raise OutputError("HTTP 400", status_code=400)
        if len(attempts) == 2:
            raise requests.ConnectionError("refused")

    worker = OutputWorker(
        "batch",
        send_many=send_many,
        max_batch=3,
        retry_on=(requests.ConnectionError,),
        initial_backoff=0.01,
    )

    def submit_together(a, b):
        holding.clear()
        gate.clear()
        worker.submit("hold", a)
        assert holding.wait(5)
        worker.submit("a", a)
        worker.submit("b", b)
        gate.set()

    try:
        submit_together(1, 2)
        assert worker.wait_idle(5)
        status = worker.status()
        assert (status.failing_keys, status.failed) == (2, 2)  # dropped

        submit_together(3, 4)
        wait_for(lambda: len(attempts) == 3)  # refused, then retried
        assert worker.wait_idle(5)
        assert attempts == [["a", "b"], ["a", "b"], ["a", "b"]]
        status = worker.status()
        assert (status.ok_keys, status.failing_keys) == (3, 0)  # and "hold"
    finally:
        worker.stop(2)


def test_min_interval_spaces_out_the_calls(make_worker):
    times = []

    def send(key, item):
        times.append(time.monotonic())

    worker = make_worker(send, min_interval=0.1)
    for i in range(4):
        worker.submit(f"field {i}", i)
    assert worker.wait_idle(10)
    assert len(times) == 4
    gaps = [later - earlier for earlier, later in zip(times, times[1:])]
    assert min(gaps) >= 0.09  # at most 10 calls per second

    # 0 turns the pacing off
    worker.min_interval = 0
    times.clear()
    for i in range(4):
        worker.submit(f"field {i}", i)
    assert worker.wait_idle(10)
    assert times[-1] - times[0] < 0.1


def test_clear_ends_a_pacing_wait(make_worker):
    calls = []
    worker = make_worker(lambda key, item: calls.append(key), min_interval=5)
    worker.submit("a", 1)
    wait_for(lambda: calls == ["a"])
    worker.submit("b", 2)
    time.sleep(0.05)  # waiting out the 5 s interval
    started = time.monotonic()
    worker.clear()
    assert worker.wait_idle(2)
    assert time.monotonic() - started < 1
    assert calls == ["a"]


def test_worker_needs_one_send_function():
    with pytest.raises(TypeError):
        OutputWorker("x")
    with pytest.raises(TypeError):
        OutputWorker("x", lambda k, i: None, send_many=lambda chunk: None)

"""Starting and stopping the HTTP server in-process (issues #7, #33, #8)."""

import http.client
import logging
import socket
import threading
import time

import pytest
import requests

import http_server


@pytest.fixture(autouse=True)
def no_server_left_running():
    yield
    http_server.stop_http_server(timeout=3)
    assert http_server.get_http_server_port() is None


@pytest.fixture
def blocker():
    """A plain listening socket occupying a free port (like another program, or
    another ScoreSight instance, would)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("0.0.0.0", 0))
    sock.listen(5)
    sock.settimeout(0.5)
    yield sock
    sock.close()


def server_threads():
    return [t for t in threading.enumerate() if t.name == "http-server"]


def test_stop_http_server_stops_the_server_promptly():
    http_server.start_http_server(port=0)
    port = http_server.get_http_server_port()
    url = f"http://127.0.0.1:{port}/json"

    # a polling client with an open keep-alive connection (like /scoresight)
    with requests.Session() as session:
        assert session.get(url, timeout=5).status_code == 200
        [thread] = server_threads()
        assert thread.daemon  # can never keep the app from exiting

        start = time.monotonic()
        http_server.stop_http_server(timeout=3.0)
        elapsed = time.monotonic() - start

    assert elapsed < 3.0, elapsed
    assert not thread.is_alive()
    assert http_server.get_http_server_port() is None
    with pytest.raises(requests.ConnectionError):
        requests.get(url, timeout=0.5)


def test_server_can_be_restarted_on_the_same_port():
    http_server.start_http_server(port=0)
    port = http_server.get_http_server_port()
    assert requests.get(f"http://127.0.0.1:{port}/json", timeout=5).ok
    http_server.stop_http_server()

    http_server.start_http_server(port=port)
    assert http_server.get_http_server_port() == port
    assert requests.get(f"http://127.0.0.1:{port}/json", timeout=5).ok


def open_event_stream(port):
    """An open /events stream (the overlays' live values), its snapshot read."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", "/events")
    response = connection.getresponse()
    assert response.status == 200
    while b"event: snapshot" not in response.readline():
        pass
    assert response.readline().startswith(b"data: ")
    assert response.readline() == b"\n"
    return connection, response


def test_stop_does_not_wait_for_overlay_streams(make_result):
    http_server.update_http_server([])
    http_server.start_http_server(port=0)
    port = http_server.get_http_server_port()
    connection, response = open_event_stream(port)
    try:
        start = time.monotonic()
        http_server.stop_http_server(timeout=3.0)
        # the streams end at once: no graceful-shutdown timeout
        assert time.monotonic() - start < 0.9
        # the stream ended cleanly
        assert response.read() == b""
    finally:
        connection.close()

    # after a restart (a new event loop) the streams get the changes again
    http_server.start_http_server(port=0)
    port = http_server.get_http_server_port()
    connection, response = open_event_stream(port)
    try:
        http_server.update_http_server([make_result("Home Score", "3")])
        lines = []
        while not lines or not lines[-1].startswith(b"data: "):
            line = response.readline()
            if line.strip():
                lines.append(line)
        assert lines[-2] == b"event: change\n"
        assert b'"Home Score":{"value":"3","state":"Success"}' in lines[-1]
    finally:
        connection.close()
        http_server.update_http_server([])


def test_starting_twice_keeps_one_server(caplog):
    http_server.start_http_server(port=0)
    port = http_server.get_http_server_port()
    with caplog.at_level(logging.WARNING):
        http_server.start_http_server(port=0)
    assert http_server.get_http_server_port() == port
    assert len(server_threads()) == 1
    assert "already running" in caplog.text


def test_busy_port_is_logged_and_the_app_continues(blocker, caplog):
    port = blocker.getsockname()[1]
    with caplog.at_level(logging.ERROR):
        http_server.start_http_server(port=port)  # must not raise
    assert http_server.get_http_server_port() is None
    assert server_threads() == []
    assert f"port {port} is already in use" in caplog.text
    # the window's HTTP server status says why it isn't running
    assert http_server.http_server_stats().start_error

    http_server.start_http_server(port=0)  # e.g. another --port
    assert http_server.http_server_stats().start_error is None


def test_stop_never_touches_a_server_it_did_not_start(blocker, monkeypatch):
    # The old stop_http_server() sent GET /shutdown to whatever listened on the
    # port, so closing a second instance killed the first one.
    port = blocker.getsockname()[1]
    monkeypatch.setattr(http_server, "DEFAULT_PORT", port)
    monkeypatch.setattr(http_server, "PORT", port, raising=False)
    http_server.start_http_server(port=port)  # fails: port busy

    http_server.stop_http_server()

    with pytest.raises(TimeoutError):
        conn, _ = blocker.accept()
        conn.close()


def test_stop_when_never_started_is_a_noop():
    start = time.monotonic()
    http_server.stop_http_server()
    assert time.monotonic() - start < 0.5

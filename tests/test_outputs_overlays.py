"""The browser overlays (src/overlays.py) and their live values
(src/overlay_feed.py): the Server-Sent Events stream, the built-in overlay
pages and their query parameters, the custom overlays folder and client.js,
over HTTP against a real server on a free port."""

import asyncio
import http.client
import json
import os
import re
import threading
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from starlette.datastructures import QueryParams

import http_server
import overlay_feed
import overlays
from resource_path import resource_path

# --- helpers ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def server():
    """(host, port) of the server these tests share (stopping it is tested in
    test_outputs_http_lifecycle.py)."""
    http_server.update_http_server([])
    http_server.start_http_server(port=0)
    port = http_server.get_http_server_port()
    assert port
    yield "127.0.0.1", port
    http_server.stop_http_server()
    http_server.update_http_server([])


@pytest.fixture
def base_url(server):
    host, port = server
    return f"http://{host}:{port}"


@pytest.fixture
def publish(make_result):
    def _publish(*readings):
        http_server.update_http_server([make_result(*r) for r in readings])

    yield _publish
    http_server.update_http_server([])


class SSEClient:
    """A minimal EventSource: reads the events of /events over a raw
    connection."""

    def __init__(self, server, path="/events", headers=None):
        self.connection = http.client.HTTPConnection(*server, timeout=5)
        self.connection.request("GET", path, headers=headers or {})
        self.response = self.connection.getresponse()
        self.retry = None

    def next_event(self) -> tuple[str, dict]:
        event, data = "message", []
        while True:
            line = self.response.readline()
            if not line:
                raise EOFError("the stream ended")
            line = line.decode("utf-8").rstrip("\r\n")
            if not line:
                if data:
                    return event, json.loads("\n".join(data))
                event = "message"
                continue
            name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if name == "event":
                event = value
            elif name == "data":
                data.append(value)
            elif name == "retry":
                self.retry = int(value)

    def close(self):
        self.connection.close()


@pytest.fixture
def sse(server):
    clients = []

    def _open(**kwargs):
        client = SSEClient(server, **kwargs)
        clients.append(client)
        return client

    yield _open
    for client in clients:
        client.close()


def raw_get(server, path) -> tuple[int, dict, bytes]:
    """GET path exactly as given (requests would normalize "..")."""
    connection = http.client.HTTPConnection(*server, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


class _Elements(HTMLParser):
    """The start tags of a page (tag, attributes) and its inline scripts."""

    def __init__(self):
        super().__init__()
        self.tags = []
        self.inline_scripts = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        self._in_script = tag == "script" and "src" not in attrs

    def handle_endtag(self, tag):
        self._in_script = False

    def handle_data(self, data):
        if self._in_script and data.strip():
            self.inline_scripts.append(data)


def parse_page(text: str) -> _Elements:
    parser = _Elements()
    parser.feed(text)
    parser.close()
    return parser


def field_names(page: _Elements) -> list[str]:
    return [attrs["data-field"] for _, attrs in page.tags if "data-field" in attrs]


class _Reading:
    def __init__(self, name, text, state):
        self.name, self.text, self.state = name, text, state


# --- the live feed -----------------------------------------------------------------


def test_feed_keeps_the_last_good_value():
    feed = overlay_feed.LiveFeed()
    feed.publish([_Reading("Score", "", "FailedFilter")])
    assert feed.snapshot()["fields"] == {
        "Score": {"value": None, "state": "FailedFilter"}
    }
    feed.publish([_Reading("Score", "12", "Success")])
    feed.publish([_Reading("Score", "1?", "FailedFilter")])
    assert feed.snapshot()["fields"]["Score"] == {
        "value": "12",
        "state": "FailedFilter",
    }
    feed.publish([_Reading("Score", "", "Empty")])
    assert feed.snapshot()["fields"]["Score"] == {"value": "12", "state": "Empty"}
    feed.publish([_Reading("Score", "12", "SameNoChange")])
    assert feed.snapshot()["fields"]["Score"] == {
        "value": "12",
        "state": "SameNoChange",
    }


def test_feed_version_changes_only_when_something_changes():
    feed = overlay_feed.LiveFeed()
    feed.publish([_Reading("A", "1", "Success"), _Reading("B", "2", "Success")])
    version = feed.snapshot()["version"]
    feed.publish([_Reading("A", "1", "Success"), _Reading("B", "2", "Success")])
    assert feed.snapshot()["version"] == version
    feed.publish([_Reading("A", "1", "Success"), _Reading("B", "3", "Success")])
    assert feed.snapshot()["version"] == version + 1
    # the snapshot keeps the boxes' order
    assert list(feed.snapshot()["fields"]) == ["A", "B"]


def drive(feed, steps):
    """Run feed.stream() on an event loop: steps is a list of callables run
    between the chunks (in the loop's thread); returns the chunks."""

    async def run():
        stream = feed.stream()
        chunks = [await stream.__anext__()]
        for step in steps:
            step()
            chunks.append(await asyncio.wait_for(stream.__anext__(), 5))
        await stream.aclose()
        return chunks

    return asyncio.run(run())


def events_in(chunk: bytes) -> list[tuple[str, dict]]:
    return [
        (event.decode(), json.loads(data))
        for event, data in re.findall(rb"event: (\w+)\ndata: (.*)\n\n", chunk)
    ]


def test_stream_sends_only_the_changes():
    feed = overlay_feed.LiveFeed()
    feed.publish([_Reading("A", "1", "Success"), _Reading("B", "2", "Success")])
    chunks = drive(
        feed,
        [
            lambda: feed.publish(
                [_Reading("A", "1", "Success"), _Reading("B", "3", "Success")]
            ),
            lambda: feed.publish([_Reading("A", "1", "Success")]),
        ],
    )
    assert chunks[0].startswith(b"retry: 2000\n\n")
    [(event, data)] = events_in(chunks[0])
    assert event == "snapshot"
    assert data["fields"] == {
        "A": {"value": "1", "state": "Success"},
        "B": {"value": "2", "state": "Success"},
    }
    assert events_in(chunks[1]) == [
        (
            "change",
            {
                "version": data["version"] + 1,
                "fields": {"B": {"value": "3", "state": "Success"}},
            },
        )
    ]
    assert events_in(chunks[2]) == [
        ("change", {"version": data["version"] + 2, "fields": {}, "removed": ["B"]})
    ]


def test_a_stream_that_falls_behind_gets_a_snapshot():
    feed = overlay_feed.LiveFeed()

    def many_changes():
        for i in range(overlay_feed._BACKLOG + 5):
            feed.publish([_Reading("A", str(i), "Success")])

    chunks = drive(feed, [many_changes])
    [(event, data)] = events_in(chunks[1])
    assert event == "snapshot"
    last = str(overlay_feed._BACKLOG + 4)
    assert data["fields"] == {"A": {"value": last, "state": "Success"}}


def test_a_stream_sends_heartbeats(monkeypatch):
    monkeypatch.setattr(overlay_feed, "HEARTBEAT_S", 0.05)
    feed = overlay_feed.LiveFeed()
    chunks = drive(feed, [lambda: None])
    assert events_in(chunks[1]) == [("heartbeat", {"version": 0})]


def test_close_streams_ends_them():
    feed = overlay_feed.LiveFeed()

    async def run():
        stream = feed.stream()
        await stream.__anext__()
        waiting = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0.05)
        assert feed.open_streams() == 1
        # from another thread, as stop_http_server() does
        threading.Thread(target=feed.close_streams).start()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(waiting, 5)
        assert feed.open_streams() == 0

    asyncio.run(run())


def test_publish_never_waits_for_a_stopped_loop():
    # a stream bound the feed to a loop that is closed now (the server stopped)
    feed = overlay_feed.LiveFeed()
    drive(feed, [])
    start = time.monotonic()
    for i in range(100):
        feed.publish([_Reading("A", str(i), "Success")])
    assert time.monotonic() - start < 1.0


# --- /events over HTTP -------------------------------------------------------------


def test_events_start_with_a_snapshot(sse, publish):
    publish(
        ("Home Score", "21", "Success"),
        ("Away Score", "x", "FailedFilter"),
        ("Time", "4:30", "SameNoChange"),
    )
    client = sse()
    assert client.response.status == 200
    assert client.response.getheader("Content-Type").startswith("text/event-stream")
    assert client.response.getheader("Cache-Control") == "no-cache"
    event, data = client.next_event()
    assert client.retry == overlay_feed.RETRY_MS
    assert event == "snapshot"
    assert data["fields"] == {
        "Home Score": {"value": "21", "state": "Success"},
        "Away Score": {"value": None, "state": "FailedFilter"},
        "Time": {"value": "4:30", "state": "SameNoChange"},
    }


def test_events_push_each_change(sse, publish):
    publish(("Home Score", "21", "Success"), ("Time", "4:30", "Success"))
    client = sse()
    _, snapshot = client.next_event()

    publish(("Home Score", "22", "Success"), ("Time", "4:30", "SameNoChange"))
    event, data = client.next_event()
    assert event == "change"
    assert data == {
        "version": snapshot["version"] + 1,
        "fields": {
            "Home Score": {"value": "22", "state": "Success"},
            "Time": {"value": "4:30", "state": "SameNoChange"},
        },
    }
    # a rejected reading keeps the last good value; only its state changes
    publish(("Home Score", "2?", "FailedFilter"), ("Time", "4:30", "SameNoChange"))
    event, data = client.next_event()
    assert data["fields"] == {"Home Score": {"value": "22", "state": "FailedFilter"}}
    # a deleted box is removed
    publish(("Time", "4:29", "Success"))
    event, data = client.next_event()
    assert data["fields"] == {"Time": {"value": "4:29", "state": "Success"}}
    assert data["removed"] == ["Home Score"]


def test_a_reconnecting_client_gets_the_current_values(sse, publish):
    publish(("Home Score", "21", "Success"))
    first = sse()
    first.next_event()
    first.close()
    # the server notices the disconnect and forgets the stream
    wait_until(lambda: overlay_feed.feed.open_streams() == 0)

    publish(("Home Score", "24", "Success"))
    again = sse()
    event, data = again.next_event()
    assert event == "snapshot"
    assert data["fields"]["Home Score"] == {"value": "24", "state": "Success"}


def test_many_clients_get_every_change(sse, publish):
    publish(("Home Score", "1", "Success"))
    clients = [sse() for _ in range(25)]
    for client in clients:
        assert client.next_event()[0] == "snapshot"
    wait_until(lambda: overlay_feed.feed.open_streams() == 25)

    start = time.monotonic()
    publish(("Home Score", "2", "Success"))
    # publishing (the GUI thread's side) doesn't wait for the clients
    assert time.monotonic() - start < 0.5
    for client in clients:
        event, data = client.next_event()
        assert (event, data["fields"]) == (
            "change",
            {"Home Score": {"value": "2", "state": "Success"}},
        )


def test_events_are_readable_from_other_origins(sse):
    # a template opened as file:// (origin "null") or from another server
    client = sse(headers={"Origin": "null"})
    client.next_event()
    assert client.response.getheader("Access-Control-Allow-Origin") == "*"


def test_streams_count_as_connected_clients(sse, monkeypatch):
    monkeypatch.setattr(overlay_feed, "HEARTBEAT_S", 0.05)
    client = sse()
    client.next_event()
    client.next_event()  # a heartbeat
    stats = http_server.http_server_stats(now=time.monotonic() + 1)
    assert stats.recent_clients >= 1


def test_state_is_the_polling_fallback(base_url, publish):
    publish(("Home Score", "21", "Success"), ("Period", "", "Empty"))
    response = requests.get(base_url + "/overlay/state", timeout=5)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["fields"] == {
        "Home Score": {"value": "21", "state": "Success"},
        "Period": {"value": None, "state": "Empty"},
    }


# --- client.js and the styles --------------------------------------------------------


def test_client_js_is_served(base_url):
    response = requests.get(base_url + "/overlay/client.js", timeout=5)
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/javascript")
    text = response.text
    # served as it is on disk (with CRLF line ends in a Windows checkout)
    with open(
        resource_path("overlays", "client.js"), encoding="utf-8", newline=""
    ) as f:
        assert text == f.read()
    for part in ("EventSource", "/events", "/overlay/state", "data-field"):
        assert part in text
    # the values go in as text, never as HTML
    assert "innerHTML" not in text and "insertAdjacentHTML" not in text


@pytest.mark.parametrize("name", ["client.js", "client-worker.js"])
def test_client_js_is_plain_es5(name):
    """The client runs in old embedded browsers (CasparCG 2.3's Chromium 71):
    no arrow functions, let/const, template strings, classes, ?., ??,
    async/await, spread or default parameters."""
    with open(resource_path("overlays", name), encoding="utf-8") as f:
        code = f.read()
    # without comments and strings
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    code = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', code)
    code = re.sub(r"/(?:[^/\\\n]|\\.)+/[gimsuy]*", "/re/", code)
    for pattern in (
        r"=>",
        r"\blet\b",
        r"\bconst\b",
        r"`",
        r"\bclass\b",
        r"\?\.",
        r"\?\?",
        r"\basync\b",
        r"\bawait\b",
        r"\.\.\.",
        r"function\s*\w*\s*\([^)]*=",
        r"\bfor\s*\(\s*var\s+\w+\s+of\b",
    ):
        assert not re.search(pattern, code), (name, pattern)


def test_overlay_css_is_served(base_url):
    response = requests.get(base_url + "/overlay/overlay.css", timeout=5)
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/css")
    assert "background: transparent" in response.text


# --- the built-in overlays -------------------------------------------------------------


@pytest.mark.parametrize("kind", overlays.OVERLAY_KINDS)
def test_builtin_overlays_render_the_standard_fields(base_url, kind):
    response = requests.get(f"{base_url}/overlay/{kind}", timeout=5)
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/html")
    assert response.headers["Content-Security-Policy"] == overlays.BUILTIN_CSP
    page = parse_page(response.text)
    assert field_names(page) == list(overlays.DEFAULT_FIELDS[kind])
    # the live values come from client.js; no inline script
    scripts = [attrs for tag, attrs in page.tags if tag == "script"]
    assert scripts == [
        {"src": "/overlay/client.js", "data-transport": "auto", "data-poll-ms": "500"}
    ]
    assert page.inline_scripts == []
    [body] = [attrs for tag, attrs in page.tags if tag == "body"]
    assert f"ss-theme-{overlays.DEFAULT_THEME[kind]}" in body["class"]


def test_the_scorebug_takes_its_options_from_the_query(base_url):
    query = (
        "fields=Home%20Score,Away%20Score,Time,Period,Shot%20Clock"
        "&labels=Lions,Tigers,,QTR&theme=light&layout=stack&position=bottom-right"
        "&animate=fade&fit=none&scale=1.5&accent=ff0000&home=%23123&away=navy"
        "&font=Arial%20Black&transport=poll&poll=250"
    )
    text = requests.get(f"{base_url}/overlay/scorebug?{query}", timeout=5).text
    page = parse_page(text)
    assert field_names(page) == [
        "Home Score",
        "Away Score",
        "Time",
        "Period",
        "Shot Clock",
    ]
    for label in ("Lions", "Tigers", "QTR", "Shot Clock"):
        assert f">{label}<" in text
    [html_tag] = [attrs for tag, attrs in page.tags if tag == "html"]
    assert html_tag["class"] == "ss-fit-none"
    [body] = [attrs for tag, attrs in page.tags if tag == "body"]
    assert body["class"] == (
        "ss-overlay ss-kind-scorebug ss-theme-light ss-pos-bottom-right"
    )
    assert body["data-animate"] == "fade"
    assert "ss-layout-stack" in text
    for css in (
        "--scale: 1.5;",
        "--accent: #ff0000;",
        "--home: #123;",
        "--away: navy;",
        '--font: "Arial Black", Bahnschrift',
    ):
        assert css in text
    [script] = [attrs for tag, attrs in page.tags if tag == "script"]
    assert (script["data-transport"], script["data-poll-ms"]) == ("poll", "250")


def test_the_single_field_overlay_takes_a_name(base_url):
    text = requests.get(
        f"{base_url}/overlay/field?name=Shot%20Clock&label=Shot", timeout=5
    ).text
    assert field_names(parse_page(text)) == ["Shot Clock"]
    assert ">Shot<" in text


def test_the_lower_third_has_a_title_and_subtitle(base_url):
    text = requests.get(
        f"{base_url}/overlay/lower-third?title=Final&subtitle=Live%20from%20the%20arena",
        timeout=5,
    ).text
    assert ">Final<" in text and ">Live from the arena<" in text
    assert field_names(parse_page(text)) == list(
        overlays.DEFAULT_FIELDS[overlays.LOWER_THIRD]
    )


INJECTIONS = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "</style><script>alert(1)</script>",
    "' onmouseover='alert(1)",
]


@pytest.mark.parametrize("kind", overlays.OVERLAY_KINDS)
def test_query_text_cannot_inject_markup(base_url, kind):
    params = {
        "fields": ",".join(INJECTIONS),
        "name": INJECTIONS[1],
        "labels": ",".join(INJECTIONS),
        "title": INJECTIONS[2],
        "subtitle": INJECTIONS[0],
        "accent": "red;}body{display:none",
        "bg": "url(javascript:alert(1))",
        "font": 'x"; } body { display: none',
        "theme": "<b>",
        "position": "javascript:alert(1)",
        "animate": '"><script>',
        "scale": "1;}*{color:red",
    }
    response = requests.get(f"{base_url}/overlay/{kind}", params=params, timeout=5)
    assert response.status_code == 200
    text = response.text
    page = parse_page(text)
    assert page.inline_scripts == []
    assert [tag for tag, _ in page.tags].count("script") == 1
    assert not any(tag == "img" for tag, _ in page.tags)
    assert "<script>alert" not in text
    assert "onerror=" not in text.replace("onerror=alert(1)&gt;", "")
    assert "display:none" not in text.replace(" ", "")
    assert "javascript:" not in text
    # the texts are there, escaped
    for name in field_names(page):
        assert name in INJECTIONS
    # and every attribute is one the page is made of
    for tag, attrs in page.tags:
        assert not any(name.startswith("on") for name in attrs), (tag, attrs)


def test_parse_options_defaults_and_limits():
    def parse(kind, query):
        return overlays.parse_options(kind, QueryParams(query))

    options = parse(overlays.SCOREBUG, "")
    assert options.fields == overlays.DEFAULT_FIELDS[overlays.SCOREBUG]
    assert (options.theme, options.position, options.scale) == ("dark", "top-left", 1)
    assert options.font == overlays.FONTS[overlays.DEFAULT_FONT]
    # both list forms, in order; names with a comma use the repeated form
    options = parse(overlays.SCOREBUG, "fields=A,%20B%20&field=C,%20D&field=E")
    assert options.fields == ("A", "B", "C, D", "E")
    # empty names are dropped, but empty labels keep their place
    options = parse(overlays.CLOCK, "fields=,Time,,&labels=,,QTR")
    assert options.fields == ("Time",)
    assert options.labels == ("", "", "QTR")
    assert options.label(0, "x") == "x" and options.label(2, "x") == "QTR"
    # at most MAX_FIELDS, each at most MAX_NAME_LENGTH characters
    options = parse(overlays.LOWER_THIRD, "fields=" + ",".join(["x" * 500] * 100))
    assert len(options.fields) == overlays.MAX_FIELDS
    assert len(options.fields[0]) == overlays.MAX_NAME_LENGTH
    # control characters are removed
    assert parse(overlays.FIELD, "name=A%0Ab%00c").fields == ("Abc",)
    # the single field: "name" first, else the first of "fields"
    assert parse(overlays.FIELD, "name=X&fields=Y,Z").fields == ("X",)
    assert parse(overlays.FIELD, "fields=Y,Z").fields == ("Y",)
    assert parse(overlays.FIELD, "fields=,Y").fields == ("Y",)
    assert parse(overlays.FIELD, "").fields == ("Time",)
    # numbers are clamped; nonsense falls back to the default
    assert parse(overlays.FIELD, "scale=100").scale == 4.0
    assert parse(overlays.FIELD, "scale=0").scale == 0.25
    for bad in ("nan", "inf", "-inf", "abc", ""):
        assert parse(overlays.FIELD, f"scale={bad}").scale == 1.0
    assert parse(overlays.FIELD, "poll=1").poll_ms == 100
    assert parse(overlays.FIELD, "poll=99999").poll_ms == 10000
    # choices are case-insensitive and validated
    options = parse(overlays.FIELD, "theme=LIGHT&position=Bottom&animate=none")
    assert (options.theme, options.position, options.animate) == (
        "light",
        "bottom",
        "none",
    )
    options = parse(overlays.FIELD, "theme=neon&position=middle&layout=grid")
    assert (options.theme, options.position, options.layout) == (
        "minimal",
        "center",
        "row",
    )


@pytest.mark.parametrize(
    "value, expected",
    [
        ("ff0000", "#ff0000"),
        ("#FFF", "#fff"),
        ("11223344", "#11223344"),
        ("Navy", "navy"),
        ("transparent", "transparent"),
        ("12345", None),
        ("rgb(1,2,3)", None),
        ("red;color:blue", None),
        ("notacolor", None),
        ("", None),
    ],
)
def test_parse_color(value, expected):
    assert overlays.parse_color(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("mono", overlays.FONTS["mono"]),
        ("Serif", overlays.FONTS["serif"]),
        ("Arial Black", f'"Arial Black", {overlays.FONTS["condensed"]}'),
        ('Arial"', overlays.FONTS["condensed"]),
        ("a;b", overlays.FONTS["condensed"]),
        ("x" * 65, overlays.FONTS["condensed"]),
    ],
)
def test_parse_font(value, expected):
    assert overlays.parse_font(value) == expected


def test_overlay_url_leaves_out_the_defaults():
    base = "http://192.168.1.20:18099"
    assert overlays.overlay_url(base, "scorebug") == f"{base}/overlay/scorebug"
    assert (
        overlays.overlay_url(
            base, "scorebug", list(overlays.DEFAULT_FIELDS["scorebug"]), theme="dark"
        )
        == f"{base}/overlay/scorebug"
    )
    assert (
        overlays.overlay_url(base, "clock", ["Time", "Shot Clock"], "light", 1.5)
        == f"{base}/overlay/clock?fields=Time,Shot%20Clock&theme=light&scale=1.5"
    )
    assert (
        overlays.overlay_url(base, "field", ["Home Score"])
        == f"{base}/overlay/field?name=Home%20Score"
    )
    # a name with a comma: one parameter per field
    url = overlays.overlay_url(base, "lower-third", ["A, B", "C & D"])
    assert url == f"{base}/overlay/lower-third?field=A,%20B&field=C%20%26%20D"


@pytest.mark.parametrize(
    "kind, fields",
    [
        ("scorebug", ["Home Score", "Away Score"]),
        ("lower-third", ["A, B", "Q&A", "50%", "x=y"]),
        ("clock", ["Time"]),
        ("field", ["Ünïcødé"]),
    ],
)
def test_overlay_url_round_trips(kind, fields):
    url = overlays.overlay_url("http://h:1", kind, fields)
    query = urlsplit(url).query
    options = overlays.parse_options(kind, QueryParams(query))
    assert list(options.fields) == fields
    assert parse_qs(query)  # a well-formed query


def test_overlay_url_with_team_names():
    base = "http://h:1"
    assert (
        overlays.overlay_url(base, "scorebug", labels=["Lions", "Tigers"])
        == f"{base}/overlay/scorebug?labels=Lions,Tigers"
    )
    # an empty name keeps the overlay's own; trailing ones are left out
    assert overlays.overlay_url(base, "scorebug", labels=["", "Tigers"]) == (
        f"{base}/overlay/scorebug?labels=,Tigers"
    )
    assert overlays.overlay_url(base, "scorebug", labels=[" Lions ", " "]) == (
        f"{base}/overlay/scorebug?labels=Lions"
    )
    assert overlays.overlay_url(base, "scorebug", labels=["", ""]) == (
        f"{base}/overlay/scorebug"
    )


@pytest.mark.parametrize(
    "labels",
    [
        ["Lions", "Tigers"],
        ["", "St. Mary's"],
        ["Smith, Jones & Co", ""],
        ["Ünïcødé", "50% = x"],
    ],
)
def test_team_names_round_trip(labels):
    url = overlays.overlay_url("http://h:1", "scorebug", labels=labels)
    options = overlays.parse_options("scorebug", QueryParams(urlsplit(url).query))
    assert options.label(0, "HOME") == (labels[0] or "HOME")
    assert options.label(1, "AWAY") == (labels[1] or "AWAY")
    html = overlays.render_overlay(options)
    for name in labels:
        assert overlays._esc(name) in html


def test_default_team_names():
    assert overlays.default_team_names("scorebug", ["Time"]) == ("HOME", "AWAY")
    assert overlays.default_team_names(
        "lower-third", overlays.DEFAULT_FIELDS["lower-third"]
    ) == ("Home", "Away")
    assert overlays.default_team_names("lower-third", ["Period"]) == ("Period", "")


# --- custom overlays -------------------------------------------------------------------


@pytest.fixture
def overlay_files(data_dir):
    """An overlays folder with a few files, and a secret next to it."""
    folder = data_dir / "overlays"
    (folder / "sub").mkdir(parents=True)
    (folder / "page.html").write_text("<p>page</p>", encoding="utf-8")
    (folder / "a&b.html").write_text("<p>ab</p>", encoding="utf-8")
    (folder / "sub" / "inner.html").write_text("<p>inner</p>", encoding="utf-8")
    (folder / "style.css").write_text("p {}", encoding="utf-8")
    (folder / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (folder / "script.py").write_text("print('no')", encoding="utf-8")
    (folder / ".hidden.html").write_text("hidden", encoding="utf-8")
    (data_dir / "scoresight.json").write_text('{"secret": 1}', encoding="utf-8")
    (data_dir / "outside.html").write_text("outside", encoding="utf-8")
    return folder


@pytest.mark.parametrize(
    "path, content_type, body",
    [
        ("page.html", "text/html", b"<p>page</p>"),
        ("sub/inner.html", "text/html", b"<p>inner</p>"),
        ("a%26b.html", "text/html", b"<p>ab</p>"),
        ("style.css", "text/css", b"p {}"),
        ("logo.png", "image/png", b"\x89PNG\r\n\x1a\n"),
    ],
)
def test_custom_overlays_are_served(server, overlay_files, path, content_type, body):
    status, headers, content = raw_get(server, "/overlay/custom/" + path)
    assert status == 200
    assert headers["content-type"].startswith(content_type)
    assert headers["x-content-type-options"] == "nosniff"
    assert content == body


@pytest.mark.parametrize(
    "path",
    [
        "../scoresight.json",
        "..%2Fscoresight.json",
        "%2e%2e/scoresight.json",
        "%2E%2E%2Foutside.html",
        "..%5Coutside.html",
        "sub/../../outside.html",
        "sub/..%5C..%5Coutside.html",
        "/outside.html",
        "%2Foutside.html",
        "C:%5CWindows%5Cwin.ini",
        "C:/Windows/win.ini",
        "page.html%00.png",
        "page.html::$DATA",
        ".hidden.html",
        "sub/.hidden.html",
        "nul.html",
        "CON.html",
        "script.py",
        "sub",
        "sub/",
        "missing.html",
        "",
    ],
)
def test_custom_overlays_stay_inside_the_folder(server, overlay_files, path):
    status, _, content = raw_get(server, "/overlay/custom/" + path)
    assert status == 404, (path, content)
    assert b"secret" not in content and b"outside" not in content


def test_a_link_out_of_the_overlays_folder_is_not_followed(server, overlay_files):
    target = overlay_files.parent / "outside.html"
    try:
        os.symlink(target, overlay_files / "link.html")
    except (OSError, NotImplementedError):
        pytest.skip("can't create symbolic links here")
    status, _, content = raw_get(server, "/overlay/custom/link.html")
    assert status == 404
    assert b"outside" not in content


def test_custom_overlay_file_checks(overlay_files):
    assert overlays.custom_overlay_file("page.html") == os.path.realpath(
        overlay_files / "page.html"
    )
    for path in ("..\\scoresight.json", "sub\\..\\page.html", "x/../page.html"):
        assert overlays.custom_overlay_file(path) is None


def test_the_overlays_list_stays_small_and_shallow(data_dir, monkeypatch):
    folder = data_dir / "overlays"
    deep = folder / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    for path in (folder, folder / "a", folder / "a" / "b", folder / "a" / "b" / "c"):
        (path / "x.html").write_text("x", encoding="utf-8")
    (deep / "too-deep.html").write_text("x", encoding="utf-8")
    assert overlays.custom_overlays() == [
        "x.html",
        "a/x.html",
        "a/b/x.html",
        "a/b/c/x.html",
    ]
    # a big folder (say node_modules) stops the walk
    big = folder / "node_modules"
    big.mkdir()
    for i in range(50):
        (big / f"{i}.js").write_text("", encoding="utf-8")
    (folder / "zz").mkdir()
    (folder / "zz" / "late.html").write_text("x", encoding="utf-8")
    assert "zz/late.html" in overlays.custom_overlays()
    monkeypatch.setattr(overlays, "MAX_VISITED", 20)
    assert "zz/late.html" not in overlays.custom_overlays()


def test_the_overlays_folder_is_made_with_the_example(data_dir):
    folder = overlays.ensure_overlays_dir()
    assert folder == str(data_dir / "overlays")
    example = data_dir / "overlays" / overlays.EXAMPLE_NAME
    with open(resource_path("overlays", "example.html"), "rb") as f:
        assert example.read_bytes() == f.read()
    # the user's changes and deletions stay
    example.write_text("mine", encoding="utf-8")
    overlays.ensure_overlays_dir()
    assert example.read_text(encoding="utf-8") == "mine"
    example.unlink()
    overlays.ensure_overlays_dir()
    assert not example.exists()
    assert overlays.custom_overlays() == []


def test_the_example_follows_the_contract():
    with open(resource_path("overlays", "example.html"), encoding="utf-8") as f:
        page = parse_page(f.read())
    assert ("script", {"src": "/overlay/client.js"}) in page.tags
    assert set(field_names(page)) >= {"Home Score", "Away Score", "Time", "Period"}
    assert "ScoreSight.on(" in page.inline_scripts[0]


def test_the_index_lists_the_overlays(base_url, overlay_files, publish):
    publish(("Home Score", "21", "Success"))
    response = requests.get(base_url + "/overlay", timeout=5)
    assert response.status_code == 200
    text = response.text
    for kind in overlays.OVERLAY_KINDS:
        assert f'href="/overlay/{kind}' in text
    assert overlays.custom_overlays() == ["a&b.html", "page.html", "sub/inner.html"]
    assert 'href="/overlay/custom/a%26b.html">a&amp;b.html</a>' in text
    assert 'href="/overlay/custom/sub/inner.html"' in text
    assert ".hidden" not in text and "script.py" not in text
    assert 'data-field="Home Score"' in text
    assert parse_page(text).inline_scripts == []

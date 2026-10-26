"""The TCP / UDP output (tcp_output.py) against real sockets on 127.0.0.1: each
preset's exact bytes, escaping, reconnecting, backoff, changes only, the
minimum interval, replies and the status."""

import socket
import sys
import threading
import time
from dataclasses import replace

import pytest

import output_status
import tcp_output
from tcp_output import (
    CASPARCG,
    CHYRON,
    CUSTOM,
    LIVE_SCORE,
    MAGICSOFT,
    PER_FIELD,
    PER_FRAME,
    TCP_SERVER,
    UDP,
    VIZRT,
    WEBSOCKET,
    WEBSOCKET_JSON,
    TcpConfig,
    TcpOutput,
    default_field_name,
    escape_amcp,
    escape_chyron,
    escape_json,
    escape_line,
    escape_xml,
    identifier_name,
    preview,
    render_message,
    render_text,
    snake_name,
    values_xml,
    visible_text,
    websocket_target,
)
from websocket_server import WebSocketServer

WAIT = 10.0  # seconds to wait for something to arrive


def wait_until(predicate, timeout=WAIT, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Connection:
    def __init__(self, sock):
        self.sock = sock
        self.data = bytearray()
        self.chunks: list[tuple[float, bytes]] = []  # (arrival time, bytes)
        self.closed = False


def stop_listening(sock: socket.socket):
    """Close a listening socket so that it accepts nothing more. On Linux,
    closing it doesn't wake an accept() blocked in another thread, which goes
    on accepting connections: shutting it down first does."""
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass  # Windows: not connected
    sock.close()


class TcpServer:
    """Listens on 127.0.0.1, records what each connection sends, and answers
    each received line with reply(line) (bytes, or None for no answer)."""

    def __init__(self, port: int = 0, reply=None):
        self.reply = reply
        self.connections: list[Connection] = []
        self._cond = threading.Condition()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform != "win32":
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._stopped = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self._stopped:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            if self._stopped:
                client.close()
                return
            connection = Connection(client)
            with self._cond:
                self.connections.append(connection)
                self._cond.notify_all()
            threading.Thread(target=self._read, args=(connection,), daemon=True).start()

    def _read(self, connection: Connection):
        pending = b""
        while True:
            try:
                data = connection.sock.recv(65536)
            except OSError:
                data = b""
            if not data:
                with self._cond:
                    connection.closed = True
                    self._cond.notify_all()
                return
            with self._cond:
                connection.data += data
                connection.chunks.append((time.monotonic(), data))
                self._cond.notify_all()
            if self.reply is not None:
                pending += data
                *lines, pending = pending.split(b"\n")
                for line in lines:
                    answer = self.reply(line)
                    if answer:
                        try:
                            connection.sock.sendall(answer)
                        except OSError:
                            pass

    def received(self, index: int = -1) -> bytes:
        with self._cond:
            if not self.connections:
                return b""
            return bytes(self.connections[index].data)

    def all_received(self) -> bytes:
        with self._cond:
            return b"".join(bytes(c.data) for c in self.connections)

    def wait_for(self, expected: bytes, index: int = -1, timeout=WAIT) -> bytes:
        """Wait until connection index received expected (at its end)."""
        assert wait_until(lambda: self.received(index).endswith(expected), timeout), (
            self.received(index),
            expected,
        )
        return self.received(index)

    def wait_connections(self, count: int, timeout=WAIT):
        assert wait_until(lambda: len(self.connections) >= count, timeout), len(
            self.connections
        )

    def drop_all(self):
        """Close every connection (the receiver going away)."""
        with self._cond:
            connections = list(self.connections)
        for connection in connections:
            try:
                connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.sock.close()

    def close(self):
        self._stopped = True
        stop_listening(self.sock)
        self.drop_all()


class UdpServer:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.datagrams: list[bytes] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(65536)
            except OSError:
                return
            with self._lock:
                self.datagrams.append(data)

    def received(self) -> list[bytes]:
        with self._lock:
            return list(self.datagrams)

    def close(self):
        self.sock.close()


@pytest.fixture
def servers():
    created = []

    def _make(**kwargs):
        server = TcpServer(**kwargs)
        created.append(server)
        return server

    yield _make
    for server in created:
        server.close()


@pytest.fixture
def make_output():
    created = []

    def _make(config: TcpConfig, mapping=None):
        output = TcpOutput(config, mapping)
        created.append(output)
        return output

    yield _make
    for output in created:
        output.close()


def config_for(preset: str, port: int, **changes) -> TcpConfig:
    config = TcpConfig(host="127.0.0.1").with_preset(preset)
    return TcpConfig.from_dict({**config.to_dict(), "port": port, **changes})


def connected(output: TcpOutput) -> bool:
    return output.connection_status().connected


def tcp_status(output: TcpOutput, has_fields=True, product="Test"):
    return output_status.tcp_status(
        output.running,
        output.config,
        product,
        has_fields,
        output.status(),
        output.connection_status(),
    )


# --- the presets' bytes ------------------------------------------------------------


def test_live_score_sends_one_json_line_with_every_field(
    servers, make_output, make_result
):
    server = servers()
    output = make_output(config_for(LIVE_SCORE, server.port, min_interval=0))
    output.start()
    server.wait_connections(1)
    assert wait_until(lambda: connected(output))
    output.update([make_result("Home Score", "21"), make_result("Time", "3:12")])
    server.wait_for(
        b'{"type":"ocr", "values":{"ScoreHome": "21", "GameClock": "3:12"}}\n'
    )
    # a change sends every field again (Live Score gets the whole set)
    output.update([make_result("Home Score", "23"), make_result("Time", "3:12")])
    assert server.wait_for(
        b'{"type":"ocr", "values":{"ScoreHome": "23", "GameClock": "3:12"}}\n'
    ) == (
        b'{"type":"ocr", "values":{"ScoreHome": "21", "GameClock": "3:12"}}\n'
        b'{"type":"ocr", "values":{"ScoreHome": "23", "GameClock": "3:12"}}\n'
    )


def test_casparcg_adds_the_template_then_updates_it(servers, make_output, make_result):
    server = servers(reply=lambda line: b"202 CG OK\r\n")
    output = make_output(config_for(CASPARCG, server.port))
    output.start()
    # on connect: CG ADD (with the values known so far: none)
    server.wait_for(
        b'CG 1-20 ADD 1 "scoresight_scorebug" 1 "<templateData></templateData>"\r\n'
    )
    output.update([make_result("Home Score", "21"), make_result("Away Score", "7")])
    server.wait_for(
        b'CG 1-20 UPDATE 1 "<templateData>'
        b'<componentData id=\\"HomeScore\\"><data id=\\"text\\" value=\\"21\\"/>'
        b"</componentData>"
        b'<componentData id=\\"AwayScore\\"><data id=\\"text\\" value=\\"7\\"/>'
        b'</componentData></templateData>"\r\n'
    )
    assert wait_until(lambda: output.connection_status().last_reply == "202 CG OK"), (
        output.connection_status()
    )
    # Play and Stop, on the same connection
    output.send_command("CG {channel}-{layer} PLAY {cg_layer}")
    server.wait_for(b"CG 1-20 PLAY 1\r\n")
    output.send_command("CG {channel}-{layer} STOP {cg_layer}")
    server.wait_for(b"CG 1-20 STOP 1\r\n")
    assert len(server.connections) == 1


def test_casparcg_json_data(servers, make_output, make_result):
    server = servers()
    output = make_output(
        config_for(
            CASPARCG,
            server.port,
            template='CG {channel}-{layer} UPDATE {cg_layer} "{json}"',
            init="",
            channel=2,
            layer=10,
            cg_layer=0,
        )
    )
    output.start()
    assert wait_until(lambda: connected(output))
    output.update([make_result("Home Score", "21"), make_result("Time", '1"2')])
    server.wait_for(
        b'CG 2-10 UPDATE 0 "{\\"HomeScore\\": \\"21\\", \\"Time\\": \\"1\\\\\\"2\\"}"\r\n'
    )


def test_magicsoft_sends_a_bracketed_line_per_changed_field(
    servers, make_output, make_result
):
    server = servers()
    output = make_output(config_for(MAGICSOFT, server.port))
    output.start()
    assert wait_until(lambda: connected(output))
    output.update([make_result("Time", "2:45")])
    server.wait_for(b"[clock]=2:45\r\n")
    output.update([make_result("Time", "2:45"), make_result("Home Score", "3")])
    assert server.wait_for(b"[home_score]=3\r\n") == (
        b"[clock]=2:45\r\n[home_score]=3\r\n"
    )


def test_chyron_sends_an_update_command_per_field_in_latin1(
    servers, make_output, make_result
):
    server = servers()
    output = make_output(config_for(CHYRON, server.port))
    output.start()
    assert wait_until(lambda: connected(output))
    output.update([make_result("Home Score", "21")])
    server.wait_for(b"P\\UPDATE\\1\\*\\Home Score\\21\\\\\r\n")
    # ISO-8859-1, and a backslash can't be inside a field
    output.update([make_result("Period", "Mi-temps\\2é")])
    server.wait_for(b"P\\UPDATE\\1\\*\\Period\\Mi-temps/2\xe9\\\\\r\n")


def test_vizrt_sends_nul_terminated_pairs_over_tcp(servers, make_output, make_result):
    server = servers()
    output = make_output(config_for(VIZRT, server.port))
    output.start()
    assert wait_until(lambda: connected(output))
    output.update([make_result("Home Score", "21"), make_result("Time", "3:12")])
    # several pairs in one send, each ended by NUL
    assert server.wait_for(b"Time|3:12\0") == b"HomeScore|21\0Time|3:12\0"


def test_vizrt_over_udp_sends_a_datagram_per_pair(make_output, make_result):
    server = UdpServer()
    try:
        output = make_output(config_for(VIZRT, server.port, transport=UDP))
        output.start()
        output.update([make_result("Home Score", "21"), make_result("Time", "3:12")])
        assert wait_until(lambda: len(server.received()) == 2)
        assert server.received() == [b"HomeScore|21\0", b"Time|3:12\0"]
        status = tcp_status(output, product="Vizrt (Shared Memory)")
        assert status.state == output_status.OK
        assert status.detail == (
            f"Vizrt (Shared Memory): Sending to 127.0.0.1:{server.port} (UDP)"
        )
    finally:
        server.close()


def test_a_multi_line_template_sends_each_line(servers, make_output, make_result):
    server = servers()
    output = make_output(
        config_for(
            CUSTOM,
            server.port,
            template="SET {field}\n\nVALUE {value}\n",
            line_ending="crlf",
        )
    )
    output.start()
    assert wait_until(lambda: connected(output))
    output.update([make_result("Time", "1:00")])
    # the empty line of the template is left out
    assert server.wait_for(b"VALUE 1:00\r\n") == b"SET Time\r\nVALUE 1:00\r\n"


@pytest.mark.parametrize(
    "encoding, expected",
    [
        ("utf-8", "Période=2€\n".encode("utf-8")),
        ("iso-8859-1", b"P\xe9riode=2?\n"),
        ("cp1252", b"P\xe9riode=2\x80\n"),
        ("utf-16-le", "Période=2€\n".encode("utf-16-le")),
        ("ascii", b"P?riode=2?\n"),
    ],
)
def test_encodings(servers, make_output, make_result, encoding, expected):
    server = servers()
    output = make_output(config_for(CUSTOM, server.port, encoding=encoding))
    output.start()
    assert wait_until(lambda: connected(output))
    output.update([make_result("Période", "2€")])
    assert server.wait_for(expected) == expected


# --- escaping and templates ------------------------------------------------------------


def test_escaping():
    assert escape_amcp('say "hi"\\now\r\nnext\nline\0') == (
        'say \\"hi\\"\\\\now\\nnext\\nline'
    )
    assert escape_json('a "b"\n\\') == 'a \\"b\\"\\n\\\\'
    assert escape_json("é") == "é"
    assert escape_xml("<a & 'b' \"c\">\n\x01") == (
        "&lt;a &amp; &apos;b&apos; &quot;c&quot;&gt;&#10;"
    )
    assert escape_chyron("1\\2\r\n3") == "1/2 3"
    assert escape_line("a\r\nb\0c\nd") == "a b c d"
    assert tcp_output.escape_nul("a\0b") == "ab"


def test_placeholders_are_escaped_and_other_braces_stay():
    config = TcpConfig(escape="json")
    text = render_text(
        '{"{field}": "{value}", "other": {unknown}, "all": {json}}',
        config,
        "Home Score",
        'say "hi"',
        (("Home Score", "21"),),
    )
    # {json} is escaped too: the escaping applies to every placeholder
    assert text == (
        '{"Home Score": "say \\"hi\\"", "other": {unknown}, '
        '"all": {\\"Home Score\\": \\"21\\"}}'
    )
    caspar = TcpConfig().with_preset(CASPARCG)
    assert render_text("{template} {channel}-{layer} {cg_layer}", caspar) == (
        "scoresight_scorebug 1-20 1"
    )


def test_values_xml():
    assert values_xml([("A&B", '<"x">')]) == (
        '<templateData><componentData id="A&amp;B">'
        '<data id="text" value="&lt;&quot;x&quot;&gt;"/>'
        "</componentData></templateData>"
    )


def test_render_message_line_endings():
    for ending, expected in (
        ("crlf", "a=1\r\n"),
        ("lf", "a=1\n"),
        ("cr", "a=1\r"),
        ("nul", "a=1\0"),
        ("none", "a=1"),
    ):
        config = TcpConfig(line_ending=ending)
        assert render_message("{field}={value}", config, "a", "1") == expected


def test_default_names():
    assert identifier_name("Home Score") == "HomeScore"
    assert identifier_name("1st Half") == "_1stHalf"
    assert identifier_name("ShotClock") == "ShotClock"
    assert identifier_name("home score") == "HomeScore"
    assert identifier_name("Score (H)") == "ScoreH"
    assert snake_name("Home Score") == "home_score"
    assert snake_name("!!") == "field"
    assert default_field_name(LIVE_SCORE, "Time") == "GameClock"
    assert default_field_name(LIVE_SCORE, "Away Score") == "ScoreAway"
    assert default_field_name(LIVE_SCORE, "Custom 1") == "Custom 1"
    assert default_field_name(CASPARCG, "Home Score") == "HomeScore"
    # MagicSoft reserves [time]
    assert default_field_name(MAGICSOFT, "Time") == "clock"
    assert default_field_name(MAGICSOFT, "Away Fouls") == "away_fouls"
    assert default_field_name(CHYRON, "Home Score") == "Home Score"
    assert default_field_name(VIZRT, "Shot Clock") == "ShotClock"
    assert default_field_name("unknown", "Home Score") == "Home Score"


def test_preview():
    config = TcpConfig().with_preset(CASPARCG)
    result = preview(config, (("HomeScore", "21"),))
    assert result.on_connect.startswith('CG 1-20 ADD 1 "scoresight_scorebug" 1 "')
    assert result.message.startswith("CG 1-20 UPDATE 1 ")
    assert result.more_fields == 0
    field = preview(TcpConfig().with_preset(MAGICSOFT), (("a", "1"), ("b", "2")))
    assert (field.on_connect, field.message, field.more_fields) == ("", "[a]=1\r\n", 1)
    assert visible_text("a\r\nb\0") == "a␍␊\nb␀"


def test_config_from_dict_repairs_bad_values():
    assert TcpConfig.from_dict(None) == TcpConfig()
    config = TcpConfig.from_dict(
        {
            "preset": "nope",
            "host": "  10.0.0.5 ",
            "port": "99999",
            "transport": "carrier pigeon",
            "mode": 3,
            "template": None,
            "line_ending": "x",
            "encoding": "klingon",
            "escape": "?",
            "min_interval": "abc",
            "refresh_interval": -5,
            "channel": "0",
            "unknown": 1,
        }
    )
    assert config == TcpConfig(host="10.0.0.5", channel=1)
    assert TcpConfig.from_dict({"port": "5250"}).port == 5250
    assert TcpConfig.from_dict({"min_interval": 99}).min_interval == 10.0
    # a preset keeps the host, sets the port (None: set it in the product)
    caspar = TcpConfig(host="10.0.0.5", port=1).with_preset(CASPARCG)
    assert (caspar.host, caspar.port, caspar.mode) == ("10.0.0.5", 5250, PER_FRAME)
    live = caspar.with_preset(LIVE_SCORE)
    assert (live.port, live.min_interval) == (None, 0.25)
    assert TcpConfig.from_dict(live.to_dict()) == live
    # a hand-edited file: unhashable values, a port as a number
    assert TcpConfig.from_dict({"preset": ["casparcg"], "mode": {}}) == TcpConfig()
    assert TcpConfig.from_dict({"port": 5250.0}).port == 5250
    assert TcpConfig.from_dict({"port": 52.5}).port is None
    assert TcpConfig.from_dict({"port": True}).port is None


def test_where_the_presets_come_from():
    # Live Score's and MagicSoft's formats are known only from another
    # vendor's public pages; the others from the products' documentation
    second_hand = {
        key for key, preset in tcp_output.PRESETS.items() if preset.second_hand
    }
    assert second_hand == {LIVE_SCORE, MAGICSOFT}


# --- what is sent when ---------------------------------------------------------------


def test_only_changes_unless_resending_unchanged_values(
    servers, make_output, make_result
):
    server = servers()
    output = make_output(config_for(CUSTOM, server.port))
    output.start()
    assert wait_until(lambda: connected(output))
    output.update([make_result("A", "1")])
    server.wait_for(b"A=1\n")
    output.update([make_result("A", "1", "SameNoChange")])
    output.update([make_result("A", "1")])  # Success again ("update on change" off)
    output.update(
        [make_result("A", "x", "FailedFilter"), make_result("B", "", "Empty")]
    )
    output.update([make_result("A", "2")])
    assert server.wait_for(b"A=2\n") == b"A=1\nA=2\n"
    # a box named in resend_unchanged (after a pause) gets its value again
    output.update([make_result("A", "2", "SameNoChange")], resend_unchanged={"A"})
    server.wait_for(b"A=2\nA=2\n")
    # "Also resend unchanged values"
    output.set_config(
        TcpConfig.from_dict({**output.config.to_dict(), "send_same": True})
    )
    output.update([make_result("A", "2", "SameNoChange")])
    server.wait_for(b"A=2\n" * 3)
    output.update([make_result("A", "2", "SameNoChange")])
    assert server.wait_for(b"A=2\n" * 4) == b"A=1\n" + b"A=2\n" * 4


def test_a_field_with_an_empty_name_is_left_out(servers, make_output, make_result):
    server = servers()
    output = make_output(config_for(CUSTOM, server.port), mapping={"A": "", "B": "Bee"})
    output.start()
    assert wait_until(lambda: connected(output))
    output.update([make_result("A", "1"), make_result("B", "2"), make_result("C", "3")])
    assert server.wait_for(b"C=3\n") == b"Bee=2\nC=3\n"
    # another name: every value again, under the new name
    output.set_field_mapping({"A": "Ay", "B": "Bee"})
    output.update([make_result("A", "1"), make_result("B", "2"), make_result("C", "3")])
    server.wait_for(b"Bee=2\nC=3\nAy=1\nBee=2\nC=3\n")


def test_the_minimum_interval_sends_the_latest_values_together(
    servers, make_output, make_result
):
    server = servers()
    output = make_output(config_for(CUSTOM, server.port, min_interval=0.3))
    output.start()
    assert wait_until(lambda: connected(output))
    for value in range(1, 11):
        output.update([make_result("A", str(value)), make_result("B", str(value))])
        time.sleep(0.05)
    server.wait_for(b"A=10\nB=10\n")
    chunks = server.connections[-1].chunks
    # fewer sends than updates, at least about 0.3 s apart, the latest values
    # always together
    assert 2 <= len(chunks) < 10, chunks
    times = [at for at, _ in chunks]
    assert all(b - a >= 0.25 for a, b in zip(times, times[1:])), times
    for _, data in chunks:
        lines = data.decode().splitlines()
        assert len(lines) == 2 and lines[0][2:] == lines[1][2:], data


def test_resend_all_every_few_seconds(servers, make_output, make_result):
    server = servers()
    output = make_output(config_for(CUSTOM, server.port, refresh_interval=0.2))
    output.start()
    assert wait_until(lambda: connected(output))
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        output.update([make_result("A", "1")])
        time.sleep(0.05)
    count = server.received().count(b"A=1\n")
    assert 3 <= count <= 7, server.received()


# --- the connection ------------------------------------------------------------------


def test_reconnects_after_the_receiver_drops_the_connection(
    servers, make_output, make_result
):
    server = servers()
    output = make_output(config_for(CUSTOM, server.port, init="HELLO"))
    output.start()
    server.wait_for(b"HELLO\n")
    output.update([make_result("A", "1"), make_result("B", "2")])
    server.wait_for(b"HELLO\nA=1\nB=2\n")
    server.drop_all()
    # a new connection: the "on connect" message and every value again
    server.wait_connections(2)
    assert server.wait_for(b"HELLO\nA=1\nB=2\n", index=1) == b"HELLO\nA=1\nB=2\n"
    output.update([make_result("A", "3"), make_result("B", "2")])
    server.wait_for(b"A=3\n", index=1)
    assert tcp_status(output).state == output_status.OK


def test_backoff_while_nothing_listens_then_connects(servers, make_output, make_result):
    port = free_port()  # nothing listens there
    output = make_output(config_for(CUSTOM, port))
    output.start()
    output.update([make_result("A", "1")])
    assert wait_until(lambda: output.status().retrying)
    status = tcp_status(output)
    assert status.state == output_status.RETRYING
    assert status.detail == f"Can't reach 127.0.0.1:{port} · retrying"
    assert f"port {port}" in status.hint
    # the wait between attempts grows
    first = output.status().retry_delay
    assert wait_until(lambda: (output.status().retry_delay or 0) > first)
    # the receiver starts: the value arrives
    server = servers(port=port)
    assert server.wait_for(b"A=1\n") == b"A=1\n"
    assert wait_until(lambda: tcp_status(output).state == output_status.OK)


def test_another_port_sends_to_the_new_receiver(servers, make_output, make_result):
    first, second = servers(), servers()
    output = make_output(config_for(CUSTOM, first.port))
    output.start()
    output.update([make_result("A", "1")])
    first.wait_for(b"A=1\n")
    output.set_config(
        TcpConfig.from_dict({**output.config.to_dict(), "port": second.port})
    )
    # the old connection is closed, the new receiver gets every value
    assert wait_until(lambda: first.connections[0].closed)
    assert second.wait_for(b"A=1\n") == b"A=1\n"


def test_another_casparcg_template_is_added_again(servers, make_output, make_result):
    server = servers()
    output = make_output(config_for(CASPARCG, server.port))
    output.start()
    server.wait_for(b'1 "<templateData></templateData>"\r\n')
    output.update([make_result("Home Score", "1")])
    server.wait_for(b'</templateData>"\r\n')
    output.set_config(
        TcpConfig.from_dict({**output.config.to_dict(), "template_name": "other"})
    )
    data = server.wait_for(b'</templateData>"\r\n', timeout=WAIT)
    assert wait_until(lambda: b'ADD 1 "other" 1' in server.received())
    data = server.received()
    assert data.count(b"ADD") == 2 and len(server.connections) == 1


def test_stop_closes_the_connection(servers, make_output, make_result):
    server = servers()
    output = make_output(config_for(CUSTOM, server.port))
    output.start()
    assert wait_until(lambda: connected(output))
    output.stop()
    assert wait_until(lambda: server.connections[0].closed)
    assert tcp_status(output).state == output_status.OFF
    output.update([make_result("A", "1")])  # stopped: nothing is sent
    time.sleep(0.2)
    assert server.received() == b""
    output.start()
    server.wait_connections(2)
    output.update([make_result("A", "1")])
    server.wait_for(b"A=1\n")


# --- replies and the status ----------------------------------------------------------


def test_amcp_replies_show_in_the_status(servers, make_output, make_result):
    answers = {"next": b"202 CG OK\r\n"}
    server = servers(reply=lambda line: answers["next"])
    output = make_output(config_for(CASPARCG, server.port))
    output.start()
    assert wait_until(lambda: output.connection_status().last_reply == "202 CG OK")
    status = tcp_status(output, product="CasparCG")
    assert status.state == output_status.OK
    assert status.detail == f"CasparCG: Connected to 127.0.0.1:{server.port}"
    assert status.hint == "Last answer: 202 CG OK"

    # an error answer (e.g. the template isn't there)
    answers["next"] = b"404 CG ERROR\r\n"
    output.update([make_result("Home Score", "1")])
    assert wait_until(lambda: output.connection_status().reply_error == "404 CG ERROR")
    status = tcp_status(output, product="CasparCG")
    assert status.state == output_status.WARNING
    assert status.detail == f"127.0.0.1:{server.port} answered “404 CG ERROR”"
    assert "templates folder" in status.hint
    assert status.technical == "404 CG ERROR"

    # data after a status line doesn't replace it; an OK clears the error
    answers["next"] = b"201 INFO OK\r\nsome data\r\n"
    output.update([make_result("Home Score", "2")])
    assert wait_until(lambda: output.connection_status().reply_error is None)
    assert output.connection_status().last_reply == "201 INFO OK"
    assert tcp_status(output, product="CasparCG").state == output_status.OK


def test_replies_without_codes_are_shown_as_they_are(servers, make_output, make_result):
    server = servers(reply=lambda line: b"*P\\UPDATE\\1\\*\\\\\r\n")
    output = make_output(config_for(CHYRON, server.port))
    output.start()
    assert wait_until(lambda: connected(output))
    output.update([make_result("Home Score", "1")])
    assert wait_until(
        lambda: output.connection_status().last_reply == "*P\\UPDATE\\1\\*\\\\"
    )
    assert output.connection_status().reply_error is None


def test_status_transitions(servers, make_output, make_result):
    output = make_output(TcpConfig(host="127.0.0.1"))  # no port yet
    assert tcp_status(output).state == output_status.OFF
    assert tcp_status(output).detail == "Off"
    output.start()
    status = tcp_status(output)
    assert (status.state, status.detail) == (
        output_status.WARNING,
        "Enter the host and port",
    )
    server = servers()
    output.set_config(TcpConfig(host="127.0.0.1", port=server.port))
    assert tcp_status(output, has_fields=False).detail == "No fields to send"
    assert wait_until(lambda: connected(output))
    status = tcp_status(output, product="")
    assert (status.state, status.detail) == (
        output_status.OK,
        f"Connected to 127.0.0.1:{server.port}",
    )
    assert status.last_activity_at is None  # nothing sent yet
    output.update([make_result("A", "1")])
    server.wait_for(b"A=1\n")
    assert wait_until(lambda: tcp_status(output).last_activity_at is not None)
    # the receiver goes away: reconnecting, then retrying
    server.close()
    assert wait_until(
        lambda: (
            tcp_status(output).state in (output_status.SENDING, output_status.RETRYING)
        )
    )
    assert wait_until(lambda: tcp_status(output).state == output_status.RETRYING)
    output.stop()
    assert tcp_status(output).state == output_status.OFF


class HangUpServer:
    """Accepts connections and closes them at once (like a server of another
    protocol), counting them."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.accepted: list[float] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            self.accepted.append(time.monotonic())
            client.close()

    def close(self):
        stop_listening(self.sock)


def test_a_receiver_that_hangs_up_at_once_is_not_flooded(make_output, make_result):
    server = HangUpServer()
    try:
        output = make_output(config_for(CUSTOM, server.port))
        output.start()
        output.update([make_result("A", "1")])
        time.sleep(2.5)
        # connections wait 0.5, 1, 2 s... after one closed right away
        assert 2 <= len(server.accepted) <= 5, server.accepted
        gaps = [b - a for a, b in zip(server.accepted, server.accepted[1:])]
        assert all(gap >= 0.4 for gap in gaps), gaps
        assert wait_until(lambda: output.connection_status().quick_closes >= 2)
        status = tcp_status(output)
        assert status.state == output_status.WARNING
        assert status.detail == (
            f"127.0.0.1:{server.port} closes the connection right away"
        )
        output.stop()
        count = len(server.accepted)
        time.sleep(0.6)
        assert len(server.accepted) == count  # stopped: no more attempts
        assert output.connection_status().quick_closes == 0
    finally:
        server.close()
    assert tcp_output.quick_close_delay(1) == 0.5
    assert tcp_output.quick_close_delay(3) == 2.0
    assert tcp_output.quick_close_delay(1000) == 5.0


def test_stopping_while_sending_keeps_nothing_for_the_next_start(
    servers, make_output, make_result
):
    server = servers()
    output = make_output(config_for(CUSTOM, server.port, min_interval=0.5))
    output.start()
    assert wait_until(lambda: connected(output))
    output.update([make_result("A", "1")])
    server.wait_for(b"A=1\n")
    # the next send waits for the minimum interval; stop meanwhile
    output.send_command("COMMAND")
    output.update([make_result("A", "2")])
    output.stop()
    output.start()
    server.wait_connections(2)
    output.update([make_result("B", "3")])
    # the new session sends its values: not the stopped session's command
    data = server.wait_for(b"B=3\n", index=1)
    assert b"COMMAND" not in server.all_received(), data
    assert not output.status().failed


def test_udp_status_before_anything_was_sent(make_output):
    server = UdpServer()
    try:
        output = make_output(config_for(CUSTOM, server.port, transport=UDP))
        output.start()
        status = tcp_status(output)
        assert status.state == output_status.SENDING
        assert (
            status.detail
            == f"Waiting for readings to send to 127.0.0.1:{server.port} (UDP)"
        )
    finally:
        server.close()


def test_per_frame_mode_sends_nothing_without_values(servers, make_output, make_result):
    server = servers()
    output = make_output(
        config_for(CUSTOM, server.port, mode=PER_FRAME, template="ALL {json}")
    )
    output.start()
    assert wait_until(lambda: connected(output))
    time.sleep(0.1)
    assert server.received() == b""
    output.update([make_result("A", "1")])
    server.wait_for(b'ALL {"A": "1"}\n')
    assert output.config.mode == PER_FRAME != PER_FIELD


# --- TCP server: programs connect to ScoreSight ----------------------------------------


class Client:
    """A program connected to ScoreSight's TCP server, recording what it gets."""

    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=WAIT)
        self.data = bytearray()
        self.closed = False
        self._lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            try:
                data = self.sock.recv(65536)
            except OSError:
                data = b""
            if not data:
                with self._lock:
                    self.closed = True
                return
            with self._lock:
                self.data += data

    def received(self) -> bytes:
        with self._lock:
            return bytes(self.data)

    def wait_for(self, expected: bytes) -> bytes:
        """Wait until it received expected (at the end of what it got)."""
        assert wait_until(lambda: self.received().endswith(expected)), (
            self.received(),
            expected,
        )
        return self.received()

    def close(self):
        _close(self.sock)


def _close(sock: socket.socket):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()


@pytest.fixture
def clients():
    created = []

    def _make(port: int) -> Client:
        client = Client(port)
        created.append(client)
        return client

    yield _make
    for client in created:
        client.close()


def server_config(port: int | None, **changes) -> TcpConfig:
    return TcpConfig.from_dict(
        {
            **TcpConfig().with_preset(CUSTOM).to_dict(),
            "transport": TCP_SERVER,
            "port": port,
            **changes,
        }
    )


def listening(output: TcpOutput) -> bool:
    return output.connection_status().listening


def client_count(output: TcpOutput) -> int:
    return len(output.connection_status().clients)


def test_server_config():
    config = server_config(5300, host="")
    assert config.listens and config.is_complete  # no host needed
    assert config.target == "port 5300"
    # the host doesn't matter to a TCP server
    assert config.endpoint == replace(config, host="10.0.0.9").endpoint
    assert TcpConfig.from_dict(config.to_dict()).transport == TCP_SERVER
    assert not server_config(None).is_complete


def test_a_program_that_connects_gets_on_connect_and_every_value_then_changes(
    make_output, make_result, clients
):
    port = free_port()
    output = make_output(server_config(port, init="HELLO"))
    output.start()
    assert wait_until(lambda: listening(output))
    output.update([make_result("Home Score", "21"), make_result("Time", "3:12")])
    client = clients(port)
    client.wait_for(b"HELLO\nHome Score=21\nTime=3:12\n")
    assert wait_until(lambda: client_count(output) == 1)
    # then only what changes
    output.update([make_result("Home Score", "22"), make_result("Time", "3:12")])
    assert client.wait_for(b"Home Score=22\n") == (
        b"HELLO\nHome Score=21\nTime=3:12\nHome Score=22\n"
    )


def test_every_program_gets_the_values(make_output, make_result, clients):
    port = free_port()
    output = make_output(server_config(port, init="HELLO"))
    output.start()
    assert wait_until(lambda: listening(output))
    first = clients(port)
    assert wait_until(lambda: client_count(output) == 1)
    first.wait_for(b"HELLO\n")
    output.update([make_result("A", "1")])
    first.wait_for(b"A=1\n")
    # a program connecting later gets the current values, and the others
    # don't get the "on connect" message again
    second = clients(port)
    second.wait_for(b"HELLO\nA=1\n")
    output.update([make_result("A", "2")])
    first.wait_for(b"A=2\n")
    second.wait_for(b"A=2\n")
    assert first.received() == b"HELLO\nA=1\nA=2\n"
    assert second.received() == b"HELLO\nA=1\nA=2\n"
    assert client_count(output) == 2


def test_one_message_with_every_field_for_each_program(
    make_output, make_result, clients
):
    port = free_port()
    output = make_output(
        server_config(port, mode=PER_FRAME, template="{json}", escape="none")
    )
    output.start()
    assert wait_until(lambda: listening(output))
    output.update([make_result("A", "1"), make_result("B", "2")])
    client = clients(port)
    client.wait_for(b'{"A": "1", "B": "2"}\n')
    output.update([make_result("A", "3"), make_result("B", "2")])
    client.wait_for(b'{"A": "3", "B": "2"}\n')


def test_a_program_that_disconnects_is_dropped(make_output, make_result, clients):
    port = free_port()
    output = make_output(server_config(port))
    output.start()
    assert wait_until(lambda: listening(output))
    client = clients(port)
    assert wait_until(lambda: client_count(output) == 1)
    client.close()
    assert wait_until(lambda: client_count(output) == 0)
    # sending to nobody is fine, and the next program gets the values
    output.update([make_result("A", "1")])
    clients(port).wait_for(b"A=1\n")


def test_what_programs_send_shows_as_the_last_answer(make_output, clients):
    port = free_port()
    output = make_output(server_config(port))
    output.start()
    assert wait_until(lambda: listening(output))
    client = clients(port)
    client.sock.sendall(b"READY\r\n")
    assert wait_until(lambda: output.connection_status().last_reply == "READY")


def test_stop_closes_the_port_and_the_connections(make_output, make_result, clients):
    port = free_port()
    output = make_output(server_config(port))
    output.start()
    assert wait_until(lambda: listening(output))
    client = clients(port)
    assert wait_until(lambda: client_count(output) == 1)
    output.stop()
    assert wait_until(lambda: client.closed)
    assert not listening(output)
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    # and it opens again
    output.start()
    assert wait_until(lambda: listening(output))
    output.update([make_result("A", "1")])
    clients(port).wait_for(b"A=1\n")


def test_another_port_moves_the_server(make_output, make_result, clients):
    port = free_port()
    output = make_output(server_config(port))
    output.start()
    assert wait_until(lambda: listening(output))
    client = clients(port)
    assert wait_until(lambda: client_count(output) == 1)
    other = free_port()
    output.set_config(server_config(other))
    assert wait_until(lambda: client.closed)
    assert wait_until(lambda: listening(output))
    output.update([make_result("A", "1")])
    clients(other).wait_for(b"A=1\n")


def test_a_busy_port_shows_and_is_retried(make_output, make_result, clients):
    port = free_port()
    blocker = tcp_output._open_server(server_config(port))
    try:
        output = make_output(server_config(port))
        output.start()
        assert wait_until(lambda: output.status().retrying)
        status = tcp_status(output)
        assert (status.state, status.detail) == (
            output_status.ERROR,
            f"Can't listen on port {port}",
        )
        assert "Another program uses this port" in status.hint
    finally:
        _close(blocker)
    # the port is free again: ScoreSight opens it by itself
    assert wait_until(lambda: listening(output))
    output.update([make_result("A", "1")])
    clients(port).wait_for(b"A=1\n")


MAC_SHARES_IPV4_PORTS = pytest.mark.xfail(
    sys.platform == "darwin",
    reason="macOS lets a dual-stack socket share a port held over IPv4 (#119)",
    strict=True,
)


@pytest.mark.parametrize(
    "address",
    [
        pytest.param("0.0.0.0", marks=MAC_SHARES_IPV4_PORTS),
        pytest.param("127.0.0.1", marks=MAC_SHARES_IPV4_PORTS),
        "::",
    ],
)
def test_a_port_another_program_listens_on_with_one_protocol_is_busy(address):
    # Windows lets a dual-stack socket share such a port unless it is taken
    # exclusively, and the other program then gets the IPv4 (or IPv6) clients
    if ":" in address and not socket.has_dualstack_ipv6():
        pytest.skip("no IPv6")
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    port = free_port()
    blocker = socket.create_server((address, port), family=family)
    try:
        with pytest.raises(tcp_output.ListenError):
            tcp_output._open_server(server_config(port))
    finally:
        blocker.close()
    _close(tcp_output._open_server(server_config(port)))


def test_server_status_transitions(make_output, make_result, clients):
    output = make_output(server_config(None))
    status = tcp_status(output)
    assert (status.state, status.detail) == (output_status.OFF, "Off")
    output.start()
    assert tcp_status(output).detail == "Enter a port"
    port = free_port()
    output.set_config(server_config(port))
    assert tcp_status(output, has_fields=False).detail == "No fields to send"
    assert wait_until(lambda: listening(output))
    status = tcp_status(output, product="")
    assert (status.state, status.detail) == (
        output_status.SENDING,
        f"Listening on port {port} · nothing connected",
    )
    client = clients(port)
    assert wait_until(lambda: client_count(output) == 1)
    status = tcp_status(output, product="")
    assert (status.state, status.detail) == (
        output_status.OK,
        f"Listening on port {port} · 1 program connected",
    )
    assert status.hint.startswith("Connected: 127.0.0.1:")
    clients(port)
    assert wait_until(lambda: client_count(output) == 2)
    assert tcp_status(output, product="Test").detail == (
        f"Test: Listening on port {port} · 2 programs connected"
    )
    output.update([make_result("A", "1")])
    client.wait_for(b"A=1\n")
    assert wait_until(lambda: tcp_status(output).last_activity_at is not None)
    output.stop()
    assert tcp_status(output).state == output_status.OFF


def test_too_many_programs_are_refused(make_output, clients, monkeypatch):
    monkeypatch.setattr(tcp_output, "MAX_CLIENTS", 2)
    port = free_port()
    output = make_output(server_config(port))
    output.start()
    assert wait_until(lambda: listening(output))
    clients(port)
    clients(port)
    assert wait_until(lambda: client_count(output) == 2)
    third = clients(port)
    assert wait_until(lambda: third.closed)
    assert client_count(output) == 2


# --- WebSocket: ScoreSight connects to a ws:// or wss:// URL ---------------------------


@pytest.fixture
def ws_servers():
    created = []

    def _make(**kwargs) -> WebSocketServer:
        server = WebSocketServer(**kwargs)
        created.append(server)
        return server

    yield _make
    for server in created:
        server.close()


def websocket_config(url: str, preset: str = WEBSOCKET_JSON, **changes) -> TcpConfig:
    return TcpConfig.from_dict(
        {
            **TcpConfig().with_preset(preset).to_dict(),
            "transport": WEBSOCKET,
            "url": url,
            **changes,
        }
    )


def test_websocket_config():
    config = websocket_config("wss://user:pw@scores.example.com/live?token=s3cret")
    assert config.websocket and config.is_complete
    # the status and the log never show the login, the path or the query
    assert config.target == "scores.example.com:443"
    assert websocket_target("ws://127.0.0.1:8080/x") == "127.0.0.1:8080"
    assert websocket_target("ws://[::1]/x") == "[::1]:80"
    for url in ("", "http://example.com", "wss://", "ws://host:port/", "example.com"):
        assert websocket_target(url) is None, url
        assert not websocket_config(url).is_complete
    # new headers need a new handshake: another endpoint
    assert config.endpoint != replace(config, headers="X-Key: 1").endpoint
    stored = TcpConfig.from_dict({**config.to_dict(), "url": "  ws://a/b  "})
    assert (stored.url, stored.headers) == ("ws://a/b", "")
    assert TcpConfig.from_dict({"url": 5, "headers": None}) == TcpConfig()
    # the preset: one JSON object with every field per message
    preset = TcpConfig(url="ws://a/b").with_preset(WEBSOCKET_JSON)
    assert (preset.transport, preset.mode, preset.template) == (
        WEBSOCKET,
        PER_FRAME,
        "{json}",
    )
    assert (preset.line_ending, preset.escape, preset.url) == (
        "none",
        "none",
        "ws://a/b",
    )
    # another preset goes back to TCP (and keeps the URL)
    assert preset.with_preset(CASPARCG).transport == "tcp"


def test_websocket_sends_a_json_message_per_change_with_the_headers(
    ws_servers, make_output, make_result
):
    server = ws_servers()
    output = make_output(
        websocket_config(
            server.url("/scores?token=abc"),
            headers="Authorization: Bearer s3cret\nnot a header",
        )
    )
    output.start()
    assert wait_until(lambda: connected(output))
    connection = server.connections[0]
    assert connection.path == "/scores?token=abc"
    assert connection.headers["authorization"] == "Bearer s3cret"
    output.update([make_result("Home Score", "21"), make_result("Time", "4:27")])
    server.wait_for(['{"Home Score": "21", "Time": "4:27"}'])
    output.update([make_result("Home Score", "21"), make_result("Time", "4:26")])
    server.wait_for(
        ['{"Home Score": "21", "Time": "4:27"}', '{"Home Score": "21", "Time": "4:26"}']
    )
    status = tcp_status(output, product="WebSocket (JSON)")
    assert (status.state, status.detail) == (
        output_status.OK,
        f"WebSocket (JSON): Connected to 127.0.0.1:{server.port}",
    )
    assert "token" not in status.hint


def test_websocket_a_message_per_field(ws_servers, make_output, make_result):
    server = ws_servers()
    output = make_output(
        websocket_config(
            server.url(),
            preset=CUSTOM,
            template='{"name": "{field}", "value": "{value}"}',
            escape="json",
            line_ending="none",
            init='{"hello": {json}}',
        )
    )
    output.start()
    server.wait_for(['{"hello": {}}'])
    output.update([make_result("A", 'say "hi"'), make_result("B", "2")])
    server.wait_for(
        [
            '{"hello": {}}',
            '{"name": "A", "value": "say \\"hi\\""}',
            '{"name": "B", "value": "2"}',
        ]
    )


def test_websocket_messages_from_the_server_show_as_the_last_answer(
    ws_servers, make_output
):
    server = ws_servers()
    output = make_output(websocket_config(server.url()))
    output.start()
    server.wait_connections(1)
    server.connections[0].send_text('{"ok": true}')
    assert wait_until(lambda: output.connection_status().last_reply == '{"ok": true}')
    assert tcp_status(output).hint == 'Last answer: {"ok": true}'


def test_websocket_reconnects_when_the_server_closes_it(
    ws_servers, make_output, make_result
):
    server = ws_servers()
    output = make_output(websocket_config(server.url(), init="HELLO"))
    output.start()
    output.update([make_result("A", "1")])
    server.wait_for(["HELLO", '{"A": "1"}'])
    server.connections[0].close()
    # a new connection: the "on connect" message and every value again
    server.wait_connections(2)
    server.wait_for(["HELLO", '{"A": "1"}'], index=1)
    output.update([make_result("A", "2")])
    server.wait_for(["HELLO", '{"A": "1"}', '{"A": "2"}'], index=1)


def test_websocket_stop_says_goodbye(ws_servers, make_output, make_result):
    server = ws_servers()
    output = make_output(websocket_config(server.url()))
    output.start()
    assert wait_until(lambda: connected(output))
    output.stop()
    connection = server.connections[0]
    assert wait_until(lambda: connection.closed)
    assert connection.got_close  # a close frame, not just a dropped connection
    assert tcp_status(output).state == output_status.OFF


def test_websocket_refused_handshake_shows_and_is_retried(
    ws_servers, make_output, make_result
):
    server = ws_servers(refuse=401)
    output = make_output(
        websocket_config(server.url("/?token=wrong"), headers="X-Key: wrong")
    )
    output.start()
    output.update([make_result("A", "1")])
    assert wait_until(lambda: output.status().retrying)
    status = tcp_status(output)
    assert (status.state, status.detail) == (
        output_status.ERROR,
        "HTTP 401: access denied",
    )
    assert "Headers" in status.hint
    assert "HTTP 401: no entry" in status.technical
    # the token is fixed: it connects, and the value arrives
    server.refuse = None
    server.wait_for(['{"A": "1"}'])
    assert wait_until(lambda: tcp_status(output).state == output_status.OK)


def test_websocket_new_headers_reconnect(ws_servers, make_output, make_result):
    server = ws_servers()
    output = make_output(websocket_config(server.url(), headers="X-Key: 1"))
    output.start()
    output.update([make_result("A", "1")])
    server.wait_for(['{"A": "1"}'])
    output.set_config(replace(output.config, headers="X-Key: 2"))
    server.wait_connections(2)
    assert server.connections[1].headers["x-key"] == "2"
    server.wait_for(['{"A": "1"}'], index=1)
    assert wait_until(lambda: server.connections[0].closed)


def test_websocket_status_without_a_url_and_when_nothing_answers(
    make_output, make_result
):
    output = make_output(websocket_config(""))
    assert "over WebSocket" in tcp_status(output).hint  # off
    output.start()
    status = tcp_status(output)
    assert (status.state, status.detail) == (
        output_status.WARNING,
        "Enter a ws:// or wss:// URL",
    )
    port = free_port()  # nothing listens there
    output.set_config(replace(output.config, url=f"ws://127.0.0.1:{port}/live"))
    output.update([make_result("A", "1")])
    assert wait_until(lambda: output.status().retrying)
    assert wait_until(
        lambda: tcp_status(output).detail == f"Can't reach 127.0.0.1:{port} · retrying"
    )

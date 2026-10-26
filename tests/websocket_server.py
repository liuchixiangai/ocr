"""A minimal WebSocket server on 127.0.0.1 for the tests (RFC 6455, standard
library only): it records each connection's handshake and the text messages it
gets, answers pings and close frames, and can refuse the handshake with an
HTTP status, send messages and close a connection."""

import base64
import hashlib
import socket
import struct
import threading
import time

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TEXT, BINARY, CLOSE, PING, PONG = 0x1, 0x2, 0x8, 0x9, 0xA


def _wait_until(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def frame(opcode: int, payload: bytes) -> bytes:
    """A frame from the server (not masked)."""
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, length)
    elif length < 1 << 16:
        header = struct.pack("!BBH", 0x80 | opcode, 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
    return header + payload


class Connection:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.request_line = ""
        self.headers: dict[str, str] = {}  # names in lower case
        self.messages: list[str] = []
        self.got_close = False  # the client sent a close frame
        self.closed = False
        self._send_lock = threading.Lock()

    @property
    def path(self) -> str:
        parts = self.request_line.split(" ")
        return parts[1] if len(parts) > 1 else ""

    def send(self, opcode: int, payload: bytes):
        with self._send_lock:
            try:
                self.sock.sendall(frame(opcode, payload))
            except OSError:
                pass

    def send_text(self, text: str):
        self.send(TEXT, text.encode("utf-8"))

    def close(self, code: int = 1000):
        """Close it the WebSocket way (a close frame, then the socket)."""
        self.send(CLOSE, struct.pack("!H", code))
        self.drop()

    def drop(self):
        """Close the socket without a close frame."""
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class WebSocketServer:
    """refuse: an HTTP status (e.g. 401) to answer handshakes with instead of
    accepting them; it can be changed while the server runs."""

    def __init__(self, refuse: int | None = None):
        self.refuse = refuse
        self.connections: list[Connection] = []
        self.handshakes = 0  # requests, accepted or not
        self._lock = threading.Lock()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def url(self, path: str = "/") -> str:
        return f"ws://127.0.0.1:{self.port}{path}"

    def _accept(self):
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket):
        connection = Connection(client)
        reader = client.makefile("rb")
        try:
            if not self._handshake(connection, reader):
                return
            with self._lock:
                self.connections.append(connection)
            self._read_frames(connection, reader)
        except (OSError, ValueError, struct.error):
            pass
        finally:
            connection.closed = True
            try:
                client.close()
            except OSError:
                pass

    def _handshake(self, connection: Connection, reader) -> bool:
        connection.request_line = reader.readline().decode("latin-1").strip()
        while True:
            line = reader.readline().decode("latin-1").strip()
            if not line:
                break
            name, _, value = line.partition(":")
            connection.headers[name.strip().lower()] = value.strip()
        with self._lock:
            self.handshakes += 1
        if self.refuse is not None:
            body = b"no entry"
            connection.sock.sendall(
                f"HTTP/1.1 {self.refuse} Refused\r\nContent-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n".encode()
                + body
            )
            return False
        key = connection.headers.get("sec-websocket-key", "")
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        connection.sock.sendall(
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode()
        )
        return True

    def _read_frames(self, connection: Connection, reader):
        while True:
            header = reader.read(2)
            if len(header) < 2:
                return
            opcode = header[0] & 0x0F
            length = header[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", reader.read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", reader.read(8))[0]
            mask = reader.read(4) if header[1] & 0x80 else b"\0\0\0\0"
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(reader.read(length)))
            if opcode == TEXT:
                with self._lock:
                    connection.messages.append(data.decode("utf-8"))
            elif opcode == PING:
                connection.send(PONG, data)
            elif opcode == CLOSE:
                connection.got_close = True
                connection.send(CLOSE, data[:2])
                return

    # --- for the tests -----------------------------------------------------------

    def messages(self, index: int = -1) -> list[str]:
        with self._lock:
            if not self.connections:
                return []
            return list(self.connections[index].messages)

    def wait_for(self, expected: list[str], index: int = -1, timeout=10.0):
        """Wait until connection index got exactly these messages."""
        assert _wait_until(lambda: self.messages(index) == expected, timeout), (
            self.messages(index),
            expected,
        )

    def wait_connections(self, count: int, timeout=10.0):
        assert _wait_until(lambda: len(self.connections) >= count, timeout), len(
            self.connections
        )

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        with self._lock:
            connections = list(self.connections)
        for connection in connections:
            connection.drop()

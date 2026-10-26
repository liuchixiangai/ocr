"""TCP / UDP output: sends the readings as text messages over a socket, to
graphics systems with a simple line protocol (CasparCG's AMCP, Chyron PRIME's
Intelligent Interface, Live Score, MagicSoft CG, Vizrt Viz Engine's Shared
Memory input) or to any program that reads lines from a TCP connection or UDP
datagrams. As a TCP server, ScoreSight listens on a port instead, and every
program that connects gets the messages. Over a WebSocket (WEBSOCKET),
ScoreSight connects to a ws:// or wss:// URL (with the extra headers of the
settings, e.g. an Authorization token, in the handshake) and sends each message
as a text message.

What is sent comes from a template: one message per changed field
("[{field}]={value}") or one message with every field ('{"values":{json}}').
Placeholders are replaced by escaped text (see render_text); everything else in
the template is sent as it is. Presets (PRESETS) fill in the template and the
options for each product, and everything stays editable.

update() is called on the GUI thread: it works out which values changed and
wakes a background OutputWorker, so a slow or unreachable receiver never blocks
the UI. The worker thread keeps one TCP connection open (connecting, and
reconnecting with backoff when it fails or the receiver closes it), sends the
optional "on connect" message and every current value after each connection,
and respects the minimum interval between sends. A reader thread reads the
receiver's replies (CasparCG answers every command, e.g. "202 CG OK"), so the
receiver never blocks on a full buffer, and an error reply shows in the status.

As a TCP server (TCP_SERVER), the worker thread opens the listening socket
(retrying with backoff while the port can't be used), an accept thread takes
the connections (at most MAX_CLIENTS), and each send goes to every connected
program: a program that just connected gets the "on connect" message and every
current value first. A reader thread per program reads what it sends; a program
that closes its connection, or doesn't read for SEND_TIMEOUT, is dropped.

A WebSocket is kept open like a TCP connection: its reader thread reads the
server's messages (each one is an answer) and answers its pings, and a close
from the server reconnects. A handshake the server refuses (HTTP 401...) raises
WebSocketRefused, with the status code for the status line.
"""

import json
import re
import socket
import threading
import time
from collections.abc import Container, Iterable, Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from urllib.parse import urlsplit

import websocket

from api_output import parse_headers
from output_worker import OutputWorker, SendSkipped, ThrottledErrorLog
from sc_logging import logger
from text_detection_target import TextDetectionTargetWithResult

# transports
TCP = "tcp"  # ScoreSight connects to the receiver
UDP = "udp"
TCP_SERVER = "tcp-server"  # ScoreSight listens; programs connect to it
WEBSOCKET = "websocket"  # ScoreSight connects to a ws:// or wss:// URL
TRANSPORTS = (TCP, UDP, TCP_SERVER, WEBSOCKET)
WEBSOCKET_SCHEMES = {"ws": 80, "wss": 443}  # and their default ports

# what a message holds
PER_FIELD = "field"  # one message per changed field
PER_FRAME = "frame"  # one message with every field
MODES = (PER_FIELD, PER_FRAME)

# what ends every line of a message (each line of the template is a line)
LINE_ENDINGS = {"crlf": "\r\n", "lf": "\n", "cr": "\r", "nul": "\0", "none": ""}

# Python codec names; characters a codec can't encode are sent as "?"
ENCODINGS = ("utf-8", "iso-8859-1", "cp1252", "utf-16-le", "ascii")

# how the text of a placeholder is escaped before it goes into the message
ESCAPE_NONE = "none"
ESCAPE_LINE = "line"  # line breaks (and NUL) become spaces
ESCAPE_JSON = "json"  # the inside of a JSON string
ESCAPE_XML = "xml"  # XML text or attribute value
ESCAPE_AMCP = "amcp"  # the inside of a quoted AMCP parameter (CasparCG)
ESCAPE_CHYRON = "chyron"  # a field of an Intelligent Interface command
ESCAPE_NUL = "nul"  # NUL characters are left out (NUL-framed protocols)
ESCAPES = (
    ESCAPE_NONE,
    ESCAPE_LINE,
    ESCAPE_JSON,
    ESCAPE_XML,
    ESCAPE_AMCP,
    ESCAPE_CHYRON,
    ESCAPE_NUL,
)

# the placeholders a template can use
PLACEHOLDERS = (
    "field",  # the field's name (per field)
    "value",  # the field's value (per field)
    "json",  # every field as a JSON object
    "xml",  # every field as CasparCG templateData XML
    "channel",  # CasparCG video channel
    "layer",  # CasparCG layer
    "cg_layer",  # CasparCG CG layer
    "template",  # CasparCG template name
)
CASPARCG_PLACEHOLDERS = ("channel", "layer", "cg_layer", "template")

# seconds to open a TCP connection: more than the 2 s Windows takes to report
# a refused connection (it retries first), so a refusal isn't taken for a
# timeout
CONNECT_TIMEOUT = 3.0
SEND_TIMEOUT = 2.0  # seconds a send may block (the receiver doesn't read)
MAX_MIN_INTERVAL = 10.0
MAX_REFRESH_INTERVAL = 3600.0
# replies longer than this without a line break are dropped
MAX_REPLY_LENGTH = 65536
# A connection the receiver closes within this many seconds was closed right
# away: the next connection waits, longer each time (up to the worker's 5 s),
# so that a receiver that hangs up at once (another protocol's port, for
# example) isn't flooded with reconnections
QUICK_CLOSE = 2.0
QUICK_CLOSE_BACKOFF = (0.5, 5.0)  # the first and the longest wait, in seconds

# TCP server: the most programs connected at once (more are refused), and how
# often (seconds) the accept thread checks whether to stop
MAX_CLIENTS = 32
ACCEPT_POLL = 0.5

LOG_LABEL = "TCP output"

_GOOD_STATES = (
    TextDetectionTargetWithResult.ResultState.Success,
    TextDetectionTargetWithResult.ResultState.SameNoChange,
)

# --- escaping ----------------------------------------------------------------

_LINE_BREAKS = re.compile(r"[\r\n\0]+")
_NEW_LINES = re.compile(r"\r\n|\r|\n")
# characters XML 1.0 can't represent
_NOT_XML_CHAR = re.compile("[^\t\n\r\x20-퟿-�\U00010000-\U0010ffff]")


def escape_line(text: str) -> str:
    """Line breaks and NUL become a space, so a value can't end a line early."""
    return _LINE_BREAKS.sub(" ", text)


def escape_json(text: str) -> str:
    """The inside of a JSON string (without the quotes)."""
    return json.dumps(text, ensure_ascii=False)[1:-1]


def escape_xml(text: str) -> str:
    """XML text or attribute value (in either kind of quotes)."""
    text = _NOT_XML_CHAR.sub("", text)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
        .replace("\t", "&#9;")
        .replace("\n", "&#10;")
        .replace("\r", "&#13;")
    )


def escape_amcp(text: str) -> str:
    """The inside of a quoted AMCP parameter. AMCP's escape sequences are \\"
    (quote), \\\\ (backslash) and \\n (new line); NUL is left out."""
    text = text.replace("\0", "").replace("\\", "\\\\").replace('"', '\\"')
    return _NEW_LINES.sub(r"\\n", text)


def escape_chyron(text: str) -> str:
    """A field of an Intelligent Interface command. Backslashes separate the
    fields, and Chyron documents no way to send one inside a field, so they
    become "/" (as PRIME does with file paths); line breaks become spaces."""
    return escape_line(text).replace("\\", "/")


def escape_nul(text: str) -> str:
    return text.replace("\0", "")


ESCAPE_FUNCTIONS = {
    ESCAPE_NONE: lambda text: text,
    ESCAPE_LINE: escape_line,
    ESCAPE_JSON: escape_json,
    ESCAPE_XML: escape_xml,
    ESCAPE_AMCP: escape_amcp,
    ESCAPE_CHYRON: escape_chyron,
    ESCAPE_NUL: escape_nul,
}

# --- field names -----------------------------------------------------------------

NAMES_AS_IS = "box"  # the field's name in ScoreSight
NAMES_IDENTIFIER = "identifier"  # "Home Score" -> "HomeScore"
NAMES_SNAKE = "snake"  # "Home Score" -> "home_score"


def identifier_name(name: str) -> str:
    """An ASCII identifier: each word starting with a capital, the spaces and
    other characters left out, "_" in front if it doesn't start with a letter
    ("Home Score" -> "HomeScore", "ShotClock" stays, "1st Half" ->
    "_1stHalf")."""
    words = re.findall(r"[0-9A-Za-z_]+", name)
    result = "".join(word[:1].upper() + word[1:] for word in words)
    if not result or not (result[0].isalpha() or result[0] == "_"):
        result = "_" + result
    return result


def snake_name(name: str) -> str:
    """Lower case words joined by "_" ("Home Score" -> "home_score")."""
    words = re.findall(r"[0-9A-Za-z]+", name.lower())
    return "_".join(words) or "field"


# --- presets -----------------------------------------------------------------------

CUSTOM = "custom"
LIVE_SCORE = "livescore"
CASPARCG = "casparcg"
MAGICSOFT = "magicsoft"
CHYRON = "chyron"
VIZRT = "vizrt"
WEBSOCKET_JSON = "websocket-json"


@dataclass(frozen=True)
class Preset:
    key: str
    name: str  # the product's name (not translated); "Custom" is translated
    # the TcpConfig fields the preset sets (the host is kept)
    settings: Mapping[str, object]
    name_style: str = NAMES_AS_IS
    # names for some of the standard fields (others follow name_style)
    names: Mapping[str, str] = field(default_factory=dict)
    # the protocol is known only from another vendor's public pages
    second_hand: bool = False


PRESETS: dict[str, Preset] = {
    preset.key: preset
    for preset in (
        Preset(
            CUSTOM,
            "Custom",
            {
                "port": None,
                "transport": TCP,
                "mode": PER_FIELD,
                "template": "{field}={value}",
                "init": "",
                "line_ending": "lf",
                "encoding": "utf-8",
                "escape": ESCAPE_LINE,
                "min_interval": 0.0,
                "refresh_interval": 0.0,
            },
        ),
        # Live Score's "Scoreboard OCR Input" (Input > Scoreboard OCR > OCR
        # Setup): Live Score listens on the port set there. The format comes
        # from another OCR application's public guide: one JSON object per
        # message, ended by LF; the field names are those of Live Score's OCR
        # Setup (GameClock, ShotClock, ScoreAway... ScoreHome is inferred). It
        # "does not support displaying data 10x a second".
        Preset(
            LIVE_SCORE,
            "Live Score",
            {
                "port": None,
                "transport": TCP,
                "mode": PER_FRAME,
                "template": '{"type":"ocr", "values":{json}}',
                "init": "",
                "line_ending": "lf",
                "encoding": "utf-8",
                "escape": ESCAPE_NONE,
                "min_interval": 0.25,
                "refresh_interval": 0.0,
            },
            names={
                "Time": "GameClock",
                "Shot Clock": "ShotClock",
                "Away Score": "ScoreAway",
                "Home Score": "ScoreHome",
            },
            second_hand=True,
        ),
        # CasparCG's AMCP (casparcg.com/docs/wiki/protocols/amcp-protocol): CR LF
        # after every command, UTF-8, quoted parameters with \" \\ \n escapes.
        # CG ADD loads the template (and plays it: play-on-load 1), CG UPDATE
        # sends the data, as templateData XML (any server) or JSON (2.1+).
        Preset(
            CASPARCG,
            "CasparCG",
            {
                "port": 5250,
                "transport": TCP,
                "mode": PER_FRAME,
                "template": 'CG {channel}-{layer} UPDATE {cg_layer} "{xml}"',
                "init": 'CG {channel}-{layer} ADD {cg_layer} "{template}" 1 "{xml}"',
                "line_ending": "crlf",
                "encoding": "utf-8",
                "escape": ESCAPE_AMCP,
                "min_interval": 0.0,
                "refresh_interval": 0.0,
                "channel": 1,
                "layer": 20,
                "cg_layer": 1,
                "template_name": "scoresight_scorebug",
            },
            name_style=NAMES_IDENTIFIER,
        ),
        # MagicSoft CG's socket server (Tools > Options > Socket Communication),
        # per another OCR application's public guide: "[clock]=2:45" and CR LF,
        # example address tcp://localhost:2034 (MagicSoft's own sample macros
        # use the same "[name]=value" lines; its default configuration has port
        # pairs from 2610 to 2643). "[time]" is reserved, so Time is sent as
        # "clock" (the guide's example).
        Preset(
            MAGICSOFT,
            "MagicSoft CG",
            {
                "port": 2034,
                "transport": TCP,
                "mode": PER_FIELD,
                "template": "[{field}]={value}",
                "init": "",
                "line_ending": "crlf",
                "encoding": "utf-8",
                "escape": ESCAPE_LINE,
                "min_interval": 0.0,
                "refresh_interval": 0.0,
            },
            name_style=NAMES_SNAKE,
            names={"Time": "clock"},
            second_hand=True,
        ),
        # Chyron PRIME's Intelligent Interface ("PRIME Automation: Intelligent
        # Interface P Commands" 4.10 and the PRIME 5.1 Automation User Guide):
        # P\UPDATE[:<Buffer>]\<Channel>[:<Layer>]\<SceneName>\<NameValuePairs>\\
        # then CR LF; TCP and ISO-8859-1 by default; the port is set in PRIME.
        # Scene "*" updates every scene on the channel (Preview and Program).
        Preset(
            CHYRON,
            "Chyron PRIME",
            {
                "port": None,
                "transport": TCP,
                "mode": PER_FIELD,
                "template": "P\\UPDATE\\1\\*\\{field}\\{value}\\\\",
                "init": "",
                "line_ending": "crlf",
                "encoding": "iso-8859-1",
                "escape": ESCAPE_CHYRON,
                "min_interval": 0.0,
                "refresh_interval": 0.0,
            },
        ),
        # Viz Engine's Shared Memory input (Viz Engine Guide, External Data
        # Input): "key|value\0", several pairs per packet allowed, over TCP (a
        # connection kept open is recommended) or UDP; the port is set in Viz
        # Configuration (no default).
        Preset(
            VIZRT,
            "Vizrt (Shared Memory)",
            {
                "port": None,
                "transport": TCP,
                "mode": PER_FIELD,
                "template": "{field}|{value}",
                "init": "",
                "line_ending": "nul",
                "encoding": "utf-8",
                "escape": ESCAPE_NUL,
                "min_interval": 0.0,
                "refresh_interval": 0.0,
            },
            name_style=NAMES_IDENTIFIER,
        ),
        # a web app or server of one's own: a JSON object with every field per
        # WebSocket message ({"Home Score": "21", "Time": "4:27"})
        Preset(
            WEBSOCKET_JSON,
            "WebSocket (JSON)",
            {
                "port": None,
                "transport": WEBSOCKET,
                "mode": PER_FRAME,
                "template": "{json}",
                "init": "",
                "line_ending": "none",
                "encoding": "utf-8",
                "escape": ESCAPE_NONE,
                "min_interval": 0.0,
                "refresh_interval": 0.0,
            },
        ),
    )
}


def default_field_name(preset_key: str, box_name: str) -> str:
    """The name a field is sent as unless the user set another one."""
    preset = PRESETS.get(preset_key, PRESETS[CUSTOM])
    if box_name in preset.names:
        return preset.names[box_name]
    if preset.name_style == NAMES_IDENTIFIER:
        return identifier_name(box_name)
    if preset.name_style == NAMES_SNAKE:
        return snake_name(box_name)
    return box_name


# --- settings ------------------------------------------------------------------


@dataclass(frozen=True)
class TcpConfig:
    """The output's settings (stored as the "tcp_output" document)."""

    preset: str = CUSTOM
    host: str = "localhost"
    port: int | None = None
    transport: str = TCP
    mode: str = PER_FIELD
    template: str = "{field}={value}"
    init: str = ""  # sent after each connection ("on connect")
    line_ending: str = "lf"
    encoding: str = "utf-8"
    escape: str = ESCAPE_LINE
    send_same: bool = False  # also resend unchanged values
    min_interval: float = 0.0  # seconds between two sends at least
    refresh_interval: float = 0.0  # resend every value this often (0: never)
    channel: int = 1
    layer: int = 20
    cg_layer: int = 1
    template_name: str = "scoresight_scorebug"
    url: str = ""  # WebSocket: ws://host:port/path or wss://...
    headers: str = ""  # WebSocket: extra handshake headers, "Name: value" lines

    @property
    def listens(self) -> bool:
        """ScoreSight is the TCP server: programs connect to it."""
        return self.transport == TCP_SERVER

    @property
    def websocket(self) -> bool:
        return self.transport == WEBSOCKET

    @property
    def target(self) -> str:
        """ "host:port" ("[::1]:5250" for an IPv6 address); "port 5300" for a
        TCP server; the URL's "host:port" for a WebSocket (never its login,
        path or query, which may hold a token)."""
        if self.websocket:
            return websocket_target(self.url) or "?"
        port = self.port if self.port is not None else "?"
        if self.listens:
            return f"port {port}"
        host = self.host or "?"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{host}:{port}"

    @property
    def endpoint(self) -> tuple:
        if self.websocket:
            # the headers go with the handshake: new ones need a new connection
            return (self.transport, self.url, self.headers)
        # a TCP server listens on every address: the host doesn't matter
        return (self.transport, "" if self.listens else self.host, self.port)

    @property
    def is_complete(self) -> bool:
        """A host and a port are set (a TCP server needs only the port, a
        WebSocket a ws:// or wss:// URL)."""
        if self.websocket:
            return websocket_target(self.url) is not None
        return (self.listens or bool(self.host)) and self.port is not None

    def uses_casparcg_parameters(self) -> bool:
        text = self.template + "\n" + self.init
        return any(f"{{{name}}}" in text for name in CASPARCG_PLACEHOLDERS)

    def with_preset(self, key: str) -> "TcpConfig":
        """These settings with a preset's template and options (the host is
        kept, the port is the preset's)."""
        preset = PRESETS.get(key, PRESETS[CUSTOM])
        return replace(self, preset=preset.key, **preset.settings)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping | None) -> "TcpConfig":
        """Settings from a stored document; a missing or invalid value gets its
        default."""
        if not isinstance(data, Mapping):
            return cls()
        defaults = cls()
        values = {}
        for item in fields(cls):
            if item.name in data:
                values[item.name] = data[item.name]
        config = replace(defaults, **values)

        def choice(value, allowed, default):
            try:
                return value if value in allowed else default
            except TypeError:  # e.g. a list in a hand-edited file
                return default

        return replace(
            config,
            preset=choice(config.preset, PRESETS, CUSTOM),
            host=str(config.host or "").strip(),
            port=parse_port(config.port),
            transport=choice(config.transport, TRANSPORTS, TCP),
            mode=choice(config.mode, MODES, PER_FIELD),
            template=_text(config.template, defaults.template),
            init=_text(config.init, ""),
            line_ending=choice(config.line_ending, LINE_ENDINGS, "lf"),
            encoding=choice(config.encoding, ENCODINGS, "utf-8"),
            escape=choice(config.escape, ESCAPES, ESCAPE_LINE),
            send_same=bool(config.send_same),
            min_interval=_number(config.min_interval, 0.0, MAX_MIN_INTERVAL),
            refresh_interval=_number(
                config.refresh_interval, 0.0, MAX_REFRESH_INTERVAL
            ),
            channel=int(_number(config.channel, 1, 9999, default=1)),
            layer=int(_number(config.layer, -9999, 9999, default=20)),
            cg_layer=int(_number(config.cg_layer, 0, 9999, default=1)),
            template_name=_text(config.template_name, defaults.template_name),
            url=_text(config.url, "").strip(),
            headers=_text(config.headers, ""),
        )


def _init_settings(config: TcpConfig) -> tuple:
    """What the "on connect" message is made of."""
    return (
        config.init,
        config.channel,
        config.layer,
        config.cg_layer,
        config.template_name,
    )


def _format_settings(config: TcpConfig) -> tuple:
    """What the messages look like (and which names the fields have)."""
    return (
        config.preset,
        config.mode,
        config.template,
        config.line_ending,
        config.encoding,
        config.escape,
    ) + _init_settings(config)


def websocket_target(url: str) -> str | None:
    """The "host:port" of a ws:// or wss:// URL (the scheme's port if it has
    none), or None if it isn't one."""
    try:
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        host, port = parts.hostname, parts.port
    except ValueError:  # e.g. a port that isn't a number
        return None
    if scheme not in WEBSOCKET_SCHEMES or not host:
        return None
    if ":" in host:
        host = f"[{host}]"
    return f"{host}:{port or WEBSOCKET_SCHEMES[scheme]}"


def parse_port(value) -> int | None:
    """A port number (1-65535), or None."""
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not number.is_integer():
        return None
    port = int(number)
    return port if 1 <= port <= 65535 else None


def _text(value, default: str) -> str:
    return value if isinstance(value, str) else default


def _number(value, low: float, high: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return min(max(number, low), high)


# --- messages --------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")

Values = tuple[tuple[str, str], ...]


def values_json(values: Iterable[tuple[str, str]]) -> str:
    """{"name": "value", ...} (a later field with the same name wins)."""
    return json.dumps(dict(values), ensure_ascii=False)


def values_xml(values: Iterable[tuple[str, str]]) -> str:
    """CasparCG templateData: a componentData per field, its id the name, its
    "text" data the value."""
    items = "".join(
        f'<componentData id="{escape_xml(name)}">'
        f'<data id="text" value="{escape_xml(value)}"/></componentData>'
        for name, value in values
    )
    return f"<templateData>{items}</templateData>"


def render_text(
    template: str,
    config: TcpConfig,
    field_name: str = "",
    value: str = "",
    values: Values = (),
) -> str:
    """template with its placeholders replaced. The text of every placeholder
    is escaped with config.escape; the rest of the template is left as it
    is. A word in braces that isn't a placeholder stays as it is, so JSON
    templates need no escaping."""
    escape = ESCAPE_FUNCTIONS.get(config.escape, ESCAPE_FUNCTIONS[ESCAPE_NONE])

    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name == "field":
            text = field_name
        elif name == "value":
            text = value
        elif name == "json":
            text = values_json(values)
        elif name == "xml":
            text = values_xml(values)
        elif name == "template":
            text = config.template_name
        else:
            text = str(getattr(config, name))
        return escape(text)

    return _PLACEHOLDER.sub(substitute, template)


def render_message(
    template: str,
    config: TcpConfig,
    field_name: str = "",
    value: str = "",
    values: Values = (),
) -> str:
    """The message a template makes: each non-empty line of the template,
    rendered, followed by the line ending."""
    ending = LINE_ENDINGS.get(config.line_ending, "\n")
    lines = [line.rstrip("\r") for line in template.split("\n")]
    return "".join(
        render_text(line, config, field_name, value, values) + ending
        for line in lines
        if line.strip()
    )


def encode_message(message: str, config: TcpConfig) -> bytes:
    return message.encode(config.encoding, errors="replace")


def visible_text(message: str) -> str:
    """message with its control characters shown (␍ ␊ ␀) and a line break
    after each LF, for a preview."""
    return (
        message.replace("\r", "␍")
        .replace("\n", "␊\n")
        .replace("\0", "␀")
        .replace("\t", "␉")
    )


@dataclass(frozen=True)
class Preview:
    on_connect: str  # "" without an "on connect" message
    message: str  # a message for the first field, or with every field
    more_fields: int = 0  # (per field) messages for these many other fields


def preview(config: TcpConfig, values: Values) -> Preview:
    """What would be sent for these values."""
    on_connect = render_message(config.init, config, values=values)
    if config.mode == PER_FIELD:
        if values:
            name, value = values[0]
        else:
            name, value = "", ""
        message = render_message(config.template, config, name, value, values)
        return Preview(on_connect, message, max(0, len(values) - 1))
    return Preview(on_connect, render_message(config.template, config, values=values))


# --- the output ------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectionStatus:
    """A snapshot of the connection (see TcpOutput.connection_status())."""

    connected: bool = False  # a TCP connection is open (UDP: a socket)
    connecting: bool = False  # opening one
    connected_at: float | None = None
    last_reply: str | None = None  # the receiver's last answer
    last_reply_at: float | None = None
    reply_error: str | None = None  # the last error answer, until an OK
    # the receiver closed the last connections right away, this many in a row
    quick_closes: int = 0
    # TCP server: the port is open, and the addresses of the programs connected
    listening: bool = False
    clients: tuple[str, ...] = ()


class ListenError(OSError):
    """The TCP server can't listen on its port (another program uses it, or the
    system doesn't allow it)."""


class WebSocketError(ConnectionError):
    """The WebSocket connection failed or broke (websocket-client's errors,
    as an OSError: the worker reconnects)."""


class WebSocketRefused(WebSocketError):
    """The server answered the WebSocket handshake with an HTTP error status
    (401: the token is missing or wrong, 404: no WebSocket at this path...)."""

    def __init__(self, status_code: int, body: str = ""):
        message = f"HTTP {status_code}: {body}" if body else f"HTTP {status_code}"
        super().__init__(message)
        self.status_code = status_code


@dataclass(eq=False)
class _Client:
    """A program connected to the TCP server."""

    sock: socket.socket
    name: str  # its address, "192.168.1.20:51234"
    welcomed: bool = False  # got the "on connect" message and every value
    closed: bool = False


# an AMCP-style reply: a 3-digit code first ("202 CG OK", "404 CG ERROR"),
# maybe after a request id ("RES abc 202 COMMIT OK")
_REPLY_CODE = re.compile(r"^(?:RES\s+\S+\s+)?([1-5]\d\d)(?:\s|$)")


@dataclass
class _Batch:
    init: bool = False
    commands: list[str] = field(default_factory=list)
    changed: dict[str, str] = field(default_factory=dict)
    frame: bool = False
    full: bool = False
    values: Values = ()


class TcpOutput:
    """Sends the readings to config's host and port (see the module's
    docstring). update(), start(), stop(), set_config() and
    set_field_mapping() are called on the GUI thread."""

    def __init__(
        self,
        config: TcpConfig | None = None,
        field_mapping: Mapping[str, str] | None = None,
        clock=time.monotonic,
    ):
        self.config = config or TcpConfig()
        # {box name: name sent}; "" leaves the field out; a box that isn't in
        # it is sent as default_field_name()
        self.field_mapping: dict[str, str] = dict(field_mapping or {})
        self.running = False
        self._clock = clock
        # GUI thread only
        self._readings: dict[str, str] = {}  # box name: last good value
        self._last_queued: dict[str, str] = {}  # box name: last value queued
        self._last_full_at: float | None = None
        # shared with the worker and reader threads
        self._lock = threading.Lock()
        self._batch = _Batch()
        self._sock: socket.socket | None = None
        self._sock_endpoint: tuple | None = None
        self._conn_id = 0
        self._connected_at: float | None = None
        self._connecting = False
        self._peer_closed = False
        self._last_send_at: float | None = None
        self._last_reply: str | None = None
        self._last_reply_at: float | None = None
        self._reply_error: str | None = None
        self._reply_codes_seen = False
        self._quick_closes = 0
        # TCP server: the listening socket and the programs connected to it;
        # _listen_id changes when it closes (its accept thread then ends)
        self._server: socket.socket | None = None
        self._server_endpoint: tuple | None = None
        self._listen_id = 0
        self._clients: list[_Client] = []
        # start() and stop() increment it: a send of an earlier session neither
        # puts its batch back nor installs its connection
        self._session = 0
        # interrupts the waits (minimum interval, reconnecting) when stopping
        self._wake = threading.Event()
        self._reply_log = ThrottledErrorLog(LOG_LABEL)
        self._close_log = ThrottledErrorLog(LOG_LABEL)
        self._accept_log = ThrottledErrorLog(LOG_LABEL)
        self._worker = OutputWorker(
            LOG_LABEL,
            self._send,
            retry_on=(OSError,),
            error_log=ThrottledErrorLog(LOG_LABEL),
            clock=clock,
        )

    # --- GUI thread ---------------------------------------------------------

    def field_name(self, box_name: str) -> str:
        """The name box_name is sent as ("" if it is left out)."""
        name = self.field_mapping.get(box_name)
        if name is None:
            name = default_field_name(self.config.preset, box_name)
        return name.strip()

    def current_values(self) -> Values:
        """(name, value) of every field's last good reading, in reading order."""
        values = []
        for box_name, value in self._readings.items():
            name = self.field_name(box_name)
            if name:
                values.append((name, value))
        return tuple(values)

    def start(self):
        if self.running:
            return
        self.running = True
        self._wake.clear()
        self._last_queued.clear()
        self._last_full_at = None
        with self._lock:
            self._session += 1
            self._quick_closes = 0
            self._batch = _Batch(full=True, values=self.current_values())
        # connect right away, so the status tells whether the receiver is there
        self._worker.submit("send", None)

    def stop(self):
        self.running = False
        self._wake.set()
        self._worker.clear()
        self._worker.reset_status()
        with self._lock:
            self._session += 1
            self._quick_closes = 0
            self._batch = _Batch()
            self._last_send_at = None
        self._disconnect()
        self._stop_listening()

    def close(self):
        """Stop sending and end the worker thread."""
        self.stop()
        self._worker.stop()

    def status(self):
        """The sending's WorkerStatus (see output_worker)."""
        return self._worker.status()

    def connection_status(self) -> ConnectionStatus:
        with self._lock:
            quick_closes = self._quick_closes
            if (
                self._sock is not None
                and self._connected_at is not None
                and self._clock() - self._connected_at >= QUICK_CLOSE
            ):
                quick_closes = 0  # this connection stays open
            return ConnectionStatus(
                connected=self._sock is not None,
                connecting=self._connecting,
                connected_at=self._connected_at,
                last_reply=self._last_reply,
                last_reply_at=self._last_reply_at,
                reply_error=self._reply_error,
                quick_closes=quick_closes,
                listening=self._server is not None,
                clients=tuple(client.name for client in self._clients),
            )

    def set_config(self, config: TcpConfig):
        """Use new settings. Another host, port or transport closes the
        connection (the next one goes to the new receiver) or the TCP server's
        port and its connections; another format resends every value; another
        "on connect" message (or CasparCG template, channel or layer) is sent
        again."""
        old, self.config = self.config, config
        if old == config:
            return
        if old.endpoint != config.endpoint:
            self._disconnect()
            self._stop_listening()
            # the status (and the errors logged) are about the new receiver
            self._worker.reset_status()
            with self._lock:
                self._last_send_at = None
                self._quick_closes = 0
        else:
            resend_init = _init_settings(old) != _init_settings(config)
            if resend_init or _format_settings(old) != _format_settings(config):
                with self._lock:
                    self._batch.full = True
                    self._batch.values = self.current_values()
                    if resend_init:
                        self._batch.init = True
        if old.preset != config.preset:
            # the default names changed
            self._last_queued.clear()
        if self.running:
            self._worker.submit("send", None)

    def set_field_mapping(self, mapping: Mapping[str, str]):
        mapping = dict(mapping)
        if mapping == self.field_mapping:
            return
        self.field_mapping = mapping
        # the receiver gets every value under its (new) name
        self._last_queued.clear()

    def forget_boxes(self, box_names: Container[str]):
        """Forget the readings of the boxes that aren't in box_names any more."""
        for name in [name for name in self._readings if name not in box_names]:
            del self._readings[name]
            self._last_queued.pop(name, None)

    def send_command(self, template: str):
        """Send a command (a template, e.g. CasparCG's CG PLAY) once."""
        if not self.running:
            return
        with self._lock:
            self._batch.commands.append(template)
            self._batch.values = self.current_values()
        self._worker.submit("send", None)

    def resend_init(self):
        """Send the "on connect" message again (e.g. CasparCG's CG ADD)."""
        if not self.running:
            return
        with self._lock:
            self._batch.init = True
            self._batch.values = self.current_values()
        self._worker.submit("send", None)

    def update(
        self,
        results: list[TextDetectionTargetWithResult],
        resend_unchanged: Container[str] = (),
    ):
        """Queue this cycle's changed values (every good one with "Also resend
        unchanged values", and those of the boxes in resend_unchanged). Never
        blocks."""
        if not self.running:
            return
        config = self.config
        changed: dict[str, str] = {}
        for result in results:
            if result.result_state not in _GOOD_STATES:
                continue
            value = "" if result.result is None else str(result.result)
            self._readings[result.name] = value
            name = self.field_name(result.name)
            if not name:
                continue
            if (
                config.send_same
                or result.name in resend_unchanged
                or self._last_queued.get(result.name) != value
            ):
                changed[name] = value
                self._last_queued[result.name] = value
        now = self._clock()
        refresh = (
            config.refresh_interval > 0
            and bool(self._readings)
            and (
                self._last_full_at is None
                or now - self._last_full_at >= config.refresh_interval
            )
        )
        if refresh:
            self._last_full_at = now
        if not changed and not refresh:
            return
        values = self.current_values()
        with self._lock:
            batch = self._batch
            batch.values = values
            batch.changed.update(changed)
            batch.frame = batch.frame or bool(changed)
            batch.full = batch.full or refresh
        self._worker.submit("send", None)

    # --- worker thread ------------------------------------------------------

    def _send(self, _key, _item):
        config = self.config
        with self._lock:
            session = self._session
        if not self.running or not config.is_complete:
            raise SendSkipped()
        if config.listens:
            self._send_to_clients(config, session)
            return
        self._ensure_connected(config, session)
        self._wait_min_interval(config)
        with self._lock:
            if not self.running or self._session != session:
                raise SendSkipped()
            batch, self._batch = self._batch, _Batch(values=self._batch.values)
        messages = self._messages(config, batch)
        if not messages:
            raise SendSkipped()
        try:
            self._transmit(config, messages)
        except OSError:
            with self._lock:
                stopped = not self.running or self._session != session
            if stopped:
                # stop() closed the connection: nothing to send or retry
                raise SendSkipped() from None
            self._put_back(batch)
            if self.config.endpoint != config.endpoint:
                # set_config() closed it: the next send goes to the new
                # receiver (it submitted one)
                raise SendSkipped() from None
            self._disconnect()
            raise
        with self._lock:
            self._last_send_at = self._clock()
        logger.debug(
            "%s: sent %d message(s) to %s", LOG_LABEL, len(messages), config.target
        )

    def _send_to_clients(self, config: TcpConfig, session: int):
        """TCP server: the batch to every program connected, the "on connect"
        message and every value first to those that just connected. A program
        that can't be sent to is dropped (the others still get the batch)."""
        self._ensure_listening(config, session)
        self._wait_min_interval(config)
        with self._lock:
            if not self.running or self._session != session:
                raise SendSkipped()
            batch, self._batch = self._batch, _Batch(values=self._batch.values)
            clients = list(self._clients)
        if not clients:
            # nothing is lost: a program that connects gets every value
            raise SendSkipped()
        messages = self._messages(config, batch)
        welcome = self._messages(
            config,
            _Batch(init=True, commands=batch.commands, full=True, values=batch.values),
        )
        sent = 0
        for client in clients:
            payload = messages if client.welcomed else welcome
            if not payload:
                continue
            try:
                client.sock.sendall(
                    b"".join(encode_message(message, config) for message in payload)
                )
            except OSError as e:
                if self._remove_client(client):
                    logger.info(
                        "%s: dropped %s, which didn't take the data (%s)",
                        LOG_LABEL,
                        client.name,
                        e,
                    )
                continue
            client.welcomed = True
            sent += 1
        if not sent:
            raise SendSkipped()
        with self._lock:
            self._last_send_at = self._clock()
        logger.debug("%s: sent to %d program(s)", LOG_LABEL, sent)

    def _ensure_listening(self, config: TcpConfig, session: int):
        """Open the TCP server's port unless it's open already (raises
        ListenError while it can't be used: the worker tries again)."""
        endpoint = config.endpoint
        with self._lock:
            if self._server is not None and self._server_endpoint == endpoint:
                return
        self._stop_listening()
        server = _open_server(config)
        with self._lock:
            current = (
                self.running
                and self._session == session
                and self.config.endpoint == endpoint
            )
            if current:
                self._listen_id += 1
                listen_id = self._listen_id
                self._server = server
                self._server_endpoint = endpoint
                self._last_reply = self._last_reply_at = self._reply_error = None
                self._reply_codes_seen = False
        if not current:
            # stopped, or another port, meanwhile
            _close_socket(server)
            raise SendSkipped()
        threading.Thread(
            target=self._accept_clients,
            args=(server, listen_id, config.encoding),
            name="tcp-output-accept",
            daemon=True,
        ).start()
        logger.info("%s: listening on port %s", LOG_LABEL, config.port)

    def _stop_listening(self):
        """Close the TCP server's port and every connection to it."""
        with self._lock:
            server, self._server = self._server, None
            self._server_endpoint = None
            self._listen_id += 1  # its accept thread stops
            clients, self._clients = self._clients, []
            for client in clients:
                client.closed = True
        if server is not None:
            _close_socket(server)
        for client in clients:
            _close_socket(client.sock)

    def _remove_client(self, client: _Client) -> bool:
        """Drop a program's connection. False if it was dropped already."""
        with self._lock:
            present = client in self._clients
            if present:
                self._clients.remove(client)
            client.closed = True
        if present:
            _close_socket(client.sock)
        return present

    @staticmethod
    def _messages(config: TcpConfig, batch: _Batch) -> list[str]:
        messages = []
        if batch.init and config.init.strip():
            messages.append(render_message(config.init, config, values=batch.values))
        for command in batch.commands:
            messages.append(render_message(command, config, values=batch.values))
        if config.mode == PER_FIELD:
            items = batch.values if batch.full else tuple(batch.changed.items())
            messages.extend(
                render_message(config.template, config, name, value, batch.values)
                for name, value in items
            )
        elif (batch.frame or batch.full) and batch.values:
            messages.append(
                render_message(config.template, config, values=batch.values)
            )
        return [message for message in messages if message]

    def _put_back(self, batch: _Batch):
        """A batch that couldn't be sent goes back, under what was queued
        meanwhile (newer values win)."""
        with self._lock:
            queued = self._batch
            changed = dict(batch.changed)
            changed.update(queued.changed)
            self._batch = _Batch(
                init=batch.init or queued.init,
                commands=batch.commands + queued.commands,
                changed=changed,
                frame=batch.frame or queued.frame,
                full=batch.full or queued.full,
                values=queued.values,
            )

    def _wait_min_interval(self, config: TcpConfig):
        with self._lock:
            last = self._last_send_at
        if config.min_interval <= 0 or last is None:
            return
        delay = last + config.min_interval - self._clock()
        if delay > 0:
            # newer values queued meanwhile are taken after the wait
            self._wake.wait(delay)

    def _ensure_connected(self, config: TcpConfig, session: int):
        """Open the connection unless it's open to config's receiver. A new
        connection sends the "on connect" message and every value."""
        endpoint = config.endpoint
        with self._lock:
            stale = self._sock is not None and (
                self._sock_endpoint != endpoint or self._peer_closed
            )
        if stale:
            self._disconnect()
        with self._lock:
            if self._sock is not None:
                return
            quick_closes = self._quick_closes
        if quick_closes:
            # the receiver hung up right away: wait before trying again
            self._wake.wait(quick_close_delay(quick_closes))
        with self._lock:
            if not self.running or self._session != session:
                raise SendSkipped()
            self._connecting = True
        try:
            sock = _open_socket(config)
        finally:
            with self._lock:
                self._connecting = False
        with self._lock:
            # (checked under the lock: stop() closes what is installed after)
            current = (
                self.running
                and self._session == session
                and self.config.endpoint == endpoint
            )
            if current:
                self._conn_id += 1
                conn_id = self._conn_id
                self._sock = sock
                self._sock_endpoint = endpoint
                self._connected_at = self._clock()
                self._peer_closed = False
                self._last_reply = self._last_reply_at = self._reply_error = None
                self._reply_codes_seen = False
                self._batch.init = True
                self._batch.full = True
        if not current:
            # stopped, or another receiver, while connecting
            _close_connection(sock)
            raise SendSkipped()
        if config.transport in (TCP, WEBSOCKET):
            if config.websocket:
                reader, args = self._read_websocket, (sock, conn_id, config.target)
            else:
                reader = self._read_replies
                args = (sock, conn_id, config.encoding, config.target)
            threading.Thread(
                target=reader, args=args, name="tcp-output-replies", daemon=True
            ).start()
            if quick_closes:
                logger.debug("%s: connected to %s", LOG_LABEL, config.target)
            else:
                logger.info("%s: connected to %s", LOG_LABEL, config.target)

    def _transmit(self, config: TcpConfig, messages: list[str]):
        with self._lock:
            sock = self._sock
        if sock is None:
            raise ConnectionError("not connected")
        if config.websocket:
            # a text message per message (always UTF-8)
            try:
                for message in messages:
                    sock.send(message)
            except websocket.WebSocketException as e:
                raise WebSocketError(str(e) or type(e).__name__) from e
            return
        payloads = [encode_message(message, config) for message in messages]
        if config.transport == UDP:
            # a datagram per message
            for payload in payloads:
                sock.send(payload)
        else:
            sock.sendall(b"".join(payloads))

    def _disconnect(self):
        with self._lock:
            sock, self._sock = self._sock, None
            self._sock_endpoint = None
            self._connected_at = None
            self._peer_closed = False
            # the reader thread of the old connection stops counting
            self._conn_id += 1
        if sock is not None:
            _close_connection(sock)

    # --- reader thread ------------------------------------------------------

    def _read_replies(self, sock: socket.socket, conn_id: int, encoding: str, target):
        separator = "\n".encode(encoding) if encoding.startswith("utf-16") else b"\n"
        buffer = b""
        while True:
            try:
                data = sock.recv(4096)
            except TimeoutError:  # nothing to read for a while
                with self._lock:
                    if self._conn_id != conn_id:
                        return
                continue
            except OSError:
                data = b""
            if not data:
                break
            buffer += data
            *lines, buffer = buffer.split(separator)
            if len(buffer) > MAX_REPLY_LENGTH:
                buffer = b""
            for line in lines:
                self._record_reply(
                    conn_id, line.decode(encoding, errors="replace"), target
                )
        self._connection_closed(conn_id, target)

    def _read_websocket(self, ws: websocket.WebSocket, conn_id: int, target: str):
        """Reads the WebSocket server's messages (each one an answer) until it
        closes the connection; websocket-client answers its pings."""
        while True:
            try:
                opcode, data = ws.recv_data()
            except websocket.WebSocketTimeoutException:  # nothing for a while
                with self._lock:
                    if self._conn_id != conn_id:
                        return
                continue
            except (websocket.WebSocketException, OSError, ValueError):
                break  # closed, or broken
            if opcode == websocket.ABNF.OPCODE_CLOSE:
                break
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            self._record_reply(conn_id, data[:MAX_REPLY_LENGTH], target)
        self._connection_closed(conn_id, target)

    def _connection_closed(self, conn_id: int, target: str):
        """The receiver closed the connection (or it broke): reconnect, after a
        wait if it closed right after it was opened."""
        with self._lock:
            current = self._conn_id == conn_id
            quick_closes = 0
            if current:
                self._peer_closed = True
                connected_at = self._connected_at
                if connected_at is not None and (
                    self._clock() - connected_at < QUICK_CLOSE
                ):
                    self._quick_closes += 1
                else:
                    self._quick_closes = 0
                quick_closes = self._quick_closes
        if current and self.running:
            if quick_closes:
                self._close_log.error(
                    f"{target} closed the connection right after it was opened "
                    f"({quick_closes} time(s) in a row); reconnecting in "
                    f"{quick_close_delay(quick_closes):.1f} s"
                )
            else:
                logger.info(
                    "%s: %s closed the connection; reconnecting", LOG_LABEL, target
                )
            # reconnect (with backoff while it can't)
            self._worker.submit("send", None)

    def _record_reply(self, conn_id: int | None, text: str, target: str):
        """A line the receiver sent (conn_id None: a program connected to the
        TCP server)."""
        line = text.strip(" \t\r\n\0")
        if not line:
            return
        match = _REPLY_CODE.match(line)
        code = int(match.group(1)) if match else None
        error = code is not None and code >= 400
        with self._lock:
            if conn_id is not None and conn_id != self._conn_id:
                return
            if code is not None:
                self._reply_codes_seen = True
            # with status codes (AMCP), the status line, not the data after it
            if code is not None or not self._reply_codes_seen:
                self._last_reply = line[:200]
                self._last_reply_at = self._clock()
            if error:
                self._reply_error = line[:200]
            elif code is not None and 200 <= code < 300:
                self._reply_error = None
        if error:
            self._reply_log.error(f"{target} answered {line[:200]!r}")
        else:
            logger.debug("%s: %s answered %r", LOG_LABEL, target, line[:200])

    # --- TCP server: accept and reader threads ------------------------------

    def _accept_clients(self, server: socket.socket, listen_id: int, encoding: str):
        while True:
            with self._lock:
                if self._listen_id != listen_id:
                    return
            try:
                sock, address = server.accept()
            except TimeoutError:  # nothing for ACCEPT_POLL: check again
                continue
            except OSError as e:
                with self._lock:
                    if self._listen_id != listen_id:
                        return  # closed by _stop_listening()
                # e.g. too many open files: don't spin
                self._accept_log.error(f"can't accept a connection: {e}")
                time.sleep(ACCEPT_POLL)
                continue
            name = _address_text(address)
            with self._lock:
                current = self._listen_id == listen_id
                full = len(self._clients) >= MAX_CLIENTS
                client = None
                if current and not full:
                    client = _Client(sock, name)
                    self._clients.append(client)
            if client is None:
                _close_socket(sock)
                if not current:
                    return
                self._accept_log.error(
                    f"refused a connection from {name}: {MAX_CLIENTS} programs "
                    "are connected already"
                )
                continue
            sock.settimeout(SEND_TIMEOUT)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            logger.info("%s: %s connected", LOG_LABEL, name)
            threading.Thread(
                target=self._read_client,
                args=(client, encoding),
                name="tcp-output-client",
                daemon=True,
            ).start()
            # the "on connect" message and every value
            self._worker.submit("send", None)

    def _read_client(self, client: _Client, encoding: str):
        """Reads what a connected program sends (so it never blocks on a full
        buffer), until it closes the connection or is dropped."""
        separator = "\n".encode(encoding) if encoding.startswith("utf-16") else b"\n"
        buffer = b""
        while True:
            try:
                data = client.sock.recv(4096)
            except TimeoutError:  # nothing to read for a while
                if client.closed:
                    return
                continue
            except OSError:
                data = b""
            if not data:
                break
            buffer += data
            *lines, buffer = buffer.split(separator)
            if len(buffer) > MAX_REPLY_LENGTH:
                buffer = b""
            for line in lines:
                if not client.closed:
                    self._record_reply(
                        None, line.decode(encoding, errors="replace"), client.name
                    )
        if self._remove_client(client):
            logger.info("%s: %s disconnected", LOG_LABEL, client.name)


def quick_close_delay(quick_closes: int) -> float:
    """The wait before reconnecting after quick_closes connections in a row
    were closed right away."""
    first, longest = QUICK_CLOSE_BACKOFF
    return min(first * 2 ** min(max(quick_closes - 1, 0), 16), longest)


def _open_socket(config: TcpConfig):
    """A connected socket (a websocket.WebSocket for a WebSocket)."""
    if config.websocket:
        return _open_websocket(config)
    if config.transport == UDP:
        family, kind, proto, _, address = socket.getaddrinfo(
            config.host, config.port, 0, socket.SOCK_DGRAM
        )[0]
        sock = socket.socket(family, kind, proto)
        try:
            # the default destination of send()
            sock.connect(address)
        except OSError:
            sock.close()
            raise
        return sock
    sock = socket.create_connection((config.host, config.port), timeout=CONNECT_TIMEOUT)
    sock.settimeout(SEND_TIMEOUT)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    return sock


def _open_websocket(config: TcpConfig) -> websocket.WebSocket:
    """A WebSocket connection to config's URL, with its headers in the
    handshake. websocket-client's errors are raised as OSErrors (the worker
    retries those): WebSocketRefused when the server answered with an HTTP
    error status, socket.gaierror for an unknown host, TimeoutError."""
    headers, _ = parse_headers(config.headers)
    try:
        ws = websocket.create_connection(
            config.url,
            timeout=CONNECT_TIMEOUT,
            header=[f"{name}: {value}" for name, value in headers.items()],
            enable_multithread=True,  # sends and the reader thread share it
        )
    except websocket.WebSocketBadStatusException as e:
        body = e.resp_body or b""
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        raise WebSocketRefused(e.status_code, " ".join(body.split())[:200]) from None
    except websocket.WebSocketAddressException as e:
        raise socket.gaierror(str(e)) from None
    except websocket.WebSocketTimeoutException as e:
        raise TimeoutError(str(e) or "timed out") from None
    except websocket.WebSocketException as e:
        raise WebSocketError(str(e) or type(e).__name__) from None
    ws.settimeout(SEND_TIMEOUT)
    return ws


def _open_server(config: TcpConfig) -> socket.socket:
    """The TCP server's listening socket, on every address of this computer
    (IPv6 and IPv4 where the system can, else IPv4).

    On Windows the port is taken exclusively: otherwise Windows lets it be
    shared with a program that listens on it with IPv4 (or IPv6) alone, which
    then gets those connections while ScoreSight says it listens. (macOS
    still allows it for a port held over IPv4: #119.)"""
    dualstack = socket.has_dualstack_ipv6()
    server = socket.socket(socket.AF_INET6 if dualstack else socket.AF_INET)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows
            server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            # as socket.create_server: reopen at once after a restart
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if dualstack:
            server.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        server.bind(("", config.port))
        server.listen()
    except OSError as e:
        server.close()
        raise ListenError(e.errno, e.strerror or str(e)) from e
    # accept() wakes up this often to see whether to stop
    server.settimeout(ACCEPT_POLL)
    return server


def _address_text(address) -> str:
    """ "192.168.1.20:51234" ("[::1]:51234"; an IPv4 address that came over
    IPv6 without its "::ffff:")."""
    host, port = address[0], address[1]
    if host.startswith("::ffff:") and "." in host:
        host = host[len("::ffff:") :]
    if ":" in host:
        host = f"[{host}]"
    return f"{host}:{port}"


def _close_connection(connection):
    if isinstance(connection, websocket.WebSocket):
        _close_websocket(connection)
    else:
        _close_socket(connection)


def _close_websocket(ws: websocket.WebSocket):
    """Say goodbye (a close frame, if it goes out at once), then close."""
    try:
        if ws.sock is not None:
            ws.sock.settimeout(0.5)
        ws.send_close()
    except Exception:  # broken already: nothing to say goodbye on
        pass
    ws.abort()  # wakes up the reader thread
    ws.shutdown()


def _close_socket(sock: socket.socket):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass

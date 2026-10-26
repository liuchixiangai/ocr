"""Outbound HTTP API: sends the readings to a user-configured URL (POST/PUT as
JSON, XML or CSV, or GET query parameters).

update_out_api() is called on the GUI thread with every detection cycle's
results. It builds what to send from a snapshot of them and hands it to a
background OutputWorker, which keeps only the latest value per key: a slow or
unreachable endpoint never makes requests (or threads) pile up, and never
blocks the caller.

By default (the behavior of 0.2.0 and before) every cycle sends one request
with all the readings. Options:

- Only changes: send only when a value changed since it was last sent (and,
  with "Resend all", every value again every so many seconds).
- Fields per request: send the changed fields in requests of at most N
  fields each (e.g. 6 for an Epiphan Pearl), instead of one per cycle. A URL
  with {field} and {value} in it sends one request per field, with the
  field's name and value (percent-encoded) in their place (e.g. a TriCaster
  DataLink key).
- Rate: at most N requests per second.
- Safe names: the field names in lower case with letters, digits and "_"
  only ("Home Score" becomes home_score), for receivers that allow no other.
- Headers: extra request headers, e.g. an Authorization token.

A login can be put in the URL (http://user:password@host/...): requests sends
it with HTTP Basic auth, or Digest when the endpoint asks for Digest.

PRESETS fill these settings in for products with a known HTTP interface.
"""

import csv
import io
import json
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import NamedTuple
from urllib.parse import quote, unquote, urlencode, urlparse, urlsplit, urlunsplit

import requests
from requests.auth import HTTPDigestAuth

from output_worker import (
    HTTP_TIMEOUT,
    OutputWorker,
    ThrottledErrorLog,
    check_http_response,
)
from sc_logging import logger
from storage import fetch_data, subscribe_to_data
from text_detection_target import TextDetectionTargetWithResult

ENCODING_JSON_FULL = "JSON (Full)"
ENCODING_JSON_SIMPLE = "JSON (Simple key-value)"
ENCODING_XML = "XML"
ENCODING_CSV = "CSV"
ENCODINGS = (ENCODING_JSON_FULL, ENCODING_JSON_SIMPLE, ENCODING_XML, ENCODING_CSV)
METHODS = ("GET", "POST", "PUT")

# in a URL: replaced with each field's (sent) name and value
FIELD_PLACEHOLDER = "{field}"
VALUE_PLACEHOLDER = "{value}"

# Formats without a per-reading state (JSON simple key-value and GET) carry only
# these: a receiver couldn't tell a rejected or empty reading from a real one.
GOOD_STATES = (
    TextDetectionTargetWithResult.ResultState.Success,
    TextDetectionTargetWithResult.ResultState.SameNoChange,
)

SETTINGS_FILE = "scoresight.json"


def _as_int(value, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _as_rate(value) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return 0.0
    return rate if rate > 0 else 0.0


out_api_url = fetch_data(SETTINGS_FILE, "out_api_url", None)
out_api_encoding = fetch_data(SETTINGS_FILE, "out_api_encoding", ENCODING_JSON_FULL)
out_api_method = fetch_data(SETTINGS_FILE, "out_api_method", "POST")
out_api_only_changes = bool(fetch_data(SETTINGS_FILE, "out_api_only_changes", False))
out_api_refresh_s = _as_int(fetch_data(SETTINGS_FILE, "out_api_refresh_s", 0))
out_api_fields_per_request = _as_int(
    fetch_data(SETTINGS_FILE, "out_api_fields_per_request", 0)
)
out_api_max_rate = _as_rate(fetch_data(SETTINGS_FILE, "out_api_max_rate", 0))
out_api_safe_names = bool(fetch_data(SETTINGS_FILE, "out_api_safe_names", False))
out_api_headers = fetch_data(SETTINGS_FILE, "out_api_headers", "") or ""


def is_valid_url_urllib(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False


def display_url(url: str) -> str:
    """url without a user name and password (for the log and the status)."""
    try:
        parts = urlsplit(url)
        if parts.username is None and parts.password is None:
            return url
        netloc = parts.netloc.rpartition("@")[2]
        return urlunsplit(parts._replace(netloc=netloc))
    except ValueError:
        return url


def has_placeholders(url: str | None) -> bool:
    return bool(url) and (FIELD_PLACEHOLDER in url or VALUE_PLACEHOLDER in url)


def per_field_mode(url: str | None, fields_per_request: int) -> bool:
    """Whether the readings go out field by field (in batches), rather than
    as one request per detection cycle."""
    return has_placeholders(url) or fields_per_request > 0


def max_fields_per_request(url: str | None, fields_per_request: int) -> int | None:
    """How many fields one request carries (None: all of them)."""
    if has_placeholders(url):
        return 1
    return fields_per_request or None


# --- names ----------------------------------------------------------------------

# Epiphan Pearl's variable names: a letter or "_", then letters, digits and "_",
# at most 32 characters
SAFE_NAME_MAX_LENGTH = 32
_NOT_SAFE = re.compile(r"[^a-z0-9_]+")


def safe_name(name: str) -> str:
    """name in lower case with letters, digits and "_" only: "Home Score" is
    home_score, "Période" periode, "1st Half" _1st_half."""
    decomposed = unicodedata.normalize("NFKD", str(name))
    ascii_name = "".join(c for c in decomposed if not unicodedata.combining(c))
    safe = _NOT_SAFE.sub("_", ascii_name.lower()).strip("_") or "field"
    if safe[0].isdigit():
        safe = "_" + safe
    return safe[:SAFE_NAME_MAX_LENGTH]


def safe_names(names: Iterable[str]) -> dict[str, str]:
    """{name: its safe name}. Two names never get the same safe name: a later
    one gets a suffix (home_score, home_score_2)."""
    result: dict[str, str] = {}
    taken: set[str] = set()
    for name in names:
        if name in result:
            continue
        base = candidate = safe_name(name)
        number = 2
        while candidate in taken:
            suffix = f"_{number}"
            candidate = base[: SAFE_NAME_MAX_LENGTH - len(suffix)] + suffix
            number += 1
        taken.add(candidate)
        result[name] = candidate
    return result


def sent_names(names: Iterable[str], safe: bool) -> dict[str, str]:
    """{box name: the name it is sent with}."""
    names = list(names)
    return safe_names(names) if safe else {name: name for name in names}


# --- headers ----------------------------------------------------------------------

_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


def parse_headers(text: str | None) -> tuple[dict[str, str], int]:
    """The "Name: value" lines of text as {name: value}, and how many other
    (non-empty) lines there were."""
    headers: dict[str, str] = {}
    invalid = 0
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        name, separator, value = line.partition(":")
        name = name.strip()
        if not separator or not _HEADER_NAME.match(name):
            invalid += 1
            continue
        headers[name] = value.strip()
    return headers, invalid


def _with_headers(base: Mapping[str, str], extra: Mapping[str, str]) -> dict[str, str]:
    """base with extra's headers added; extra wins (names ignore case)."""
    merged = dict(base)
    for name, value in extra.items():
        for existing in [key for key in merged if key.lower() == name.lower()]:
            del merged[existing]
        merged[name] = value
    return merged


# --- presets ----------------------------------------------------------------------


@dataclass(frozen=True)
class ApiPreset:
    """Settings of the outbound API for a product."""

    key: str
    name: str  # a product name: not translated
    scheme: str
    default_login: str  # "user:password", or ""
    placeholder_host: str  # replaced by the device's address
    path_and_query: str
    method: str
    encoding: str
    fields_per_request: int
    only_changes: bool
    max_rate: int  # requests per second, 0: no limit
    refresh_s: int  # "Resend all" every so many seconds, 0: never
    safe_names: bool

    def url(self, current_url: str | None = None) -> str:
        """The preset's URL, with the address (and login) of current_url if it
        has one, else the placeholder address and the default login."""
        netloc = None
        if current_url:
            try:
                parts = urlsplit(current_url.strip())
                host = parts.hostname
            except ValueError:
                host = None
            if host and host.upper() not in PLACEHOLDER_HOSTS:
                netloc = parts.netloc
                if "@" not in netloc and self.default_login:
                    netloc = f"{self.default_login}@{netloc}"
        if netloc is None:
            login = f"{self.default_login}@" if self.default_login else ""
            netloc = login + self.placeholder_host
        return f"{self.scheme}://{netloc}{self.path_and_query}"

    def matches(self, settings: "ApiSettings") -> bool:
        """Whether settings are this preset's (with any address and login)."""
        try:
            parts = urlsplit((settings.url or "").strip())
        except ValueError:
            return False
        path_and_query = parts.path + (f"?{parts.query}" if parts.query else "")
        return (
            path_and_query == self.path_and_query
            and settings.method == self.method
            and (settings.method == "GET" or settings.encoding == self.encoding)
            and settings.fields_per_request == self.fields_per_request
            and settings.only_changes == self.only_changes
            and settings.max_rate == self.max_rate
            and settings.refresh_s == self.refresh_s
            and settings.safe_names == self.safe_names
        )


PRESETS = {
    # Vizrt TriCaster DataLink (Automation and Integration Guide, 8.3): one
    # GET per key; %Key% in a title shows the value. Login: Basic or Digest,
    # admin/admin by default. The highest rate it takes isn't documented.
    "tricaster": ApiPreset(
        key="tricaster",
        name="TriCaster DataLink",
        scheme="http",
        default_login="admin:admin",
        placeholder_host="TRICASTER-IP",
        path_and_query="/v1/datalink?key={field}&value={value}",
        method="GET",
        encoding=ENCODING_JSON_SIMPLE,
        fields_per_request=1,
        only_changes=True,
        max_rate=10,
        refresh_s=0,
        safe_names=False,
    ),
    # Epiphan Pearl custom system variables: at most 5 requests a second and 6
    # variables a request, names of letters, digits and "_"; %(name) in a
    # layout's text shows the value. Cleared when Pearl restarts: resent.
    "pearl": ApiPreset(
        key="pearl",
        name="Epiphan Pearl",
        scheme="http",
        default_login="admin:PASSWORD",
        placeholder_host="PEARL-IP",
        path_and_query="/admin/set_variables.cgi",
        method="GET",
        encoding=ENCODING_JSON_SIMPLE,
        fields_per_request=6,
        only_changes=True,
        max_rate=5,
        refresh_s=10,
        safe_names=True,
    ),
    # 光为 OCR: reserved website push. Placeholder host is never contacted.
    "guangwei_website": ApiPreset(
        key="guangwei_website",
        name="光为网站（预留）",
        scheme="https",
        default_login="",
        placeholder_host="YOUR-WEBSITE-HOST",
        path_and_query="/api/score/update",
        method="POST",
        encoding=ENCODING_JSON_FULL,
        fields_per_request=0,
        only_changes=True,
        max_rate=5,
        refresh_s=0,
        safe_names=True,
    ),
}
PLACEHOLDER_HOSTS = frozenset(p.placeholder_host.upper() for p in PRESETS.values())


@dataclass(frozen=True)
class ApiSettings:
    """The outbound API's settings."""

    url: str | None
    method: str
    encoding: str | None
    fields_per_request: int = 0
    only_changes: bool = False
    max_rate: float = 0
    refresh_s: int = 0
    safe_names: bool = False


def current_settings() -> ApiSettings:
    return ApiSettings(
        url=out_api_url,
        method=out_api_method,
        encoding=out_api_encoding,
        fields_per_request=out_api_fields_per_request,
        only_changes=out_api_only_changes,
        max_rate=out_api_max_rate,
        refresh_s=out_api_refresh_s,
        safe_names=out_api_safe_names,
    )


def matching_preset(settings: ApiSettings) -> str | None:
    """The key of the preset whose settings these are, or None."""
    for preset in PRESETS.values():
        if preset.matches(settings):
            return preset.key
    return None


@dataclass(frozen=True)
class SendMode:
    """How the readings are sent, for the window's status."""

    per_field: bool = False
    fields_per_request: int | None = None  # None: all in one request
    only_changes: bool = False
    max_rate: float = 0
    # the URL still has a preset's placeholder address
    needs_address: bool = False


def send_mode() -> SendMode:
    url = out_api_url
    try:
        host = (urlsplit(url or "").hostname or "").upper()
    except ValueError:
        host = ""
    return SendMode(
        per_field=per_field_mode(url, out_api_fields_per_request),
        fields_per_request=max_fields_per_request(url, out_api_fields_per_request),
        only_changes=out_api_only_changes,
        max_rate=out_api_max_rate,
        needs_address=host in PLACEHOLDER_HOSTS,
    )


# --- settings changes ---------------------------------------------------------------

# A request waiting to be sent, or being retried while the endpoint is
# unreachable, was built for the settings at that time: it is dropped when they
# change or the output is turned off (the next cycle builds a new one). The
# status (what was sent, what failed) and what counts as sent start over too.


def _restart():
    global _last_refresh_at
    _worker.clear()
    _worker.reset_status()
    _forget_sent()
    _last_refresh_at = None
    _apply_pacing()


def _apply_pacing():
    _worker.max_batch = max_fields_per_request(out_api_url, out_api_fields_per_request)
    _worker.min_interval = 1.0 / out_api_max_rate if out_api_max_rate > 0 else 0.0


def setup_out_api_url(url):
    global out_api_url
    out_api_url = url
    _restart()


def setup_out_api_encoding(encoding):
    global out_api_encoding
    out_api_encoding = encoding
    _restart()


def setup_out_api_method(method):
    global out_api_method
    out_api_method = method
    _restart()


def setup_out_api_only_changes(only_changes):
    global out_api_only_changes
    out_api_only_changes = bool(only_changes)
    _restart()


def setup_out_api_refresh_s(seconds):
    global out_api_refresh_s
    out_api_refresh_s = _as_int(seconds)
    _restart()


def setup_out_api_fields_per_request(fields):
    global out_api_fields_per_request
    out_api_fields_per_request = _as_int(fields)
    _restart()


def setup_out_api_max_rate(rate):
    global out_api_max_rate
    out_api_max_rate = _as_rate(rate)
    _restart()


def setup_out_api_safe_names(safe):
    global out_api_safe_names
    out_api_safe_names = bool(safe)
    _restart()


def setup_out_api_headers(headers):
    global out_api_headers
    out_api_headers = headers or ""
    _restart()


def setup_out_api_enabled(enabled):
    if not enabled:
        _restart()


def out_api_status():
    """The sending's WorkerStatus (see output_worker)."""
    return _worker.status()


SETTING_SUBSCRIPTIONS = {
    "out_api_url": setup_out_api_url,
    "out_api_encoding": setup_out_api_encoding,
    "out_api_method": setup_out_api_method,
    "out_api_only_changes": setup_out_api_only_changes,
    "out_api_refresh_s": setup_out_api_refresh_s,
    "out_api_fields_per_request": setup_out_api_fields_per_request,
    "out_api_max_rate": setup_out_api_max_rate,
    "out_api_safe_names": setup_out_api_safe_names,
    "out_api_headers": setup_out_api_headers,
    "enable_out_api": setup_out_api_enabled,
}
for _name, _callback in SETTING_SUBSCRIPTIONS.items():
    subscribe_to_data(SETTINGS_FILE, _name, _callback)


# --- requests ------------------------------------------------------------------------


class ApiRequest(NamedTuple):
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None


class SnapshotItem(NamedTuple):
    """One cycle's request, and the good values it carries."""

    request: ApiRequest
    values: tuple[tuple[str, str], ...]
    # _sent_epoch when it was queued: what was sent is recorded only if it
    # hasn't changed since (the settings didn't change meanwhile)
    epoch: int = 0


class FieldConfig(NamedTuple):
    """The settings a field's request is built with."""

    url: str
    method: str
    encoding: str
    headers: tuple[tuple[str, str], ...]


class FieldItem(NamedTuple):
    """One field's good reading, to send in a request of one or more fields."""

    name: str  # the box's name (the worker's key)
    sent_name: str  # the name it is sent with
    text: str
    state: str
    rect: tuple[float, float, float, float]
    config: FieldConfig
    epoch: int = 0  # as SnapshotItem.epoch


def good_values(data: list[TextDetectionTargetWithResult]) -> dict[str, str]:
    """{name: text} of the readings in a good state (Success, SameNoChange)."""
    return {r.name: r.result for r in data if r.result_state in GOOD_STATES}


# (name, text, state, x, y, width, height) of a reading, for the XML and CSV
Row = tuple[str, str, str, float, float, float, float]


def _xml_body(rows: Iterable[Row]) -> bytes:
    root = ET.Element("root")
    for name, text, state, x, y, width, height in rows:
        resultEl = ET.SubElement(root, "result")
        resultEl.set("name", name)
        resultEl.set("result", text)
        resultEl.set("result_state", state)
        resultEl.set("x", str(x))
        resultEl.set("y", str(y))
        resultEl.set("width", str(width))
        resultEl.set("height", str(height))
    return ET.tostring(root, encoding="utf-8")


def _csv_body(rows: Iterable[Row]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Text", "State", "X", "Y", "Width", "Height"])
    for row in rows:
        writer.writerow(row)
    return output.getvalue().encode("utf-8")


def _json_full_body(rows: Iterable[Row]) -> bytes:
    return json.dumps(
        [
            {
                "name": name,
                "text": text,
                "state": state,
                "rect": {"x": x, "y": y, "width": width, "height": height},
            }
            for name, text, state, x, y, width, height in rows
        ]
    ).encode("utf-8")


def _body(rows: list[Row], values: dict[str, str], encoding: str):
    """(body, content type) of a POST/PUT, or None if there is nothing to send
    (no good values for the stateless JSON (Simple key-value))."""
    if encoding == ENCODING_JSON_SIMPLE:
        if not values:
            return None
        return json.dumps(values).encode("utf-8"), "application/json"
    if encoding == ENCODING_XML:
        return _xml_body(rows), "application/xml"
    if encoding == ENCODING_CSV:
        return _csv_body(rows), "text/csv"
    return _json_full_body(rows), "application/json"  # ENCODING_JSON_FULL


def build_request(
    data: list[TextDetectionTargetWithResult],
    url: str,
    method: str,
    encoding: str,
    names: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
) -> ApiRequest | None:
    """The request for one detection cycle, or None if there is nothing to send.

    GET and "JSON (Simple key-value)" carry no state, so they contain only the
    readings in a good state, and nothing is sent when there are none. The other
    encodings send every box with its state. names maps box names to the names
    to send; headers are added to the request's.
    """
    names = names or {}
    extra = headers or {}
    if method == "GET":
        values = good_values(data)
        if not values:
            return None
        separator = "&" if "?" in url else "?"
        # spaces as %20: not every receiver decodes "+" as a space
        query = urlencode(
            [(names.get(name, name), text) for name, text in values.items()],
            quote_via=quote,
        )
        return ApiRequest("GET", url + separator + query, dict(extra), None)

    if encoding == ENCODING_JSON_FULL and not names:
        # exactly the results' dicts (a text may be null)
        body = json.dumps([result.to_dict() for result in data]).encode("utf-8")
        content = (body, "application/json")
    else:
        rows = [
            (
                names.get(r.name, r.name),
                r.result,
                r.result_state.name,
                r.x(),
                r.y(),
                r.width(),
                r.height(),
            )
            for r in data
        ]
        values = {names.get(k, k): v for k, v in good_values(data).items()}
        content = _body(rows, values, encoding)
        if content is None:
            return None
    body, content_type = content
    return ApiRequest(
        method, url, _with_headers({"Content-Type": content_type}, extra), body
    )


def build_field_request(fields: list[FieldItem]) -> ApiRequest:
    """The request for some fields (all built with the same FieldConfig)."""
    url, method, encoding, header_items = fields[0].config
    extra = dict(header_items)
    if has_placeholders(url):
        # one field per request (the worker sends them one by one)
        field = fields[0]
        url = url.replace(FIELD_PLACEHOLDER, quote(field.sent_name, safe="")).replace(
            VALUE_PLACEHOLDER, quote(field.text, safe="")
        )
        if method == "GET":
            return ApiRequest("GET", url, extra, None)
    elif method == "GET":
        separator = "&" if "?" in url else "?"
        query = urlencode([(f.sent_name, f.text) for f in fields], quote_via=quote)
        return ApiRequest("GET", url + separator + query, extra, None)
    rows = [(f.sent_name, f.text, f.state, *f.rect) for f in fields]
    values = {f.sent_name: f.text for f in fields}
    body, content_type = _body(rows, values, encoding)
    return ApiRequest(
        method, url, _with_headers({"Content-Type": content_type}, extra), body
    )


# --- what was sent (for "Only changes") ------------------------------------------

_sent_lock = threading.Lock()
# the text of each field that was last sent (alone, or in a cycle's request),
# by box name
_sent_fields: dict[str, str] = {}
# incremented by _forget_sent(): a request queued before (e.g. for the URL there
# was before a settings change) that arrives afterwards isn't recorded as sent
_sent_epoch = 0
# when "Resend all" last made every value count as changed (time.monotonic())
_last_refresh_at: float | None = None


def _forget_sent():
    """Everything counts as changed: it is sent again."""
    global _sent_epoch
    with _sent_lock:
        _sent_fields.clear()
        _sent_epoch += 1


def _record_sent(values: Iterable[tuple[str, str]], epoch: int) -> None:
    """Remember values as sent, if what was sent wasn't forgotten meanwhile."""
    with _sent_lock:
        if epoch == _sent_epoch:
            _sent_fields.update(values)


def _refresh_if_due(now: float) -> None:
    """With "Resend all", forget what was sent once the interval is over."""
    global _last_refresh_at
    if not (out_api_only_changes and out_api_refresh_s > 0):
        return
    if _last_refresh_at is not None and now - _last_refresh_at < out_api_refresh_s:
        return
    _last_refresh_at = now
    _forget_sent()


# --- the worker thread ---------------------------------------------------------------

_session = requests.Session()  # used only by the worker thread
# Digest logins, by (scheme, address, user, password), for the endpoints that
# asked for Digest (used only by the worker thread)
_digest_auths: dict[tuple[str, str, str, str], HTTPDigestAuth] = {}


def _login_key(url: str) -> tuple[str, str, str, str] | None:
    try:
        parts = urlsplit(url)
        if parts.username is None:
            return None
        address = parts.netloc.rpartition("@")[2]
        return (
            parts.scheme,
            address,
            unquote(parts.username),
            unquote(parts.password or ""),
        )
    except ValueError:
        return None


def _asks_for_digest(response) -> bool:
    headers = getattr(response, "headers", None) or {}
    return "digest" in str(headers.get("WWW-Authenticate", "")).lower()


def _send_request(request: ApiRequest) -> None:
    kwargs = {"headers": request.headers, "data": request.body, "timeout": HTTP_TIMEOUT}
    key = _login_key(request.url)
    auth = _digest_auths.get(key) if key is not None else None
    try:
        if auth is not None:
            response = _session.request(
                request.method, request.url, auth=auth, **kwargs
            )
        else:
            # a login in the URL is sent with HTTP Basic auth
            response = _session.request(request.method, request.url, **kwargs)
            if response.status_code == 401 and key is not None:
                if _asks_for_digest(response):
                    # the endpoint wants Digest (e.g. a TriCaster): from now on
                    auth = _digest_auths[key] = HTTPDigestAuth(key[2], key[3])
                    response = _session.request(
                        request.method, request.url, auth=auth, **kwargs
                    )
    except (requests.ConnectionError, requests.Timeout):
        # the receiver may restart meanwhile (an Epiphan Pearl forgets its
        # variables): send every value again once it answers
        _forget_sent()
        raise
    shown = display_url(request.url)
    check_http_response(response, f"{request.method} {shown}")
    logger.debug("Output API: %s %s sent", request.method, shown)


def _send_chunk(chunk: list[tuple[object, object]]) -> None:
    fields: list[FieldItem] = []
    for _key, item in chunk:
        if isinstance(item, SnapshotItem):
            _send_request(item.request)
            _record_sent(item.values, item.epoch)
        elif isinstance(item, FieldItem):
            fields.append(item)
    # (the fields of a chunk share their settings: a change clears the queue)
    for config in dict.fromkeys(field.config for field in fields):
        batch = [field for field in fields if field.config == config]
        _send_request(build_field_request(batch))
        for field in batch:
            _record_sent([(field.name, field.text)], field.epoch)


_worker = OutputWorker(
    "Output API",
    send_many=_send_chunk,
    retry_on=(requests.ConnectionError, requests.Timeout),
)
_apply_pacing()
# settings problems are reported on every cycle; log them at most every 30 s
_settings_log = ThrottledErrorLog("Output API")
_unknown_encodings_reported: set[str] = set()


def update_out_api(data: list[TextDetectionTargetWithResult]):
    """Send this cycle's readings to the configured URL in the background."""
    url, method, encoding = out_api_url, out_api_method, out_api_encoding
    if not url:
        _settings_log.error("no URL set")
        return
    # 光为 OCR: never hit a preset placeholder (avoids DNS error spam)
    try:
        _host = (urlsplit(str(url)).hostname or "").upper()
    except ValueError:
        _host = ""
    if _host in PLACEHOLDER_HOSTS:
        return
    if not is_valid_url_urllib(url):
        _settings_log.error(f"Invalid URL: {display_url(str(url))}")
        return
    if method not in METHODS:
        _settings_log.error(f"Invalid method: {method}")
        return
    if method != "GET" and encoding not in ENCODINGS:
        if encoding not in _unknown_encodings_reported:
            _unknown_encodings_reported.add(encoding)
            logger.warning(
                "Output API: unknown encoding %r, sending %s",
                encoding,
                ENCODING_JSON_FULL,
            )
        encoding = ENCODING_JSON_FULL
    headers, invalid = parse_headers(out_api_headers)
    if invalid:
        # (never the lines themselves: they may hold a token)
        _settings_log.error(
            f"{invalid} header line(s) are not “Name: value” and are not sent"
        )
    names = (
        sent_names([r.name for r in data], safe=True) if out_api_safe_names else None
    )
    _refresh_if_due(time.monotonic())

    if per_field_mode(url, out_api_fields_per_request):
        config = FieldConfig(url, method, encoding, tuple(headers.items()))
        with _sent_lock:
            sent = dict(_sent_fields)
            epoch = _sent_epoch
        for r in data:
            if r.result_state not in GOOD_STATES:
                continue
            text = "" if r.result is None else str(r.result)
            if out_api_only_changes and sent.get(r.name) == text:
                continue
            item = FieldItem(
                name=r.name,
                sent_name=names.get(r.name, r.name) if names else r.name,
                text=text,
                state=r.result_state.name,
                rect=(r.x(), r.y(), r.width(), r.height()),
                config=config,
                epoch=epoch,
            )
            _worker.submit(r.name, item)
        return

    request = build_request(data, url, method, encoding, names=names, headers=headers)
    if request is None:
        logger.debug("Output API: no good readings to send")
        return
    values = tuple(
        (name, "" if text is None else str(text))
        for name, text in good_values(data).items()
    )
    with _sent_lock:
        changed = any(_sent_fields.get(name) != text for name, text in values)
        epoch = _sent_epoch
    # (a reading that is rejected for a moment isn't a change)
    if out_api_only_changes and not changed:
        return
    _worker.submit("request", SnapshotItem(request, values, epoch))

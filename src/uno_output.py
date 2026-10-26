"""overlays.uno output: sends the new values of mapped fields to a UNO control
app's API.

update_uno() is called on the GUI thread. It only queues the commands; a
background OutputWorker sends them (latest value per command wins) through one
HTTPS session with timeouts, so a slow or unreachable UNO never blocks the UI.

overlays.uno counts every call against the plan's limits (the Free plan: about
10,000 a day and 100 a minute; the sources disagree, so they are read from the
X-Singular-Ratelimit-* response headers when present). Only fields with a
command are sent, the calls are spaced out to stay under the per-minute limit
the headers announce, and on HTTP 429 (Too Many Requests) the latest value of
each field is kept and sent once the limit resets, instead of being dropped.
"""

import datetime
import json
import threading
import time
from collections.abc import Container, Mapping
from dataclasses import dataclass

import requests

from output_worker import (
    HTTP_TIMEOUT,
    MAX_RETRY_AFTER,
    OutputWorker,
    RetryLater,
    check_http_response,
)
from text_detection_target import TextDetectionTargetWithResult
from sc_logging import logger
from storage import subscribe_to_data, fetch_data, unsubscribe_from_data

BURST_CALLS_HEADER = "X-Singular-Ratelimit-Burst-Calls"
DAILY_CALLS_HEADER = "X-Singular-Ratelimit-Daily-Calls"
# the window of the "burst" limit (the plans give it as calls per minute)
BURST_WINDOW_S = 60.0
# the daily calls left that make the status warn, as a share of the limit
LOW_DAILY_SHARE = 0.1


@dataclass(frozen=True)
class RateWindow:
    """One of overlays.uno's rate limits, as a response header reported it."""

    limit: int | None = None
    remaining: int | None = None
    reset_at: float | None = None  # time.time() when the window starts over

    def reset_in(self, now: float | None = None) -> float | None:
        if self.reset_at is None:
            return None
        now = time.time() if now is None else now
        return max(0.0, self.reset_at - now)


@dataclass(frozen=True)
class UnoQuota:
    """overlays.uno's limits, from the latest response that reported them."""

    burst: RateWindow | None = None
    daily: RateWindow | None = None

    @property
    def daily_low(self) -> bool:
        """Few daily calls are left (or none)."""
        daily = self.daily
        if daily is None or daily.remaining is None:
            return False
        if daily.limit:
            return daily.remaining <= daily.limit * LOW_DAILY_SHARE
        return daily.remaining <= 0


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _reset_time(value, now: float) -> float | None:
    """A reset given as epoch seconds or milliseconds, seconds from now, or an
    ISO 8601 date, as epoch seconds."""
    number = _number(value)
    if number is not None:
        if number > 1e12:  # epoch milliseconds
            return number / 1000.0
        if number > 1e9:  # epoch seconds
            return number
        return now + max(0.0, number)  # seconds from now
    if isinstance(value, str):
        try:
            when = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        return when.timestamp()
    return None


def parse_rate_window(value, now: float | None = None) -> RateWindow | None:
    """An X-Singular-Ratelimit-* header (a JSON object such as
    {"limit": 100, "remaining": 97, "reset": 1728600000}), or None if it can't
    be read. The exact format isn't documented: common key names are
    accepted, and "remaining" is computed from a used count if needed."""
    if value is None:
        return None
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return None
    if not isinstance(data, Mapping):
        return None
    fields = {
        str(key).lower().replace("_", "").replace("-", ""): v for key, v in data.items()
    }

    def first(*names):
        for name in names:
            if name in fields:
                return fields[name]
        return None

    now = time.time() if now is None else now
    limit = _number(first("limit", "max", "quota", "total"))
    remaining = _number(first("remaining", "left", "available"))
    used = _number(first("used", "current", "count", "calls"))
    if remaining is None and limit is not None and used is not None:
        remaining = max(0.0, limit - used)
    reset_value = first(
        "reset", "resetat", "resettime", "resetsat", "resetin", "resetafter"
    )
    reset_at = None if reset_value is None else _reset_time(reset_value, now)
    if limit is None and remaining is None and reset_at is None:
        return None
    return RateWindow(
        limit=None if limit is None else int(limit),
        remaining=None if remaining is None else int(remaining),
        reset_at=reset_at,
    )


class UNOAPI:
    def __init__(self, endpoint, field_mapping):
        self.endpoint = endpoint
        self.field_mapping = field_mapping
        self.running = False
        self.update_same = fetch_data("scoresight.json", "uno_send_same", False)
        subscribe_to_data("scoresight.json", "uno_send_same", self.set_update_same)
        self.essentials = fetch_data("scoresight.json", "uno_essentials", False)
        subscribe_to_data("scoresight.json", "uno_essentials", self.set_essentials)
        self.uno_essentials_id = fetch_data("scoresight.json", "uno_essentials_id", "")
        subscribe_to_data(
            "scoresight.json", "uno_essentials_id", self.set_uno_essentials_id
        )
        self._session = requests.Session()  # used only by the worker thread
        # the limits the last response reported; written by the worker thread
        self._quota = UnoQuota()
        self._quota_lock = threading.Lock()
        self._worker = OutputWorker(
            "UNO",
            self._send_payload,
            retry_on=(requests.ConnectionError, requests.Timeout),
        )

    def set_update_same(self, update_same):
        self.update_same = update_same

    def set_essentials(self, essentials):
        self.essentials = essentials

    def set_uno_essentials_id(self, uno_essentials_id):
        self.uno_essentials_id = uno_essentials_id

    def set_field_mapping(self, field_mapping):
        logger.debug(f"Setting UNO field mapping: {field_mapping}")
        self.field_mapping = field_mapping

    def set_endpoint(self, endpoint):
        """Send to another API URL from now on. The field mapping and the running
        state are kept; commands not sent yet go to the new URL."""
        if endpoint == self.endpoint:
            return
        self.endpoint = endpoint
        # the status (and the errors logged) are about the new URL, which may
        # be another account with other limits
        self._set_quota(UnoQuota())
        self._worker.reset_status()

    def update_uno(
        self,
        detection: list[TextDetectionTargetWithResult],
        resend_unchanged: Container[str] = (),
    ):
        """Queue the new values of the mapped fields. Never blocks.
        The boxes named in resend_unchanged also send an unchanged value (as
        "Also resend unchanged values" does for every box), e.g. after the outputs were paused."""
        if not self.running:
            return

        if not self.field_mapping:
            logger.debug("Field mapping is not set")
            return

        for target in detection:
            send = target.result_state == (
                TextDetectionTargetWithResult.ResultState.Success
            ) or (
                target.result_state
                == TextDetectionTargetWithResult.ResultState.SameNoChange
                and (self.update_same or target.name in resend_unchanged)
            )
            if send and target.name in self.field_mapping:
                uno_command = self.field_mapping[target.name]
                if uno_command:
                    self.send_uno_command(uno_command, target.result)

    def payload_for(self, command, value) -> dict:
        if not self.essentials:
            return {"command": command, "value": value}
        return {
            "command": "SetOverlayContentField",
            "value": value,
            "fieldId": command,
            "id": self.uno_essentials_id,
        }

    def send_uno_command(self, command, value):
        """Queue command with value for sending in the background."""
        self._worker.submit(command, self.payload_for(command, value))

    def _send_payload(self, command, payload) -> None:
        # runs on the worker thread
        endpoint = self.endpoint
        response = self._session.put(endpoint, json=payload, timeout=HTTP_TIMEOUT)
        headers = getattr(response, "headers", None) or {}
        self.check_rate_limits(headers, endpoint)
        try:
            check_http_response(response, f"UNO {command}")
        except RetryLater as e:
            # keep the value (the worker does) and send it when the limit resets
            delay = e.delay
            if delay is None:
                delay = self._reset_delay()
            raise RetryLater(str(e), delay=delay) from None
        logger.debug(f"Successfully sent {command}: {payload['value']} to UNO API")

    def check_rate_limits(self, headers, endpoint=None):
        """Remember the limits the response reports, and space out the calls
        to stay under the per-minute limit. endpoint: the URL the response
        came from (if the URL changed since, its limits no longer apply)."""
        now = time.time()
        burst = parse_rate_window(headers.get(BURST_CALLS_HEADER), now)
        daily = parse_rate_window(headers.get(DAILY_CALLS_HEADER), now)
        if burst is None and daily is None:
            return
        logger.debug("UNO rate limits: burst %s, daily %s", burst, daily)
        with self._quota_lock:
            if endpoint is not None and endpoint != self.endpoint:
                return  # an answer from the URL there was before
            self._set_quota_locked(
                UnoQuota(
                    burst=burst or self._quota.burst, daily=daily or self._quota.daily
                )
            )

    def _set_quota(self, quota: UnoQuota) -> None:
        with self._quota_lock:
            self._set_quota_locked(quota)

    def _set_quota_locked(self, quota: UnoQuota) -> None:
        self._quota = quota
        burst = quota.burst
        if burst is not None and burst.limit:
            self._worker.min_interval = BURST_WINDOW_S / burst.limit
        else:
            self._worker.min_interval = 0.0

    def _reset_delay(self) -> float | None:
        """Seconds until a used-up limit starts over, from the last headers."""
        now = time.time()
        quota = self.quota()
        waits = [
            window.reset_in(now)
            for window in (quota.burst, quota.daily)
            if window is not None
            and window.remaining is not None
            and window.remaining <= 0
            and window.reset_at is not None
        ]
        if not waits:
            return None
        return min(max(waits), MAX_RETRY_AFTER)

    def quota(self) -> UnoQuota:
        """The limits the last response reported (thread-safe)."""
        with self._quota_lock:
            return self._quota

    def start(self):
        self.running = True

    def stop(self):
        self.running = False
        self._worker.clear()
        self._worker.reset_status()

    def status(self):
        """The sending's WorkerStatus (see output_worker)."""
        return self._worker.status()

    def close(self):
        """Stop sending and unsubscribe from settings changes."""
        self.stop()
        unsubscribe_from_data("scoresight.json", "uno_send_same", self.set_update_same)
        unsubscribe_from_data("scoresight.json", "uno_essentials", self.set_essentials)
        unsubscribe_from_data(
            "scoresight.json", "uno_essentials_id", self.set_uno_essentials_id
        )
        self._worker.stop()

"""The live values behind the overlay pages: each field's last good value and the
state of its latest reading, pushed to Server-Sent Events clients (/events) as
they change and returned whole to polling clients (/overlay/state).

publish() runs on the GUI thread (http_server.update_http_server calls it for
every OCR result) and never waits for a client: it compares the readings with
the last ones under a lock, and only when something changed does it encode one
"change" message (shared by every stream) and schedule one wake-up on the
server's event loop. The streams run on the server thread: each sends the
messages it hasn't sent yet, a full snapshot if it fell too far behind, and a
heartbeat when nothing changed for HEARTBEAT_S.

The wire format (one JSON object per SSE "data:" line):

    event: snapshot   {"version": 7, "fields": {"Home Score": {"value": "21", "state": "Success"}, ...}}
    event: change     {"version": 8, "fields": {"Time": {"value": "4:29", "state": "Success"}}, "removed": ["Old box"]}
    event: heartbeat  {"version": 8}

"value" is the field's last good reading (null until it had one); "state" is
the state of its latest reading (Success, SameNoChange, FailedFilter, Empty).
"removed" (only when not empty) lists the fields that are gone (deleted or
renamed boxes). Versions only increase while ScoreSight runs; they start over
when it restarts, so clients compare them for equality only.
"""

import asyncio
import itertools
import json
import threading
from collections import deque
from collections.abc import AsyncIterator, Iterable

# a stream that sent nothing for this long sends a heartbeat (clients that hear
# nothing for a few heartbeats reconnect)
HEARTBEAT_S = 5.0
# how long a disconnected EventSource waits before it reconnects
RETRY_MS = 2000
# change messages kept for streams that are behind; one further behind gets a
# snapshot instead
_BACKLOG = 256

# readings whose text is the field's value (as in http_server)
GOOD_STATES = frozenset({"Success", "SameNoChange"})


def sse_message(event: str, data: dict) -> bytes:
    """One Server-Sent Event. json.dumps escapes newlines, so the data is one
    line."""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _field_dict(value: str | None, state: str) -> dict:
    return {"value": value, "state": state}


class LiveFeed:
    """The fields' live values and the streams that follow them (thread-safe)."""

    def __init__(self):
        self._lock = threading.Lock()
        # name -> (last good value or None, state of the latest reading), in
        # the boxes' order
        self._fields: dict[str, tuple[str | None, str]] = {}
        self._version = 0
        # (version, encoded "change" message), consecutive versions
        self._backlog: deque[tuple[int, bytes]] = deque(maxlen=_BACKLOG)
        self._snapshot_cache: tuple[int, dict, bytes] | None = None
        # the server's event loop (bound by the first stream) and the event its
        # streams wait on; the event is only touched on that loop
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wakeup: asyncio.Event | None = None
        self._wake_scheduled = False
        # close_streams() bumps it: the streams started before end
        self._generation = 0
        self._streams = 0

    # --- the GUI thread's side ------------------------------------------------

    def publish(self, readings: Iterable) -> None:
        """Take the latest readings (objects with .name, .text and .state, a
        ResultState name). Cheap when nothing changed; never blocks on
        clients."""
        with self._lock:
            previous = self._fields
            fields: dict[str, tuple[str | None, str]] = {}
            for reading in readings:
                name = reading.name
                if reading.state in GOOD_STATES:
                    value = reading.text
                else:
                    # a rejected or empty reading keeps the last good value
                    old = previous.get(name)
                    value = old[0] if old is not None else None
                fields[name] = (value, reading.state)
            if fields == previous:
                return
            changed = {
                name: _field_dict(*field)
                for name, field in fields.items()
                if previous.get(name) != field
            }
            removed = [name for name in previous if name not in fields]
            self._fields = fields
            self._version += 1
            data = {"version": self._version, "fields": changed}
            if removed:
                data["removed"] = removed
            self._backlog.append((self._version, sse_message("change", data)))
            loop = self._loop
            wake = loop is not None and not self._wake_scheduled
            if wake:
                self._wake_scheduled = True
        if wake:
            self._schedule_wake(loop)

    def close_streams(self) -> None:
        """End the open streams (the server is stopping: it then doesn't wait
        for them)."""
        with self._lock:
            self._generation += 1
            loop = self._loop
        if loop is not None:
            self._schedule_wake(loop)

    def clear(self) -> None:
        """Forget every field (for tests)."""
        self.publish(())

    # --- the server thread's side ---------------------------------------------

    def snapshot(self) -> dict:
        """{"version": n, "fields": {name: {"value": ..., "state": ...}}}"""
        with self._lock:
            return self._snapshot_locked()[0]

    def open_streams(self) -> int:
        """How many /events streams are open."""
        with self._lock:
            return self._streams

    async def stream(self) -> AsyncIterator[bytes]:
        """The body of one /events response: the snapshot, then the changes,
        until the client disconnects (the server cancels the task) or
        close_streams() is called."""
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._loop is not loop:
                # the first stream since the server (re)started
                self._loop = loop
                self._wakeup = asyncio.Event()
                self._wake_scheduled = False
            generation = self._generation
            _, message = self._snapshot_locked()
            version = self._version
            self._streams += 1
        try:
            yield f"retry: {RETRY_MS}\n\n".encode() + message
            while True:
                with self._lock:
                    if self._generation != generation:
                        return
                    behind = self._version != version
                if not behind:
                    # nothing in between: a publish() after the check above
                    # schedules a wake-up that runs after this wait started
                    event = self._wakeup
                    try:
                        async with asyncio.timeout(HEARTBEAT_S):
                            await event.wait()
                    except TimeoutError:
                        yield sse_message("heartbeat", {"version": version})
                    continue
                with self._lock:
                    if self._generation != generation:
                        return
                    chunk = self._messages_since_locked(version)
                    version = self._version
                yield chunk
        finally:
            with self._lock:
                self._streams -= 1

    def _schedule_wake(self, loop: asyncio.AbstractEventLoop) -> None:
        try:
            loop.call_soon_threadsafe(self._wake_streams)
        except RuntimeError:
            # the loop is closed: the server stopped, no stream is waiting
            with self._lock:
                self._wake_scheduled = False

    def _wake_streams(self) -> None:
        """On the server's loop: wake every waiting stream."""
        with self._lock:
            self._wake_scheduled = False
        event, self._wakeup = self._wakeup, asyncio.Event()
        if event is not None:
            event.set()

    def _snapshot_locked(self) -> tuple[dict, bytes]:
        cached = self._snapshot_cache
        if cached is None or cached[0] != self._version:
            data = {
                "version": self._version,
                "fields": {
                    name: _field_dict(*field) for name, field in self._fields.items()
                },
            }
            cached = (self._version, data, sse_message("snapshot", data))
            self._snapshot_cache = cached
        return cached[1], cached[2]

    def _messages_since_locked(self, version: int) -> bytes:
        """The change messages after version, or a snapshot if some of them
        aren't kept any more."""
        if not self._backlog or self._backlog[0][0] > version + 1:
            return self._snapshot_locked()[1]
        start = version + 1 - self._backlog[0][0]
        return b"".join(
            message for _, message in itertools.islice(self._backlog, start, None)
        )


# the app's feed (http_server publishes to it, the overlay routes read it)
feed = LiveFeed()

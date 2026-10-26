"""Background senders for the network outputs (vMix, UNO, OBS, outbound HTTP API).

OCR results reach MainWindow.ocrResult on the GUI thread several times a second.
Network calls must never run there: a slow or unreachable endpoint would freeze
the UI, and the queued results would pile up behind it.

Each output owns an OutputWorker. Callers hand it (key, item) pairs with submit(),
which never blocks. A single daemon thread per worker sends them. Pending items
are coalesced per key: a newer item for a key replaces an older one that hasn't
been sent yet, so the backlog is bounded by the number of keys (e.g. mapped
fields) and the endpoint always receives the latest values. Keys let outputs
that only send changed values (vMix, UNO, OBS) keep every field's latest value,
while snapshot outputs (the outbound API) use a single key.

Items must be plain, immutable snapshots (strings, tuples): the worker thread
reads them after the caller has moved on.

status() returns a WorkerStatus: what was sent, what failed and whether the
endpoint is being retried. It is a snapshot taken under the worker's lock, so
the GUI can read it at any time (output_status.py turns it into what the
window shows).
"""

import datetime
import email.utils
import threading
import time
import weakref
from collections.abc import Callable, Hashable
from dataclasses import dataclass

from sc_logging import logger

# (connect, read) timeout in seconds for every HTTP request an output makes.
# Outputs send through one requests.Session each, used only by their worker
# thread, so connections (and TLS sessions) are reused between requests.
HTTP_TIMEOUT = (2.0, 2.0)


class ThrottledErrorLog:
    """Logs repeated errors at most once per interval, with the number of errors
    that were not logged in between, so a failing output doesn't flood the log
    on every detection cycle."""

    def __init__(self, label: str, interval: float = 30.0, clock=time.monotonic):
        self.label = label
        self.interval = interval
        self._clock = clock
        self._lock = threading.Lock()
        self._last_logged: float | None = None
        self._suppressed = 0

    def error(self, message: str) -> bool:
        """Log message as an error unless one was logged within the interval.
        Returns True if it was logged."""
        with self._lock:
            now = self._clock()
            if (
                self._last_logged is not None
                and now - self._last_logged < self.interval
            ):
                self._suppressed += 1
                return False
            suppressed, self._suppressed = self._suppressed, 0
            self._last_logged = now
        if suppressed:
            message += (
                f" ({suppressed} more error(s) since the last report were not logged)"
            )
        logger.error("%s: %s", self.label, message)
        return True

    def reset(self) -> None:
        """Forget the throttling state, so the next error is logged immediately."""
        with self._lock:
            self._last_logged = None
            self._suppressed = 0


class SendSkipped(Exception):
    """Raised by a send function for an item it deliberately didn't send (e.g.
    its connection was closed meanwhile): neither a success nor a failure."""


class OutputError(Exception):
    """The endpoint answered, but with an error (e.g. an HTTP error status)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# the longest wait a RetryLater may ask for: a wrong Retry-After never stops an
# output for long (an endpoint that still refuses answers 429 again)
MAX_RETRY_AFTER = 3600.0


class RetryLater(OutputError):
    """The endpoint asked to send again later (HTTP 429 Too Many Requests), or
    a known rate limit is used up. The item and the rest of the batch are kept
    for a retry (a newer item for the same key replaces its older one) and the
    worker waits delay seconds (its backoff if None) before sending again."""

    def __init__(
        self, message: str, delay: float | None = None, status_code: int | None = 429
    ):
        super().__init__(message, status_code)
        self.delay = delay


def retry_after_seconds(value, now: float | None = None) -> float | None:
    """The wait an HTTP Retry-After header asks for (seconds, or an HTTP date),
    or None if it is missing or can't be read."""
    if value is None:
        return None
    value = str(value).strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        now = time.time() if now is None else now
        seconds = when.timestamp() - now
    if seconds != seconds:  # NaN
        return None
    return min(max(0.0, seconds), MAX_RETRY_AFTER)


def check_http_response(response, request_description: str) -> None:
    """Raise OutputError unless response (a requests.Response) has a 2xx status;
    RetryLater for HTTP 429 (Too Many Requests), with its Retry-After."""
    if 200 <= response.status_code < 300:
        return
    body = " ".join((response.text or "").split())[:200]
    message = f"{request_description} returned HTTP {response.status_code}" + (
        f": {body}" if body else ""
    )
    if response.status_code == 429:
        headers = getattr(response, "headers", None) or {}
        raise RetryLater(message, delay=retry_after_seconds(headers.get("Retry-After")))
    raise OutputError(message, status_code=response.status_code)


@dataclass(frozen=True)
class ErrorInfo:
    """A failure, without the exception object (which would keep the frames of
    its traceback alive)."""

    type: type
    message: str
    # an HTTP status (OutputError) or another numeric code of the error (e.g.
    # the code of an OBS request error)
    code: int | None = None
    # the endpoint couldn't be reached (the item is kept for a retry)
    unreachable: bool = False

    @classmethod
    def of(cls, error: BaseException, unreachable: bool = False) -> "ErrorInfo":
        code = getattr(error, "status_code", None)
        if code is None:
            code = getattr(error, "code", None)
        if isinstance(code, bool) or not isinstance(code, int):
            code = None
        return cls(type(error), str(error), code, unreachable)

    def __str__(self) -> str:
        name = self.type.__name__
        return f"{name}: {self.message}" if self.message else name


@dataclass(frozen=True)
class WorkerStatus:
    """A snapshot of an OutputWorker (see OutputWorker.status())."""

    name: str
    pending: int = 0  # items waiting to be sent
    busy: bool = False  # a batch is being sent
    sent: int = 0  # items sent successfully
    failed: int = 0  # items that failed
    consecutive_failures: int = 0  # failures since the last success
    # the endpoint couldn't be reached, or asked to wait (RetryLater, e.g.
    # HTTP 429), and nothing was sent since: the unsent items are retried
    # every retry_delay seconds (last_error tells which)
    retrying: bool = False
    retry_delay: float | None = None
    # while retrying: the seconds left until the next try (None if unknown)
    retry_in: float | None = None
    # keys (e.g. fields) whose latest item was sent, or failed and was dropped
    ok_keys: int = 0
    failing_keys: int = 0
    # times (of the worker's clock, time.monotonic by default)
    last_success_at: float | None = None
    last_failure_at: float | None = None
    # the error behind the current problem: what makes the endpoint unreachable
    # while retrying, else the latest error of a failing key (None without a
    # problem)
    last_error: ErrorInfo | None = None


_workers: "weakref.WeakSet[OutputWorker]" = weakref.WeakSet()
_workers_lock = threading.Lock()


class OutputWorker:
    """Sends submitted items from one background daemon thread.

    send(key, item) is called on the worker thread and reports a failure by
    raising. Exceptions of a type in retry_on mean the endpoint is unreachable
    (connection refused, timeout): the item and the rest of the batch are kept
    for a retry (unless a newer item for the same key arrives first) and the
    worker waits with exponential backoff before trying again. RetryLater
    (e.g. HTTP 429) keeps them the same way, and waits as long as it asks. Any
    other exception drops that one item. Failures are logged through a
    ThrottledErrorLog; recovery from an unreachable endpoint is logged once.
    status() tells how the sending goes.

    With send_many instead of send, the worker hands it up to max_batch
    pending items at a time, as a list of (key, item) pairs (None: all of
    them), e.g. to send several fields in one request; a failure applies to
    every item of that call. min_interval (seconds, settable at any time)
    spaces out the calls, e.g. for an endpoint that accepts 5 requests/s.
    """

    def __init__(
        self,
        name: str,
        send: Callable[[Hashable, object], None] | None = None,
        *,
        send_many: Callable[[list[tuple[Hashable, object]]], None] | None = None,
        max_batch: int | None = 1,
        min_interval: float = 0.0,
        retry_on: tuple[type[BaseException], ...] = (),
        initial_backoff: float = 0.5,
        max_backoff: float = 5.0,
        error_log: ThrottledErrorLog | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        if (send is None) == (send_many is None):
            raise TypeError("OutputWorker needs either send or send_many")
        self.name = name
        if send_many is None:

            def send_many(chunk, send=send):
                ((key, item),) = chunk
                send(key, item)

            max_batch = 1
        self._send_many = send_many
        # read by the worker thread before each call; may be changed any time
        self.max_batch = max_batch
        self.min_interval = min_interval
        self._last_call_at: float | None = None  # time.monotonic()
        self._retry_on = retry_on
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self.error_log = error_log or ThrottledErrorLog(name)
        self._clock = clock
        self._cond = threading.Condition()
        self._pending: dict[Hashable, object] = {}
        self._thread: threading.Thread | None = None
        self._busy = False
        self._stopped = False
        # incremented by clear(): the rest of a batch being sent is dropped too
        self._generation = 0
        self._retry_failures = 0  # consecutive "unreachable" failures
        # the wait the last RetryLater asked for (None: the backoff applies)
        self._retry_after: float | None = None
        self._throttled = False  # the retries are because of a RetryLater
        # when the retry being waited for happens (time.monotonic())
        self._retry_at: float | None = None
        # for status(), written with the lock held. reset_status() increments
        # the epoch: the result of a request that was in flight then (e.g. to
        # the previous vMix host) doesn't count.
        self._status_epoch = 0
        self._sent = 0
        self._failed = 0
        self._consecutive_failures = 0
        self._key_ok: dict[Hashable, bool] = {}  # latest result per key
        # the error of each key whose latest item was dropped, with its time
        self._key_errors: dict[Hashable, tuple[float, ErrorInfo]] = {}
        # the error that made the endpoint unreachable (until a success)
        self._unreachable_error: ErrorInfo | None = None
        self._unreachable_key: Hashable | None = None
        self._last_success_at: float | None = None
        self._last_failure_at: float | None = None
        with _workers_lock:
            _workers.add(self)

    def submit(self, key: Hashable, item: object) -> None:
        """Queue item for key, replacing an unsent item for the same key.
        Never blocks on the network. Ignored after stop()."""
        with self._cond:
            if self._stopped:
                return
            self._pending[key] = item
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name=f"output-{self.name}", daemon=True
                )
                self._thread.start()
            self._cond.notify_all()

    def clear(self) -> None:
        """Drop the items that haven't been sent yet (a request already in
        flight still completes)."""
        with self._cond:
            self._pending.clear()
            self._generation += 1
            self._cond.notify_all()

    def pending(self) -> dict[Hashable, object]:
        """A copy of the items waiting to be sent."""
        with self._cond:
            return dict(self._pending)

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Wait until nothing is pending or being sent. Returns False on timeout."""
        with self._cond:
            return self._cond.wait_for(
                lambda: self._stopped or (not self._pending and not self._busy),
                timeout,
            )

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> WorkerStatus:
        """A snapshot of what was sent and what failed (thread-safe, never
        blocks on the network)."""
        with self._cond:
            retrying = bool(self._retry_failures and self._unreachable_error)
            if retrying:
                error = self._unreachable_error
            elif self._key_errors:
                # the most recent error of a key whose latest item was dropped
                _, error = max(self._key_errors.values(), key=lambda entry: entry[0])
            else:
                error = None
            retry_in = None
            if retrying and self._retry_at is not None:
                retry_in = max(0.0, self._retry_at - time.monotonic())
            return WorkerStatus(
                name=self.name,
                pending=len(self._pending),
                busy=self._busy,
                sent=self._sent,
                failed=self._failed,
                consecutive_failures=self._consecutive_failures,
                retrying=retrying,
                retry_delay=self._backoff_delay() if retrying else None,
                retry_in=retry_in,
                ok_keys=sum(1 for ok in self._key_ok.values() if ok),
                failing_keys=sum(1 for ok in self._key_ok.values() if not ok),
                last_success_at=self._last_success_at,
                last_failure_at=self._last_failure_at,
                last_error=error,
            )

    def reset_status(self) -> None:
        """Forget what was sent and what failed, e.g. when the output is turned
        off or sends somewhere else. A retry waiting out its backoff is tried
        right away, and the next error is logged right away."""
        with self._cond:
            self._status_epoch += 1
            self._sent = self._failed = self._consecutive_failures = 0
            self._retry_failures = 0
            self._retry_after = None
            self._throttled = False
            self._key_ok.clear()
            self._key_errors.clear()
            self._unreachable_error = self._unreachable_key = None
            self._last_success_at = self._last_failure_at = None
            self._cond.notify_all()
        self.error_log.reset()

    def forget_keys(self, predicate: Callable[[Hashable], bool]) -> None:
        """Forget the results of the keys for which predicate(key) is true, e.g.
        the text sources of an OBS connection that was closed."""
        with self._cond:
            for key in [key for key in self._key_ok if predicate(key)]:
                del self._key_ok[key]
                self._key_errors.pop(key, None)
            if self._unreachable_key is not None and predicate(self._unreachable_key):
                self._unreachable_error = self._unreachable_key = None
                self._consecutive_failures = 0
                self._retry_failures = 0

    def stop(self, timeout: float = 2.0) -> bool:
        """Drop pending items and stop the thread, waiting up to timeout seconds
        for a request in flight to finish. Returns True if the thread ended."""
        with self._cond:
            self._stopped = True
            self._pending.clear()
            self._generation += 1
            self._cond.notify_all()
            thread = self._thread
        if thread is None or thread is threading.current_thread():
            return True
        thread.join(timeout)
        if thread.is_alive():
            logger.warning(
                "%s: output thread still busy after %.1fs; leaving it (daemon)",
                self.name,
                timeout,
            )
            return False
        return True

    # --- worker thread ----------------------------------------------------

    def _run(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._stopped or self._pending)
                if self._stopped:
                    return
                batch, self._pending = self._pending, {}
                generation = self._generation
                epoch = self._status_epoch
                self._busy = True
            try:
                retry = self._send_batch(list(batch.items()), generation)
            except Exception:  # never let the thread die
                logger.exception("%s: unexpected error in output thread", self.name)
                retry = False
            finally:
                with self._cond:
                    self._busy = False
                    self._cond.notify_all()
            if retry:
                with self._cond:
                    delay = self._backoff_delay()
                    self._retry_at = time.monotonic() + delay
                    # cleared, or sending somewhere else (reset_status): don't
                    # wait out the backoff of the old items or target
                    self._cond.wait_for(
                        lambda: (
                            self._stopped
                            or self._generation != generation
                            or self._status_epoch != epoch
                        ),
                        delay,
                    )
                    self._retry_at = None

    def _backoff_delay(self) -> float:
        """The wait before retrying: what the last RetryLater asked for, else
        an exponential backoff after self._retry_failures consecutive
        "unreachable" failures."""
        # cap the exponent: the failures go on for as long as the endpoint is
        # down, and 2 ** 1024 doesn't fit in a float (OverflowError would end
        # the thread, and with it the output, for the rest of the session)
        exponent = min(self._retry_failures - 1, 32)
        backoff = min(self._initial_backoff * 2**exponent, self._max_backoff)
        if self._retry_after is not None:
            # never less than the backoff: "Retry-After: 0", or a date that is
            # past by this computer's clock, mustn't make a busy loop
            return max(self._retry_after, backoff)
        return backoff

    def _chunks(self, items: list[tuple[Hashable, object]]):
        """items split into the lists handed to one send_many call each."""
        index = 0
        while index < len(items):
            size = self.max_batch or len(items)
            yield index, items[index : index + max(1, size)]
            index += max(1, size)

    def _pace(self, generation: int) -> bool:
        """Wait until min_interval has passed since the last call. Returns False
        if the worker was cleared or stopped meanwhile."""
        interval = self.min_interval
        if interval and interval > 0 and self._last_call_at is not None:
            wait = self._last_call_at + interval - time.monotonic()
            if wait > 0:
                with self._cond:
                    epoch = self._status_epoch
                    # (reset_status: sending somewhere else, without the old
                    # target's pacing)
                    self._cond.wait_for(
                        lambda: (
                            self._stopped
                            or self._generation != generation
                            or self._status_epoch != epoch
                        ),
                        wait,
                    )
        if self._stopped or self._generation != generation:
            return False
        self._last_call_at = time.monotonic()
        return True

    def _send_batch(
        self, items: list[tuple[Hashable, object]], generation: int
    ) -> bool:
        """Send items in order. Returns True if they must be retried later (the
        endpoint was unreachable or asked to wait: the unsent items were put
        back)."""
        for index, chunk in self._chunks(items):
            if self._generation != generation:  # cleared or stopped meanwhile
                return False
            if not self._pace(generation):
                return False
            epoch = self._status_epoch
            keys = [key for key, _ in chunk]
            try:
                self._send_many(chunk)
            except SendSkipped:
                continue
            except RetryLater as e:
                self._record_failure(
                    keys, e, generation, epoch, unreachable=True, retry_after=e.delay
                )
                self.error_log.error(_describe(e))
                self._put_back(items[index:], generation)
                return True
            except self._retry_on as e:
                self._record_failure(keys, e, generation, epoch, unreachable=True)
                self.error_log.error(_describe(e))
                self._put_back(items[index:], generation)
                return True
            except Exception as e:
                self._record_failure(keys, e, generation, epoch, unreachable=False)
                self.error_log.error(_describe(e))
            else:
                restored = self._record_success(keys, generation, epoch)
                if restored == "unreachable":
                    logger.info("%s: connection restored", self.name)
                    self.error_log.reset()
                elif restored == "throttled":
                    logger.info("%s: sending again after the rate limit", self.name)
                    self.error_log.reset()
        return False

    def _put_back(self, items: list[tuple[Hashable, object]], generation: int):
        with self._cond:
            if self._generation == generation:
                # retry the unsent items first; a newer item submitted
                # meanwhile for the same key replaces the old one
                retry = dict(items)
                retry.update(self._pending)
                self._pending = retry

    def _counts(self, generation: int, epoch: int) -> bool:
        """Whether the result of a request started at (generation, epoch) is
        about the current target. Call with the lock held."""
        # cleared meanwhile (e.g. the output was turned off), or the status was
        # reset (e.g. another host): a request that was in flight doesn't count
        return self._generation == generation and self._status_epoch == epoch

    def _record_failure(
        self,
        keys: list[Hashable],
        error: Exception,
        generation: int,
        epoch: int,
        unreachable: bool,
        retry_after: float | None = None,
    ) -> None:
        with self._cond:
            if unreachable:
                # (also when it doesn't count: the wait is interrupted then)
                self._retry_after = (
                    None if retry_after is None else min(retry_after, MAX_RETRY_AFTER)
                )
            if not self._counts(generation, epoch):
                return
            now = self._clock()
            info = ErrorInfo.of(error, unreachable)
            self._failed += len(keys)
            self._consecutive_failures += len(keys)
            self._last_failure_at = now
            if unreachable:
                # kept for a retry: the keys' results aren't known yet
                self._retry_failures += 1
                self._throttled = isinstance(error, RetryLater)
                self._unreachable_error, self._unreachable_key = info, keys[0]
            else:
                # dropped: the keys' latest values didn't arrive
                for key in keys:
                    self._key_ok[key] = False
                    self._key_errors[key] = (now, info)

    def _record_success(
        self, keys: list[Hashable], generation: int, epoch: int
    ) -> str | None:
        """Returns "unreachable" or "throttled" if the items were being retried
        until now (the endpoint was unreachable, or asked to wait)."""
        with self._cond:
            restored = None
            if self._retry_failures > 0:
                restored = "throttled" if self._throttled else "unreachable"
            self._retry_failures = 0
            self._retry_after = None
            self._throttled = False
            if self._counts(generation, epoch):
                self._unreachable_error = self._unreachable_key = None
                self._sent += len(keys)
                self._consecutive_failures = 0
                for key in keys:
                    self._key_ok[key] = True
                    self._key_errors.pop(key, None)
                self._last_success_at = self._clock()
            return restored


def _describe(e: BaseException) -> str:
    text = str(e)
    return f"{type(e).__name__}: {text}" if text else type(e).__name__


def stop_all_workers(timeout: float = 2.0) -> bool:
    """Stop every OutputWorker, sharing one timeout. Returns True if all ended."""
    with _workers_lock:
        workers = list(_workers)
    deadline = time.monotonic() + timeout
    all_stopped = True
    for worker in workers:
        remaining = max(0.0, deadline - time.monotonic())
        all_stopped = worker.stop(remaining) and all_stopped
    return all_stopped

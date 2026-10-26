"""Startup timing: how long ScoreSight took to start, phase by phase.

main.py imports this module before anything else, so the time it is imported is
"main.py started". Startup milestones are marked as they are reached (mark(),
from any thread). Once the app is ready, main.py calls finish(), which logs one
line to the normal log, e.g.

    Startup: before main.py 0.41 s, arguments 0.01 s, imports 1.23 s,
    QApplication 0.10 s, main window 0.80 s, show 0.05 s, first event loop pass
    0.02 s; total 2.62 s; HTTP server ready at 1.15 s, video sources discovered
    at 2.40 s

"before main.py" is the time from the creation of the process (the frozen
app's bootloader, the Python interpreter) to main.py; the "total" and "at"
times count from the creation of the process when it is known (else from
main.py). With SCORESIGHT_STARTUP_REPORT=<path> set, finish() also writes the
milestones to that path as JSON.

Kept dependency-free and cheap to import (no Qt, no app modules). Nothing here
raises: timing must never break the startup it measures.
"""

import json
import os
import sys
import threading
import time

REPORT_ENV = "SCORESIGHT_STARTUP_REPORT"

_t0 = time.perf_counter()
_t0_wall = time.time()

# The startup phases in order, as (milestone, label): each phase ends at its
# milestone and starts at the previous one that was reached.
PHASES = (
    ("args_parsed", "arguments"),
    ("modules_imported", "imports"),
    ("qapplication_created", "QApplication"),
    ("main_window_created", "main window"),
    ("window_shown", "show"),
    ("event_loop_idle", "first event loop pass"),
)
# The milestone at which the app is ready (the "total").
READY = "event_loop_idle"
# Startup work that finishes in the background, reported by when it was done.
BACKGROUND = (
    ("http_server_ready", "HTTP server ready"),
    ("sources_discovered", "video sources discovered"),
)

_lock = threading.Lock()
# {milestone: seconds since main.py started}
_milestones: dict[str, float] = {}
_finished = False


def mark(name: str) -> None:
    """Record that startup reached the milestone name. Only the first time
    counts (e.g. the first source discovery, not a later refresh)."""
    try:
        seconds = time.perf_counter() - _t0
        with _lock:
            if name in _milestones:
                return
            _milestones[name] = seconds
            late = _finished
        if late:
            # reached after the summary was logged (e.g. a slow source discovery)
            label = dict(BACKGROUND).get(name, name)
            _log(f"Startup: {label} at {seconds + (_before_main() or 0.0):.2f} s")
    except Exception:
        pass


def elapsed(name: str) -> float | None:
    """Seconds from main.py's start to the milestone, or None if not reached."""
    with _lock:
        return _milestones.get(name)


def background_done() -> bool:
    """Whether every BACKGROUND milestone was reached."""
    with _lock:
        return all(name in _milestones for name, _ in BACKGROUND)


def _process_age_now() -> float | None:
    """Seconds since this process was created, or None if unknown."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
            ctypes.POINTER(wintypes.FILETIME)
        ] * 4
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(
            kernel32.GetCurrentProcess(), *(ctypes.byref(t) for t in times)
        ):
            return None
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        # FILETIME: 100 ns intervals since 1601-01-01 (UTC)
        return time.time() - (created / 1e7 - 11644473600)
    if sys.platform.startswith("linux"):
        with open("/proc/self/stat", "rb") as f:
            stat = f.read()
        # the fields after the command name, which may contain spaces and ")"
        fields = stat[stat.rindex(b")") + 2 :].split()
        start_ticks = int(fields[19])  # field 22: starttime, in ticks since boot
        since_boot = time.clock_gettime(time.CLOCK_BOOTTIME)
        return since_boot - start_ticks / os.sysconf("SC_CLK_TCK")
    if sys.platform == "darwin":
        import ctypes
        import ctypes.util
        import struct

        libc = ctypes.CDLL(ctypes.util.find_library("c"))
        libc.sysctl.argtypes = [
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        libc.sysctl.restype = ctypes.c_int
        # sysctl {CTL_KERN, KERN_PROC, KERN_PROC_PID, pid} -> struct kinfo_proc
        mib = (ctypes.c_int * 4)(1, 14, 1, os.getpid())
        buffer = ctypes.create_string_buffer(1024)
        size = ctypes.c_size_t(len(buffer))
        if libc.sysctl(mib, 4, buffer, ctypes.byref(size), None, 0) != 0:
            return None
        # kinfo_proc starts with kp_proc.p_starttime, a struct timeval
        seconds, microseconds = struct.unpack_from("@qi", buffer.raw, 0)
        return time.time() - (seconds + microseconds / 1e6)
    return None


_before_main_cache: list[float | None] = []


def _before_main() -> float | None:
    """Seconds from the creation of the process to main.py's start, or None."""
    if not _before_main_cache:
        try:
            age = _process_age_now()
            value = None if age is None else age - (time.perf_counter() - _t0)
            # a clock change or an odd platform could give nonsense
            if value is not None and not 0 <= value < 3600:
                value = None
        except Exception:
            value = None
        _before_main_cache.append(value)
    return _before_main_cache[0]


def _seconds(value: float) -> str:
    return f"{value:.2f} s"


def report() -> dict:
    """The startup milestones and phases (seconds), as written to the JSON
    report. "milestones" count from main.py's start; "total" and "ready_at"
    from the creation of the process when "before_main" is known."""
    with _lock:
        milestones = dict(_milestones)
    before_main = _before_main()
    offset = before_main or 0.0
    phases = {}
    previous = 0.0
    for name, label in PHASES:
        if name in milestones:
            phases[label] = milestones[name] - previous
            previous = milestones[name]
    ready = milestones.get(READY)
    try:
        from version import __version__ as version
    except Exception:
        version = None
    return {
        "version": version,
        "pid": os.getpid(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "platform": sys.platform,
        "main_started_at": _t0_wall,
        "before_main": before_main,
        "milestones": milestones,
        "phases": phases,
        "total": None if ready is None else ready + offset,
        "ready_at": {
            label: milestones[name] + offset
            for name, label in BACKGROUND
            if name in milestones
        },
    }


def summary_line(data: dict | None = None) -> str:
    """The one-line summary that is logged, e.g. "Startup: before main.py
    0.41 s, imports 1.23 s, ...; total 2.62 s; HTTP server ready at 1.15 s"."""
    data = report() if data is None else data
    parts = []
    if data.get("before_main") is not None:
        parts.append(f"before main.py {_seconds(data['before_main'])}")
    parts += [f"{label} {_seconds(value)}" for label, value in data["phases"].items()]
    line = "Startup: " + ", ".join(parts)
    if data.get("total") is not None:
        line += f"; total {_seconds(data['total'])}"
    background = [
        f"{label} at {_seconds(data['ready_at'][label])}"
        if label in data["ready_at"]
        else f"{label}: not yet"
        for _, label in BACKGROUND
    ]
    return line + "; " + ", ".join(background)


def write_report(path: str, data: dict | None = None) -> bool:
    """Write the report as JSON to path (atomically: readers never see a
    partial file). Returns False if it couldn't be written."""
    try:
        data = report() if data is None else data
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        tmp_path = f"{path}.{os.getpid()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
        return True
    except Exception:
        return False


def finish() -> None:
    """Log the summary line and, with SCORESIGHT_STARTUP_REPORT set, write the
    JSON report. Only the first call does anything."""
    global _finished
    try:
        with _lock:
            if _finished:
                return
            _finished = True
        data = report()
        _log(summary_line(data))
        path = os.environ.get(REPORT_ENV)
        if path and not write_report(path, data):
            _log(f"Startup: could not write the startup report to {path}")
    except Exception:
        pass


def _log(message: str) -> None:
    # imported here: this module is imported before the app's logging is set up
    from sc_logging import logger

    logger.info(message)

"""Run the ScoreSight app as a child process, for scripts/ci/e2e_test.py and
scripts/ci/boot_benchmark.py.

- app_env(): throw-away settings and log folders, headless (offscreen) Qt.
- AppProcess: starts the app in a process group of its own, stops it the way a
  user would (SIGTERM on macOS and Linux, Ctrl+Break on Windows, both of which
  the app handles by closing its window and shutting down normally) and kills
  the whole process tree if it doesn't stop in time.
- http_get() / wait_for_http(): readiness probes on the app's HTTP server.
- print_diagnostics(): the app's output and log files, for a failed run.
- append_step_summary(): Markdown for the GitHub Actions job summary.

The windowed Windows build has no console of its own; the app attaches to the
console of the process that started it when it runs offscreen (see
attach_console_for_quit_signals in src/main.py), so Ctrl+Break reaches it too.
"""

import http.client
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

LOCALHOST = "127.0.0.1"
# The 0.1.x app always served on this port (it has no --port option).
LEGACY_HTTP_PORT = 18099
# A connection attempt to a port nobody listens on takes about a second to fail
# on Windows (it retries the SYN), which would make the readiness probe
# coarse: give up on it sooner and try again. (Connecting to a listening
# localhost port takes well under a millisecond.)
CONNECT_TIMEOUT = 0.1
POLL_INTERVAL = 0.02


def resolve_command(command: list[str]) -> list[str]:
    """The command with its program as an absolute path when it is a file (on
    Windows, CreateProcess doesn't find e.g. "dist/scoresight/scoresight.exe"),
    else as found on PATH. Raises FileNotFoundError if it is neither. Symbolic
    links are kept: a virtual environment's bin/python is one, and resolving
    it would run the base interpreter without the environment's packages."""
    if not command:
        raise FileNotFoundError("no command given")
    program = Path(command[0])
    if program.is_file():
        return [str(program.absolute()), *command[1:]]
    found = shutil.which(command[0])
    if found is None:
        raise FileNotFoundError(f"{command[0]} does not exist")
    return [found, *command[1:]]


def free_port() -> int:
    """A TCP port that is free on localhost right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((LOCALHOST, 0))
        return sock.getsockname()[1]


def app_env(data_dir: Path, log_dir: Path, **extra: str) -> dict[str, str]:
    """The environment for the app: headless Qt, the given settings and log
    folders, no profile and no startup report unless given in extra."""
    env = dict(os.environ)
    for name in ("SCORESIGHT_PROFILE", "SCORESIGHT_STARTUP_REPORT"):
        env.pop(name, None)
    env.update(
        QT_QPA_PLATFORM="offscreen",
        SCORESIGHT_DATA_DIR=str(data_dir),
        SCORESIGHT_LOG_DIR=str(log_dir),
    )
    env.update(extra)
    return env


def ensure_console() -> None:
    """Windows: make sure this process has a console. Ctrl+Break can only be
    sent to processes that share the sender's console, and some CI runners
    start steps without one. The new console's window is hidden."""
    if sys.platform != "win32":
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32
    if kernel32.GetConsoleCP():  # 0: no console
        return
    if kernel32.AllocConsole():
        window = kernel32.GetConsoleWindow()
        if window:
            ctypes.windll.user32.ShowWindow(window, 0)  # SW_HIDE


def kill_process_tree(process: subprocess.Popen) -> None:
    """Kill the process and everything it started (a 0.1.x onefile build runs
    the app in a child of its bootloader)."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            # the app leads its own session (start_new_session)
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        process.wait(10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(10)


class AppProcess:
    """The app, started with its output (stdout and stderr) going to a file."""

    def __init__(self, command: list[str], env: dict[str, str], output_path: Path):
        self.command = list(command)
        self.output_path = Path(output_path)
        ensure_console()
        kwargs = {}
        if sys.platform == "win32":
            # Ctrl+Break is sent to a process group: one for the app alone
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            # its own session (and process group), so the whole tree can be killed
            kwargs["start_new_session"] = True
        self._output = open(self.output_path, "wb")
        self.started_wall = time.time()
        self.started = time.perf_counter()
        try:
            self.process = subprocess.Popen(
                self.command,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=self._output,
                stderr=subprocess.STDOUT,
                **kwargs,
            )
        except BaseException:
            self._output.close()
            raise
        self.exit_code: int | None = None
        self.killed = False

    def elapsed(self) -> float:
        """Seconds since the app was started."""
        return time.perf_counter() - self.started

    def running(self) -> bool:
        return self.process.poll() is None

    def request_stop(self) -> bool:
        """Ask the app to quit like a user would. Returns False if the request
        couldn't be sent."""
        if not self.running():
            return True
        try:
            if sys.platform == "win32":
                os.kill(self.process.pid, signal.CTRL_BREAK_EVENT)
            else:
                os.kill(self.process.pid, signal.SIGTERM)
        except OSError as e:
            print(f"warning: could not ask the app to quit: {e}", flush=True)
            return False
        return True

    def stop(self, timeout: float) -> int | None:
        """Ask the app to quit and wait up to timeout seconds. Returns its exit
        code, or None if it had to be killed."""
        if self.running():
            self.request_stop()
            try:
                self.process.wait(timeout)
            except subprocess.TimeoutExpired:
                print(
                    f"warning: the app did not quit within {timeout:g} s; killing it",
                    flush=True,
                )
                self.kill()
                return None
        self.exit_code = self.process.returncode
        return self.exit_code

    def kill(self) -> None:
        if self.running():
            self.killed = True
        kill_process_tree(self.process)
        self._output.close()

    def close(self) -> None:
        """Kill the app if it still runs and release the output file."""
        if self.running():
            self.kill()
        self._output.close()

    def output(self) -> str:
        """What the app printed so far (the file is written by the app only)."""
        try:
            return self.output_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def http_get(port: int, path: str = "/json", timeout: float = 5.0) -> tuple[int, bytes]:
    """GET http://127.0.0.1:<port><path>. Returns (status, body); raises
    OSError (or http.client.HTTPException) if there is no answer."""
    connection = http.client.HTTPConnection(LOCALHOST, port, timeout=CONNECT_TIMEOUT)
    try:
        connection.connect()
        connection.sock.settimeout(timeout)
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def wait_for_http(
    app: AppProcess, port: int, timeout: float, path: str = "/json"
) -> float | None:
    """Poll GET path until it answers 200. Returns the seconds since the app
    started, or None if it didn't answer within timeout or the app exited."""
    deadline = app.started + timeout
    while time.perf_counter() < deadline:
        if not app.running():
            return None
        try:
            status, _ = http_get(port, path, timeout=2.0)
        except (OSError, http.client.HTTPException):
            status = None
        if status == 200:
            return app.elapsed()
        time.sleep(POLL_INTERVAL)
    return None


def tail(text: str, lines: int) -> str:
    return "\n".join(text.rstrip().splitlines()[-lines:])


def print_diagnostics(app: AppProcess | None, log_dir: Path, lines: int = 150) -> None:
    """Print the app's output and the end of its log files (a windowed app
    can't show a traceback)."""
    if app is not None:
        print(f"----- app output ({app.output_path.name}) -----")
        print(tail(app.output(), lines) or "(none)")
    log_files = sorted(Path(log_dir).glob("*.log")) if Path(log_dir).is_dir() else []
    if not log_files:
        print(f"----- no log files in {log_dir} -----")
    for log_file in log_files:
        print(f"----- {log_file.name} (last {lines} lines) -----")
        print(tail(log_file.read_text(encoding="utf-8", errors="replace"), lines))
    sys.stdout.flush()


def append_step_summary(markdown: str) -> None:
    """Add Markdown to the GitHub Actions job summary (if there is one)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(markdown.rstrip() + "\n\n")

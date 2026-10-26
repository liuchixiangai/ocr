"""Test Update now's installer side on Windows: Setup run as an update.

    python scripts/ci/update_test.py dist/scoresight-<version>-windows-x64-setup.exe

Installs the installer for the current user (no UAC prompt) into a throw-away
folder, then runs it again as ScoreSight's Update now does (src/app_update.py:
the same command line, a mutex held as the app holds it) and checks that Setup
- writes the ready file (the app then closes),
- waits while the mutex is held (the app hasn't quit yet),
- installs once it is released, and
- starts the installed app again, with the updating app's environment (its
  settings and log folders, its profile): the app writes its startup report.
Then stops that app and uninstalls. Exits 0 if all of this happened.
"""

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# how long each step may take, in seconds
TIMEOUT = 180
# the mutex is held this long once Setup is ready: it must wait meanwhile
HOLD_S = 3.0


def fail(message: str, setup_log: Path | None = None):
    print(f"FAILED: {message}", flush=True)
    if setup_log is not None and setup_log.exists():
        print(f"----- {setup_log.name} -----")
        print(setup_log.read_text(encoding="utf-8", errors="replace").rstrip())
    sys.exit(1)


def wait_for(condition, timeout: float, interval: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return condition()


def same_file(a, b) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
if _kernel32 is not None:
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    _kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def image_of(pid: int) -> tuple[str | None, str]:
    """(the process's executable, or None; why not)."""
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None, f"OpenProcess error {ctypes.get_last_error()}"
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        if not _kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(length)
        ):
            return None, f"QueryFullProcessImageNameW error {ctypes.get_last_error()}"
        return buffer.value, ""
    finally:
        _kernel32.CloseHandle(handle)


def all_processes() -> list[int]:
    psapi = ctypes.WinDLL("psapi")
    ids = (wintypes.DWORD * 8192)()
    size = wintypes.DWORD()
    psapi.EnumProcesses(ids, ctypes.sizeof(ids), ctypes.byref(size))
    return list(ids[: size.value // ctypes.sizeof(wintypes.DWORD)])


def processes_of(exe: Path) -> list[int]:
    """The ids of the running processes of this executable (the same file,
    whatever the path's form: CI's temporary folder is a short 8.3 path)."""
    found = []
    for pid in all_processes():
        image, _ = image_of(pid)
        if image is not None and same_file(image, exe):
            found.append(pid)
    return found


def scoresight_processes() -> list[str]:
    """Every running scoresight.exe, for a failure's message."""
    found = []
    for pid in all_processes():
        image, _ = image_of(pid)
        if image is not None and os.path.basename(image).lower() == "scoresight.exe":
            found.append(f"{pid}: {image}")
    return found


def kill(pid: int):
    handle = _kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if handle:
        _kernel32.TerminateProcess(handle, 1)
        _kernel32.CloseHandle(handle)


def run(setup: Path, work: Path):
    app = work / "ScoreSight"
    exe = app / "scoresight.exe"
    report = work / "startup-report.json"
    setup_log = work / "setup-update.log"
    # what the relaunched app must inherit: throw-away settings, headless
    os.environ.update(
        QT_QPA_PLATFORM="offscreen",
        SCORESIGHT_DATA_DIR=str(work / "data"),
        SCORESIGHT_LOG_DIR=str(work / "logs"),
        SCORESIGHT_PROFILE="update-test",
        SCORESIGHT_STARTUP_REPORT=str(report),
    )
    sys.path.insert(0, str(ROOT / "src"))
    import app_update  # after the environment: sc_logging makes its log files

    print(f"Installing {setup.name} into {app}", flush=True)
    install = subprocess.run(
        [
            str(setup),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CURRENTUSER",
            f"/DIR={app}",
        ],
        timeout=TIMEOUT,
    )
    if install.returncode != 0 or not exe.exists():
        fail(f"the installation failed (exit code {install.returncode})")

    print("Updating, as Update now does", flush=True)
    # this script's own logs (sc_logging) are there too
    logs_before = set((work / "logs").glob("scoresight_*.log"))
    mutex = app_update.mutex_name()
    handle = app_update.hold_mutex(mutex)
    if not handle:
        fail("the mutex couldn't be created")
    ready = work / "installing.txt"
    # as Update now does: this installation (another one may be registered:
    # build.yml installs 0.1.0 for all users before)
    mode = app_update.install_mode(str(app))
    if mode != app_update.CURRENT_USER:
        fail(f"the installation's mode is {mode}, not {app_update.CURRENT_USER}")
    process = app_update.start_installer(
        str(setup), str(ready), mutex, str(setup_log), str(app), mode
    )
    if not wait_for(lambda: ready.exists() or process.poll() is not None, TIMEOUT):
        fail("Setup didn't write the ready file", setup_log)
    if not ready.exists():
        fail(f"Setup ended ({process.returncode}) without the ready file", setup_log)
    print("Setup is ready; the app holds the mutex", flush=True)
    time.sleep(HOLD_S)
    if process.poll() is not None:
        fail("Setup didn't wait for the app to quit", setup_log)
    # the app quits
    _kernel32.CloseHandle(handle)
    try:
        code = process.wait(TIMEOUT)
    except subprocess.TimeoutExpired:
        fail("Setup didn't finish", setup_log)
    if code != 0:
        fail(f"Setup's exit code is {code}", setup_log)
    log = setup_log.read_text(encoding="utf-8", errors="replace")
    if "Installation process succeeded" not in log:
        fail("Setup didn't install", setup_log)
    print("Installed; waiting for the new version to start", flush=True)
    if not wait_for(report.exists, TIMEOUT):
        fail(
            "the installed app wasn't started again (or not with the updating "
            "app's environment: no startup report)",
            setup_log,
        )
    data = json.loads(report.read_text(encoding="utf-8"))
    print(f"The new version started: {json.dumps(data)[:200]}", flush=True)
    if not set((work / "logs").glob("scoresight_*.log")) - logs_before:
        fail("the relaunched app didn't log to the updating app's log folder")
    # the relaunched app, by the id in its report
    pid = data.get("pid")
    image, why = image_of(pid) if isinstance(pid, int) else (None, "no pid")
    if image is None or not same_file(image, exe):
        fail(
            f"the started app (pid {pid}) isn't {exe}: {image or why}; running: "
            f"{scoresight_processes() or 'no scoresight.exe'}",
            setup_log,
        )
    print(f"It is the installed app: {image}", flush=True)
    kill(pid)
    wait_for(lambda: not processes_of(exe), 30)
    print("UPDATE TEST OK", flush=True)


def uninstall(work: Path):
    uninstaller = work / "ScoreSight" / "unins000.exe"
    if not uninstaller.exists():
        return
    for pid in processes_of(work / "ScoreSight" / "scoresight.exe"):
        kill(pid)
    subprocess.run(
        [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
        timeout=TIMEOUT,
    )
    # the uninstaller finishes from a temporary copy of itself
    wait_for(lambda: not (work / "ScoreSight" / "scoresight.exe").exists(), 60)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("setup", type=Path, help="the Windows installer")
    parser.add_argument(
        "--work-dir", type=Path, help="keep the files here (default: a temp folder)"
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        print("The Windows installer is tested on Windows only")
        return 1
    setup = args.setup.resolve()
    if not setup.exists():
        print(f"No {setup}")
        return 1
    if args.work_dir:
        work = args.work_dir.resolve()
        work.mkdir(parents=True, exist_ok=True)
        try:
            run(setup, work)
        finally:
            uninstall(work)
        return 0
    # this script's log files stay open until it ends
    with tempfile.TemporaryDirectory(
        prefix="scoresight-update-test-", ignore_cleanup_errors=True
    ) as tmp:
        work = Path(tmp)
        try:
            run(setup, work)
        finally:
            uninstall(work)
    return 0


if __name__ == "__main__":
    sys.exit(main())

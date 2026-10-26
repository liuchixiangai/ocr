"""Smoke-test a built ScoreSight app by running its --self-test.

    python scripts/ci/smoke_test.py dist/scoresight/scoresight.exe
    python scripts/ci/smoke_test.py dist/ScoreSight.app/Contents/MacOS/scoresight

Runs the app headless (QT_QPA_PLATFORM=offscreen) with throw-away settings and
log folders, and passes only if it exits with code 0 and prints "SELF-TEST OK"
within the timeout. On failure it prints the app's output and its log files,
since a windowed app can't show a traceback. Works for the GUI-subsystem Windows
executable too: its output is captured through pipes.
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

EXPECTED_OUTPUT = "SELF-TEST OK"


def _print_logs(log_dir: Path) -> None:
    for log_file in sorted(log_dir.glob("*.log")):
        print(f"----- {log_file.name} -----")
        print(log_file.read_text(encoding="utf-8", errors="replace").rstrip())


def run_smoke_test(command: list[str], timeout: float) -> int:
    """Run `command --self-test`; return 0 if it passed, 1 otherwise."""
    with tempfile.TemporaryDirectory(prefix="scoresight-smoke-") as tmp:
        data_dir = Path(tmp, "data")
        log_dir = Path(tmp, "logs")
        env = dict(
            os.environ,
            QT_QPA_PLATFORM="offscreen",
            SCORESIGHT_DATA_DIR=str(data_dir),
            SCORESIGHT_LOG_DIR=str(log_dir),
        )
        env.pop("SCORESIGHT_PROFILE", None)
        print(f"$ {subprocess.list2cmdline([*command, '--self-test'])}", flush=True)
        try:
            result = subprocess.run(
                [*command, "--self-test"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            output = (e.output or b"").decode("utf-8", errors="replace")
            print(output.rstrip())
            print(f"FAILED: no result within {timeout:g} s")
            _print_logs(log_dir)
            return 1
        output = result.stdout.decode("utf-8", errors="replace")
        print(output.rstrip())
        if result.returncode != 0:
            print(f"FAILED: exit code {result.returncode}")
        elif EXPECTED_OUTPUT not in output:
            print(f"FAILED: exit code 0 but no {EXPECTED_OUTPUT!r} in the output")
        else:
            print("smoke test passed")
            return 0
        _print_logs(log_dir)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("executable", help="the built app's executable")
    parser.add_argument(
        "--timeout", type=float, default=300, help="seconds (default: %(default)s)"
    )
    args = parser.parse_args(argv)
    executable = Path(args.executable)
    if not executable.is_file():
        print(f"error: {executable} does not exist", file=sys.stderr)
        return 1
    return run_smoke_test([str(executable.resolve())], args.timeout)


if __name__ == "__main__":
    sys.exit(main())

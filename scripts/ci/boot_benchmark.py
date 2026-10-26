"""Boot-time benchmark: how long ScoreSight takes from launch until its HTTP
server answers.

    python scripts/ci/boot_benchmark.py --runs 5 --json boot.json -- dist/scoresight/scoresight.exe
    python scripts/ci/boot_benchmark.py --legacy --runs 3 --label 0.1.0 -- /path/to/0.1.0/scoresight
    python scripts/ci/boot_benchmark.py --compare boot.json boot-0.1.0.json

Each run starts the app headless (offscreen) with fresh settings and log
folders, measures the time from spawning it until GET /json on its HTTP server
returns 200, then asks it to quit (SIGTERM; Ctrl+Break on Windows) and kills
it if it doesn't. Run 1 is "cold" (the first launch of that binary, e.g. while
the OS still scans it), runs 2..N are "warm". The result reports the cold
time, the median and range of the warm ones and, for this version of the app,
the startup milestones the app measures itself (SCORESIGHT_STARTUP_REPORT: the
time before main.py, imports, main window, ..., see src/startup_timing.py).

--legacy runs a ScoreSight 0.1.x build: it has no --port or --profile option,
always serves on port 18099, ignores SCORESIGHT_DATA_DIR/LOG_DIR (it uses the
user's folders, empty on a CI runner) and writes no startup report. Both
versions start their HTTP server while the main window is being built, so the
probe compares like with like.

--max-cold-seconds/--max-warm-seconds make the run fail when exceeded (or when
a run doesn't get ready at all). --compare prints (and adds to the GitHub job
summary) a table of earlier --json results side by side.
"""

import argparse
import http.client
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import app_harness

# How long, after the HTTP server answered, to wait for the app's startup
# report (written once the video sources are discovered, or 5 s after the
# event loop started).
REPORT_WAIT_SECONDS = 10.0
STOP_TIMEOUT_SECONDS = 20.0
# 0.1.x doesn't handle SIGTERM or Ctrl+Break (on Windows it is killed anyway)
LEGACY_STOP_TIMEOUT_SECONDS = 5.0
# the in-app milestones shown in the summary: (label, function of the report)
IN_APP_ROWS = (
    ("in-app: before main.py", lambda r: r.get("before_main")),
    *(
        (f"in-app: {label}", lambda r, label=label: r.get("phases", {}).get(label))
        for label in (
            "imports",
            "QApplication",
            "main window",
            "show",
            "first event loop pass",
        )
    ),
    ("in-app: total (ready)", lambda r: r.get("total")),
    (
        "in-app: HTTP server ready",
        lambda r: r.get("ready_at", {}).get("HTTP server ready"),
    ),
    (
        "in-app: video sources discovered",
        lambda r: r.get("ready_at", {}).get("video sources discovered"),
    ),
)


def wait_until_port_free(port: int, timeout: float) -> bool:
    """Wait until nothing answers on the port (a legacy run's server is gone)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            app_harness.http_get(port, "/json", timeout=1.0)
        except (OSError, http.client.HTTPException):
            return True
        time.sleep(0.2)
    return False


def wait_for_file(path: Path, timeout: float, app: app_harness.AppProcess) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and app.running():
        if path.is_file():
            return True
        time.sleep(0.05)
    return path.is_file()


def run_once(
    command: list[str], run: int, port: int | None, legacy: bool, timeout: float
) -> dict:
    """Launch the app once and measure it. Returns the run's record."""
    record = {"run": run, "kind": "cold" if run == 1 else "warm"}
    with tempfile.TemporaryDirectory(
        prefix="scoresight-boot-", ignore_cleanup_errors=True
    ) as tmp:
        tmp = Path(tmp)
        report_path = tmp / "startup-report.json"
        extra = {} if legacy else {"SCORESIGHT_STARTUP_REPORT": str(report_path)}
        env = app_harness.app_env(tmp / "data", tmp / "logs", **extra)
        if legacy:
            port = app_harness.LEGACY_HTTP_PORT
            full_command = list(command)
        else:
            port = port or app_harness.free_port()
            full_command = [*command, "--port", str(port)]
        record["port"] = port
        with app_harness.AppProcess(full_command, env, tmp / "app-output.txt") as app:
            ready = app_harness.wait_for_http(app, port, timeout)
            record["ready"] = ready
            if ready is None:
                record["error"] = (
                    f"exited with code {app.process.poll()} before its HTTP server "
                    "answered"
                    if not app.running()
                    else f"its HTTP server didn't answer within {timeout:g} s"
                )
            elif not legacy and wait_for_file(report_path, REPORT_WAIT_SECONDS, app):
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    report = None
                record["startup_report"] = report
                if report and report.get("main_started_at"):
                    # bootloader + interpreter startup, seen from outside
                    record["spawn_to_main"] = (
                        report["main_started_at"] - app.started_wall
                    )
            stop_started = time.perf_counter()
            record["exit_code"] = app.stop(
                LEGACY_STOP_TIMEOUT_SECONDS if legacy else STOP_TIMEOUT_SECONDS
            )
            record["stop_seconds"] = time.perf_counter() - stop_started
            record["killed"] = app.killed
            if ready is None:
                app_harness.print_diagnostics(app, tmp / "logs", lines=60)
    if legacy and not wait_until_port_free(port, 15):
        record["warning"] = f"port {port} still answers after the app quit"
    return record


def summarize(runs: list[dict]) -> dict:
    """Cold, warm median/min/max (seconds to ready) and the in-app milestones."""
    ready = [r["ready"] for r in runs if r.get("ready") is not None]
    warm = [r["ready"] for r in runs[1:] if r.get("ready") is not None]
    cold = runs[0].get("ready") if runs else None
    summary = {
        "cold": cold,
        "warm_median": statistics.median(warm) if warm else None,
        "warm_min": min(warm) if warm else None,
        "warm_max": max(warm) if warm else None,
        "all_median": statistics.median(ready) if ready else None,
        "runs": len(runs),
        "failed_runs": sum(1 for r in runs if r.get("ready") is None),
    }
    reports = [r.get("startup_report") for r in runs]
    if any(reports):
        in_app = {}
        for label, value in IN_APP_ROWS:
            cold_value = value(reports[0]) if reports[0] else None
            warm_values = [value(r) for r in reports[1:] if r and value(r) is not None]
            in_app[label] = {
                "cold": cold_value,
                "warm_median": statistics.median(warm_values) if warm_values else None,
            }
        summary["in_app"] = in_app
    return summary


def check_limits(summary: dict, max_cold: float | None, max_warm: float | None):
    """The reasons the benchmark fails (empty if it passes)."""
    failures = []
    if summary["failed_runs"]:
        failures.append(f"{summary['failed_runs']} run(s) never got ready")
    if (
        max_cold is not None
        and summary["cold"] is not None
        and summary["cold"] > max_cold
    ):
        failures.append(f"cold start {summary['cold']:.2f} s > {max_cold:g} s")
    if (
        max_warm is not None
        and summary["warm_median"] is not None
        and summary["warm_median"] > max_warm
    ):
        failures.append(
            f"warm start median {summary['warm_median']:.2f} s > {max_warm:g} s"
        )
    return failures


def _seconds(value) -> str:
    return "-" if value is None else f"{value:.2f} s"


def _range(low, high) -> str:
    return "-" if low is None else f"{low:.2f}-{high:.2f} s"


def format_result(result: dict) -> str:
    """Markdown for one benchmark result."""
    summary = result["summary"]
    lines = [
        f"### Boot time: {result['label']}",
        "",
        f"| | cold (run 1) | warm median (runs 2-{summary['runs']}) | warm min-max |",
        "|---|---|---|---|",
        f"| launch to HTTP server answering | {_seconds(summary['cold'])} | "
        f"{_seconds(summary['warm_median'])} | "
        f"{_range(summary['warm_min'], summary['warm_max'])} |",
    ]
    for label, values in summary.get("in_app", {}).items():
        lines.append(
            f"| {label} | {_seconds(values['cold'])} | "
            f"{_seconds(values['warm_median'])} | |"
        )
    if summary["failed_runs"]:
        lines.append("")
        lines.append(f"{summary['failed_runs']} of {summary['runs']} runs failed.")
    for failure in result.get("failures", []):
        lines.append(f"- FAILED: {failure}")
    return "\n".join(lines)


def format_comparison(results: list[tuple[str, dict | None]]) -> str:
    """Markdown table of several results (name, result or None if missing)."""
    lines = [
        "### Boot time comparison",
        "",
        "From launch until the HTTP server answers, headless, on the machine that "
        "ran the benchmark.",
        "",
        "| Build | cold (run 1) | warm median | warm min-max | runs (failed) |",
        "|---|---|---|---|---|",
    ]
    for name, result in results:
        if result is None:
            lines.append(f"| {name} | no result | | | |")
            continue
        s = result["summary"]
        lines.append(
            f"| {result.get('label') or name} | {_seconds(s['cold'])} | "
            f"{_seconds(s['warm_median'])} | {_range(s['warm_min'], s['warm_max'])} | "
            f"{s['runs']} ({s['failed_runs']}) |"
        )
    return "\n".join(lines)


def compare(paths: list[Path]) -> int:
    results = []
    for path in paths:
        try:
            results.append((path.name, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, KeyError):
            results.append((path.name, None))
    markdown = format_comparison(results)
    print(markdown)
    app_harness.append_step_summary(markdown)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        usage="%(prog)s [options] -- <app command...>\n"
        "       %(prog)s --compare RESULT.json [RESULT.json ...]",
    )
    parser.add_argument("--runs", type=int, default=5, help="(default: %(default)s)")
    parser.add_argument(
        "--port", type=int, help="HTTP port for the app (default: a free port per run)"
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="a 0.1.x build: no --port (it serves on 18099), no startup report",
    )
    parser.add_argument("--label", default="", help="name of the build in the summary")
    parser.add_argument("--json", type=Path, help="write the results to this file")
    parser.add_argument(
        "--timeout",
        type=float,
        default=120,
        help="seconds a run may take to get ready (default: %(default)s)",
    )
    parser.add_argument("--max-cold-seconds", type=float, metavar="S")
    parser.add_argument("--max-warm-seconds", type=float, metavar="S")
    parser.add_argument(
        "--compare",
        type=Path,
        nargs="+",
        metavar="RESULT",
        help="summarize earlier --json results side by side, then exit",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER, help="the app command")
    args = parser.parse_args(argv)
    if args.compare:
        return compare(args.compare)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("give the app command after --")
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    try:
        command = app_harness.resolve_command(command)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    label = args.label or Path(command[-1]).name
    if args.legacy and not wait_until_port_free(app_harness.LEGACY_HTTP_PORT, 5):
        print(f"error: port {app_harness.LEGACY_HTTP_PORT} is in use", file=sys.stderr)
        return 1

    print(f"$ {subprocess.list2cmdline(command)}", flush=True)
    runs = []
    for run in range(1, args.runs + 1):
        record = run_once(command, run, args.port, args.legacy, args.timeout)
        runs.append(record)
        line = f"run {run} ({record['kind']}): ready after {_seconds(record['ready'])}"
        report = record.get("startup_report")
        if report and report.get("total") is not None:
            line += f", in-app total {_seconds(report['total'])}"
        if record["exit_code"] is None:
            line += ", had to be killed"
        elif record["exit_code"] != 0:
            line += f", exit code {record['exit_code']}"
        if record.get("error"):
            line += f" - {record['error']}"
        print(line, flush=True)

    summary = summarize(runs)
    failures = check_limits(summary, args.max_cold_seconds, args.max_warm_seconds)
    result = {
        "label": label,
        "legacy": args.legacy,
        "command": command,
        "platform": sys.platform,
        "limits": {
            "max_cold": args.max_cold_seconds,
            "max_warm": args.max_warm_seconds,
        },
        "summary": summary,
        "runs": runs,
        "failures": failures,
        "passed": not failures,
    }
    markdown = format_result(result)
    print(markdown, flush=True)
    app_harness.append_step_summary(markdown)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())

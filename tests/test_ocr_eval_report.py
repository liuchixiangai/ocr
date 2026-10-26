"""The committed OCR evaluation report (ocr_eval/results/2026-10-11/): its
tables are what tables.py computes from the committed results, and its
results files read."""

import runpy
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from ocr_eval.report import read_results  # noqa: E402

RESULTS = Path(__file__).resolve().parents[1] / "ocr_eval" / "results" / "2026-10-11"


def test_report_tables_match_the_committed_results(capsys):
    tables = runpy.run_path(str(RESULTS / "tables.py"), run_name="tables")
    assert tables["main"](["--check", str(RESULTS / "REPORT.md")]) == 0, (
        capsys.readouterr().out
    )


def test_results_files_read():
    accuracy = read_results(RESULTS / "results.json.gz")
    assert set(accuracy["datasets"]) == {
        "textocr_scoreboards",
        "commons_scoreboards",
        "synthetic_7seg",
    }
    assert all(run["status"] == "ok" for run in accuracy["runs"])
    latency = read_results(RESULTS / "latency.json.gz")
    assert {run["threads"] for run in latency["runs"]} >= {"1", "all"}
    assert all("machine_load" in run for run in latency["runs"])

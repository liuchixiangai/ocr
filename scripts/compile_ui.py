"""Compile the Qt Designer files src/*.ui into src/ui_*.py.

Runs the pyside6-uic that belongs to the interpreter running this script (the
PySide6 installed in the active venv), with the flags the committed files were
generated with. Output is always written with LF line endings, so it is the same
on every OS.

Usage:
    python scripts/compile_ui.py                 # regenerate every src/ui_*.py
    python scripts/compile_ui.py src/about.ui    # regenerate specific files
    python scripts/compile_ui.py --check         # CI: fail if any ui_*.py is stale

--check compiles into a temp dir and exits 1 if a committed ui_*.py differs from
the fresh output (ignoring line endings and the uic version header line), is
missing, or has no matching .ui file.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"

# -p: no header protection (C++ only, kept for parity with the historic output)
# -n: no implicit custom-widget includes/imports
UIC_FLAGS = ["-p", "-n", "--no-qt-namespace", "--star-imports"]

# Same entry point as the `pyside6-uic` console script, but bound to this
# interpreter instead of whatever pyside6-uic is first on PATH.
_UIC_LAUNCHER = (
    "import sys; from PySide6.scripts.pyside_tool import uic; "
    "sys.argv[0] = 'pyside6-uic'; uic()"
)

_VERSION_LINE_PREFIX = "## Created by: Qt User Interface Compiler version"


def generated_path(ui_file: Path) -> Path:
    return ui_file.with_name(f"ui_{ui_file.stem}.py")


def compile_ui(ui_file: Path) -> str:
    """Return the Python code pyside6-uic generates for ui_file (LF endings)."""
    with tempfile.TemporaryDirectory() as tmp:
        out_file = Path(tmp) / generated_path(ui_file).name
        subprocess.run(
            [
                sys.executable,
                "-c",
                _UIC_LAUNCHER,
                ui_file.name,
                "-o",
                str(out_file),
                *UIC_FLAGS,
            ],
            cwd=ui_file.parent,
            # uic must not inherit (and wait on) an interactive stdin
            stdin=subprocess.DEVNULL,
            check=True,
        )
        code = out_file.read_bytes().decode("utf-8")
    return code.replace("\r\n", "\n")


def _comparable(code: str) -> list[str]:
    return [
        line
        for line in code.replace("\r\n", "\n").split("\n")
        if not line.startswith(_VERSION_LINE_PREFIX)
    ]


def write(ui_files: list[Path]) -> int:
    for ui_file in ui_files:
        py_file = generated_path(ui_file)
        code = compile_ui(ui_file).encode("utf-8")
        if py_file.exists() and py_file.read_bytes() == code:
            print(f"Unchanged {py_file.relative_to(REPO_ROOT).as_posix()}")
            continue
        py_file.write_bytes(code)
        print(
            f"Compiled  {ui_file.relative_to(REPO_ROOT).as_posix()} -> "
            f"{py_file.relative_to(REPO_ROOT).as_posix()}"
        )
    return 0


def check(ui_files: list[Path], check_orphans: bool) -> int:
    problems = []
    for ui_file in ui_files:
        py_file = generated_path(ui_file)
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        if not py_file.exists():
            problems.append(f"{rel} is missing")
            continue
        fresh = compile_ui(ui_file)
        committed = py_file.read_bytes().decode("utf-8")
        if _comparable(fresh) != _comparable(committed):
            problems.append(f"{rel} is out of date with {ui_file.name}")
    if check_orphans:
        sources = {generated_path(ui_file) for ui_file in ui_files}
        for py_file in sorted(SRC_DIR.glob("ui_*.py")):
            if py_file not in sources:
                problems.append(
                    f"{py_file.relative_to(REPO_ROOT).as_posix()} has no matching .ui file"
                )

    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        print(
            "Regenerate with: python scripts/compile_ui.py "
            "(never edit src/ui_*.py by hand)",
            file=sys.stderr,
        )
        return 1
    print(f"All {len(ui_files)} generated UI modules are up to date.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compile src/*.ui into src/ui_*.py with pyside6-uic."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="don't write anything; exit 1 if any generated file is stale",
    )
    parser.add_argument(
        "ui_files",
        nargs="*",
        type=Path,
        help="specific .ui files to compile (default: every src/*.ui)",
    )
    args = parser.parse_args(argv)

    if args.ui_files:
        ui_files = [p.resolve() for p in args.ui_files]
        for ui_file in ui_files:
            if ui_file.suffix != ".ui" or not ui_file.is_file():
                parser.error(f"not a .ui file: {ui_file}")
    else:
        ui_files = sorted(SRC_DIR.glob("*.ui"))

    try:
        if args.check:
            return check(ui_files, check_orphans=not args.ui_files)
        return write(ui_files)
    except subprocess.CalledProcessError as e:
        print(
            f"error: pyside6-uic failed with exit code {e.returncode}", file=sys.stderr
        )
        return e.returncode or 1


if __name__ == "__main__":
    sys.exit(main())

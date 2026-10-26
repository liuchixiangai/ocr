"""Refresh the translations: pyside6-lupdate collects the translatable strings of
src/*.py and src/*.ui into translations/*.ts, then pyside6-lrelease compiles
each .ts into the .qm file the app loads.

Runs the PySide6 tools that belong to the interpreter running this script (the
PySide6 installed in the active venv), on every OS.

Usage:
    python scripts/update_translations.py            # update the .ts, compile the .qm
    python scripts/update_translations.py --qm-only  # only compile the .qm (after
                                                     # translating in Qt Linguist)
    python scripts/update_translations.py --check    # CI: fail if they are stale

lupdate merges: existing translations are kept, new strings are added as
unfinished (the app shows the English text until they are translated), and
strings that are gone are kept as vanished/obsolete for the translators'
reference. The generated src/ui_*.py are not scanned; their strings come from
the .ui files.

--check runs lupdate and lrelease into a temp dir and exits 1 if a .ts file is
missing a string from the code (or still lists a removed one as current), or a
.qm file differs from what lrelease makes of its .ts. Source line numbers
(<location>) are ignored, so moving code doesn't make the files stale.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
TRANSLATIONS_DIR = REPO_ROOT / "translations"

# Same entry points as the pyside6-lupdate / pyside6-lrelease console scripts,
# but bound to this interpreter instead of whatever is first on PATH.
_TOOL_LAUNCHER = (
    "import sys; from PySide6.scripts.pyside_tool import {tool}; "
    "sys.argv[0] = 'pyside6-{tool}'; {tool}()"
)

# translation types of strings that are no longer in the code
_GONE = {"vanished", "obsolete"}


def source_files() -> list[str]:
    """The files lupdate scans, relative to the repository root."""
    files = sorted(SRC_DIR.glob("*.ui")) + sorted(
        p for p in SRC_DIR.glob("*.py") if not p.name.startswith("ui_")
    )
    return [p.relative_to(REPO_ROOT).as_posix() for p in files]


def ts_files(directory: Path = TRANSLATIONS_DIR) -> list[Path]:
    return sorted(directory.glob("*.ts"))


def _run_tool(tool: str, args: list[str], quiet: bool = False):
    subprocess.run(
        [sys.executable, "-c", _TOOL_LAUNCHER.format(tool=tool), *args],
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL if quiet else None,
        check=True,
    )


def _to_lf(path: Path):
    data = path.read_bytes()
    if b"\r\n" in data:
        path.write_bytes(data.replace(b"\r\n", b"\n"))


def lupdate(targets: list[Path], quiet: bool = False):
    """Merge the strings of the source files into the target .ts files."""
    _run_tool(
        "lupdate",
        [*source_files(), "-ts", *(str(t) for t in targets)],
        quiet=quiet,
    )
    for target in targets:
        _to_lf(target)


def lrelease(ts_file: Path, qm_file: Path, quiet: bool = False):
    _run_tool("lrelease", [str(ts_file), "-qm", str(qm_file)], quiet=quiet)


def messages(ts_file: Path) -> tuple[set, set]:
    """(current, gone) messages of a .ts file as (context, source, comment)."""
    current, gone = set(), set()
    root = ET.parse(ts_file).getroot()
    for context in root.iter("context"):
        name = context.findtext("name") or ""
        for message in context.iter("message"):
            key = (
                name,
                message.findtext("source") or "",
                message.findtext("comment") or "",
            )
            translation = message.find("translation")
            kind = translation.get("type") if translation is not None else None
            (gone if kind in _GONE else current).add(key)
    return current, gone


def check_language(ts_file: Path) -> list[str]:
    """lupdate silently skips a .ts whose language it doesn't recognize, so the
    TS element must name the language of the file: scoresight_<language>.ts."""
    language = ET.parse(ts_file).getroot().get("language")
    expected = ts_file.stem.split("_", 1)[1] if "_" in ts_file.stem else None
    if language != expected:
        return [f'{ts_file.name}: <TS language="{language}"> should be "{expected}"']
    return []


def check_contexts(ts_file: Path) -> list[str]:
    """Problems with the strings lupdate found: a string without a context means
    a translate call it couldn't read (e.g. QCoreApplication.translate called
    through an alias), which the app would never find a translation for."""
    current, _ = messages(ts_file)
    return [
        f"{ts_file.name}: string without a context (source {source!r}); call "
        "self.tr() or QCoreApplication.translate('Context', ...) directly"
        for context, source, _ in sorted(current)
        if not context
    ]


def update(qm_only: bool = False) -> int:
    targets = ts_files()
    if not targets:
        print(f"no .ts files in {TRANSLATIONS_DIR}", file=sys.stderr)
        return 1
    problems = [p for t in targets for p in check_language(t)]
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    if not qm_only:
        lupdate(targets)
        problems = [p for t in targets[:1] for p in check_contexts(t)]
        if problems:
            print("\n".join(problems), file=sys.stderr)
            return 1
    for ts_file in targets:
        lrelease(ts_file, ts_file.with_suffix(".qm"), quiet=True)
    print(f"Compiled {len(targets)} translations in {TRANSLATIONS_DIR}")
    return 0


def check() -> int:
    targets = ts_files()
    if not targets:
        print(f"no .ts files in {TRANSLATIONS_DIR}", file=sys.stderr)
        return 1
    problems = [p for t in targets for p in check_language(t)]
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        copies = []
        for ts_file in targets:
            copy = tmp_dir / ts_file.name
            shutil.copyfile(ts_file, copy)
            copies.append(copy)
        lupdate(copies, quiet=True)
        problems += check_contexts(copies[0])
        for ts_file, copy in zip(targets, copies):
            committed, _ = messages(ts_file)
            fresh, fresh_gone = messages(copy)
            missing = fresh - committed
            removed = committed & fresh_gone
            if missing or removed:
                problems.append(
                    f"{ts_file.name}: {len(missing)} new string(s) missing, "
                    f"{len(removed)} removed string(s) still current"
                )
            qm_file = ts_file.with_suffix(".qm")
            fresh_qm = tmp_dir / qm_file.name
            lrelease(ts_file, fresh_qm, quiet=True)
            if not qm_file.is_file():
                problems.append(f"{qm_file.name} is missing")
            elif qm_file.read_bytes() != fresh_qm.read_bytes():
                problems.append(f"{qm_file.name} is not compiled from {ts_file.name}")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        print(
            "The translations are stale; run: python scripts/update_translations.py",
            file=sys.stderr,
        )
        return 1
    print(f"All {len(targets)} translations are up to date.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true", help="fail if the .ts or .qm files are stale"
    )
    mode.add_argument(
        "--qm-only", action="store_true", help="only compile the .ts files to .qm"
    )
    args = parser.parse_args(argv)
    if args.check:
        return check()
    return update(qm_only=args.qm_only)


if __name__ == "__main__":
    sys.exit(main())

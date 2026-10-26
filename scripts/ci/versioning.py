"""Version helpers shared by scoresight.spec, scripts/bump_version.py and CI.

src/version.py is the single source of truth for the version. This module reads
it without importing the app, and holds the rules the release tooling relies on:
the accepted version format, the tag check, package naming and the CHANGELOG
section lookup. Standard library only, so CI can run it before installing any
dependencies.

Usage:
    python scripts/ci/versioning.py version          # print src/version.py's version
    python scripts/ci/versioning.py check-tag TAG    # exit 1 unless TAG == version
    python scripts/ci/versioning.py changelog VER    # print CHANGELOG.md's [VER] section
    python scripts/ci/versioning.py github-output [--release] [--sha SHA]
        # version, label (package version), numeric (Windows X.Y.Z.0) and
        # prerelease, as key=value lines appended to $GITHUB_OUTPUT (or stdout)
"""

import argparse
import ast
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = REPO_ROOT / "src" / "version.py"
CHANGELOG_FILE = REPO_ROOT / "CHANGELOG.md"

# X.Y.Z, X.Y.Z-beta.N or X.Y.Z-rc.N: the version and tag formats releases use.
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(beta|rc)\.(\d+))?$")

# A Markdown link reference definition, e.g. "[0.1.0]: https://...".
_LINK_DEFINITION_RE = re.compile(r"^\[[^\]]+\]:\s+\S+")


def read_version(path: Path | str = VERSION_FILE) -> str:
    """The `__version__` string literal assigned in path (parsed, not imported)."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "__version__" for t in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise ValueError(f'{path}: no `__version__ = "..."` assignment found')


def parse_version(version: str) -> tuple[int, int, int, int, int]:
    """A sort key for a release version; ValueError if the format is wrong.

    Final releases sort after their release candidates, which sort after betas.
    """
    match = VERSION_RE.fullmatch(version)
    if not match:
        raise ValueError(
            f"invalid version {version!r}: expected X.Y.Z, X.Y.Z-beta.N or X.Y.Z-rc.N"
        )
    major, minor, patch, stage, number = match.groups()
    stage_rank = {"beta": 0, "rc": 1, None: 2}[stage]
    return int(major), int(minor), int(patch), stage_rank, int(number or 0)


def is_prerelease(version: str) -> bool:
    return parse_version(version)[3] < 2


def numeric_version(version: str) -> str:
    """The four-part X.Y.Z.0 Windows version resources need (no pre-release tag)."""
    major, minor, patch, _, _ = parse_version(version)
    return f"{major}.{minor}.{patch}.0"


def package_label(version: str, release: bool, sha: str | None = None) -> str:
    """The version used in package file names.

    Release builds use the version as is; any other build appends the short
    commit hash so test packages can't be mistaken for a release.
    """
    parse_version(version)
    if release or not sha:
        return version
    return f"{version}-{sha[:7]}"


def check_tag(tag: str, version: str) -> None:
    """Raise ValueError unless the release tag is exactly the app version."""
    parse_version(tag)
    if tag != version:
        raise ValueError(
            f"tag {tag!r} does not match src/version.py ({version!r}); "
            "bump the version with scripts/bump_version.py before tagging"
        )


def changelog_section(text: str, version: str) -> str:
    """The body of the `## [version]` section of a Keep a Changelog file.

    Raises ValueError if the section is missing or empty.
    """
    heading = re.compile(rf"^## \[{re.escape(version)}\](?:\s|$)")
    lines = text.splitlines()
    for start, line in enumerate(lines):
        if heading.match(line):
            break
    else:
        raise ValueError(f"CHANGELOG has no '## [{version}]' section")
    body = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    # the link reference definitions at the end of the file aren't part of the
    # last section
    while body and (not body[-1].strip() or _LINK_DEFINITION_RE.match(body[-1])):
        body.pop()
    section = "\n".join(body).strip("\n")
    if not section.strip():
        raise ValueError(f"CHANGELOG section '## [{version}]' is empty")
    return section + "\n"


def _github_output(release: bool, sha: str | None) -> str:
    version = read_version()
    values = {
        "version": version,
        "label": package_label(version, release, sha),
        "numeric": numeric_version(version),
        "prerelease": str(is_prerelease(version)).lower(),
    }
    return "".join(f"{key}={value}\n" for key, value in values.items())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="print the version in src/version.py")
    check = commands.add_parser("check-tag", help="fail unless TAG == the version")
    check.add_argument("tag")
    notes = commands.add_parser("changelog", help="print a CHANGELOG.md section")
    notes.add_argument("version")
    notes.add_argument("--file", type=Path, default=CHANGELOG_FILE)
    output = commands.add_parser("github-output", help="write version info for CI")
    output.add_argument("--release", action="store_true")
    output.add_argument("--sha", default=os.environ.get("GITHUB_SHA"))
    args = parser.parse_args(argv)

    try:
        if args.command == "version":
            print(read_version())
        elif args.command == "check-tag":
            version = read_version()
            check_tag(args.tag, version)
            print(f"tag {args.tag} matches src/version.py")
        elif args.command == "changelog":
            text = args.file.read_text(encoding="utf-8")
            # release notes are Markdown files: UTF-8 whatever the console uses
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            sys.stdout.write(changelog_section(text, args.version))
        elif args.command == "github-output":
            lines = _github_output(args.release, args.sha)
            sys.stdout.write(lines)
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
                    f.write(lines)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

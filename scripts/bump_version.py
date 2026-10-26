"""Prepare a release: set the version and cut the CHANGELOG section.

    python scripts/bump_version.py 0.2.0             # or 0.2.0-beta.1 / 0.2.0-rc.1
    python scripts/bump_version.py 0.2.0 --dry-run   # show the changes, write nothing

It sets `__version__` in src/version.py, renames CHANGELOG.md's
`## [Unreleased]` section to `## [X.Y.Z] - YYYY-MM-DD` (today, or --date), adds
a fresh empty `## [Unreleased]` above it, and updates the comparison links at the
bottom of the changelog. If src/version.py already has the new version (as for
the first release after the version was set during development), only the
CHANGELOG changes.

src/version.py holds the version in development, so its pre-releases can be cut
(0.2.0-beta.1 or 0.2.0-rc.1 while it says 0.2.0). The final X.Y.Z then folds
the sections of its pre-releases (and Unreleased, which may be empty) into its
own section, so its release notes list every change since the previous release.

It refuses to go backwards (below the newest release in CHANGELOG.md, or below
src/version.py other than to a pre-release of it), to reuse a version that
already has a section, or to release an empty Unreleased section otherwise.

Afterwards, review and commit, then tag (the release workflow builds the tag):
    git commit -am "Release X.Y.Z"
    git tag -a X.Y.Z -m "ScoreSight X.Y.Z"
    git push origin main X.Y.Z
"""

import argparse
import datetime
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "ci"))

from versioning import (  # noqa: E402
    CHANGELOG_FILE,
    VERSION_FILE,
    parse_version,
    read_version,
)

# The (?=\r?$) lookaheads leave a CRLF file's line endings alone.
UNRELEASED_HEADING_RE = re.compile(r"^## \[Unreleased\][ \t]*(?=\r?$)", re.MULTILINE)
VERSION_ASSIGNMENT_RE = re.compile(
    r"""^(__version__\s*=\s*)(["'])([^"']*)\2""", re.MULTILINE
)
# "[Unreleased]: https://github.com/owner/repo/compare/0.1.0...HEAD"
UNRELEASED_LINK_RE = re.compile(
    r"^\[Unreleased\]:[ \t]*(?P<base>\S+/compare/)(?P<previous>[^\s.]\S*?)"
    r"\.\.\.HEAD[ \t]*(?=\r?$)",
    re.MULTILINE,
)
# "## [0.2.0-rc.1] - 2026-10-10": the section of a released version
RELEASE_HEADING_RE = re.compile(
    r"^## \[(?P<version>\d+\.\d+\.\d+(?:-(?:beta|rc)\.\d+)?)\][^\r\n]*",
    re.MULTILINE,
)
SECTION_HEADING_RE = re.compile(r"^## ", re.MULTILINE)
SUBSECTION_HEADING_RE = re.compile(r"^### .*$", re.MULTILINE)
# "[0.1.0]: https://..." (a Markdown link reference definition)
LINK_DEFINITION_RE = re.compile(r"^\[[^\]]+\]:[ \t]+\S")
# a list item, or the indented continuation of one
LIST_LINE_RE = re.compile(r"^(\s*[-*+] |\s+\S)")


class BumpError(Exception):
    pass


def bump_version_file(text: str, new_version: str) -> str:
    matches = VERSION_ASSIGNMENT_RE.findall(text)
    if len(matches) != 1:
        raise BumpError("src/version.py must contain exactly one __version__ = '...'")
    return VERSION_ASSIGNMENT_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{new_version}{m.group(2)}", text
    )


def released_versions(text: str) -> list[str]:
    """The versions that have a release section in a changelog."""
    return [match.group("version") for match in RELEASE_HEADING_RE.finditer(text)]


def _is_prerelease_of(version: str, final_version: str) -> bool:
    """Whether version is a beta or rc of final_version (an X.Y.Z release)."""
    key, final_key = parse_version(version), parse_version(final_version)
    return final_key[3] == 2 and key[3] < 2 and key[:3] == final_key[:3]


def _link_definitions_start(text: str) -> int:
    """Where the link reference definitions at the end of text (with "\n" line
    endings) start, or the end of text if there are none."""
    start = len(text)
    for line in reversed(text.splitlines(keepends=True)):
        if line.strip() and not LINK_DEFINITION_RE.match(line):
            break
        start -= len(line)
    # the blank lines before the definitions belong to the last section
    while start < len(text) and text[start] == "\n":
        start += 1
    return start


def _section_end(text: str, body_start: int, links_start: int) -> int:
    """The end of the section whose body starts at body_start."""
    next_heading = SECTION_HEADING_RE.search(text, body_start)
    end = next_heading.start() if next_heading else len(text)
    return min(end, max(links_start, body_start))


def _join_blocks(blocks: list[str]) -> str:
    """Join Markdown blocks, keeping consecutive bullet lists one (tight) list."""
    joined = ""
    for block in blocks:
        block = block.strip("\n")
        if not block.strip():
            continue
        if not joined:
            joined = block
        elif LIST_LINE_RE.match(joined.rsplit("\n", 1)[-1]) and LIST_LINE_RE.match(
            block.split("\n", 1)[0]
        ):
            joined += "\n" + block
        else:
            joined += "\n\n" + block
    return joined


def _merge_sections(bodies: list[str]) -> str:
    """One changelog section body from several (newest first): their text before
    the first ### heading, then each ### heading once, with its entries from
    every section (newest first). The headings keep the order of the oldest
    section, usually the fullest; headings only newer ones have come after."""
    preambles = []
    subsections: dict[str, list[str]] = {}
    for body in reversed(bodies):  # oldest first, for the order of the headings
        for heading in SUBSECTION_HEADING_RE.finditer(body):
            subsections.setdefault(heading.group(0).strip(), [])
    for body in bodies:
        headings = list(SUBSECTION_HEADING_RE.finditer(body))
        preambles.append(body[: headings[0].start()] if headings else body)
        for index, heading in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else None
            subsections[heading.group(0).strip()].append(body[heading.end() : end])
    blocks = [_join_blocks(preambles)]
    for heading, contents in subsections.items():
        content = _join_blocks(contents)
        blocks.append(f"{heading}\n\n{content}" if content else heading)
    merged = "\n\n".join(block for block in blocks if block)
    return f"\n\n{merged}\n\n" if merged else ""


def _update_links(text: str, new_version: str, folded: list[str]) -> str:
    """Point [Unreleased] at the new version and add the new version's link.
    The links of folded pre-releases are removed, and the new version compares
    with the release before them."""
    link = UNRELEASED_LINK_RE.search(text)
    if link is None:
        return text
    base, previous = link.group("base"), link.group("previous")
    new_link = f"[{new_version}]: {base}{previous}...{new_version}"
    if folded:
        oldest = re.escape(folded[-1])
        oldest_link = re.search(
            rf"^\[{oldest}\]:[ \t]*\S+/compare/(?P<previous>\S+?)\.\.\.{oldest}[ \t]*$",
            text,
            re.MULTILINE,
        )
        if oldest_link is not None:
            previous = oldest_link.group("previous")
            new_link = f"[{new_version}]: {base}{previous}...{new_version}"
        else:  # the pre-releases were the first releases
            repository = base[: -len("compare/")]
            new_link = f"[{new_version}]: {repository}releases/tag/{new_version}"
        for version in folded:
            text = re.sub(
                rf"^\[{re.escape(version)}\]:[^\n]*\n?", "", text, flags=re.MULTILINE
            )
        link = UNRELEASED_LINK_RE.search(text)
    links = f"[Unreleased]: {base}{new_version}...HEAD\n{new_link}"
    return text[: link.start()] + links + text[link.end() :]


def bump_changelog(text: str, new_version: str, date: str) -> str:
    if re.search(rf"^## \[{re.escape(new_version)}\]", text, re.MULTILINE):
        raise BumpError(f"CHANGELOG.md already has a '## [{new_version}]' section")
    newline = "\r\n" if "\r\n" in text else "\n"
    text = text.replace("\r\n", "\n")
    heading = UNRELEASED_HEADING_RE.search(text)
    if heading is None:
        raise BumpError("CHANGELOG.md has no '## [Unreleased]' heading")
    links_start = _link_definitions_start(text)
    rest = _section_end(text, heading.end(), links_start)
    body = text[heading.end() : rest]

    # A final X.Y.Z takes over the sections of its pre-releases (the newest
    # releases, right below Unreleased), so that its release notes cover every
    # change since the previous release.
    folded = []
    bodies = [body]
    while (release := RELEASE_HEADING_RE.match(text, rest)) is not None:
        if not _is_prerelease_of(release.group("version"), new_version):
            break
        folded.append(release.group("version"))
        rest = _section_end(text, release.end(), links_start)
        bodies.append(text[release.end() : rest])
    if folded:
        body = _merge_sections(bodies)
    if not body.strip():
        raise BumpError(
            "the '## [Unreleased]' section of CHANGELOG.md is empty; "
            "describe the release's changes there first"
        )
    text = (
        text[: heading.start()]
        + f"## [Unreleased]\n\n## [{new_version}] - {date}"
        + body
        + text[rest:]
    )
    text = _update_links(text, new_version, folded)
    return text.replace("\n", newline)


def _read(path: Path) -> str:
    # newline="" keeps the file's own line endings
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _diff(path: Path, old: str, new: str) -> str:
    name = path.name
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            f"a/{name}",
            f"b/{name}",
        )
    )


def bump(
    new_version: str,
    date: str,
    version_file: Path,
    changelog_file: Path,
    dry_run: bool = False,
) -> str:
    """Apply (or with dry_run, only compute) the release edits; return the diff."""
    try:
        new_key = parse_version(new_version)
        current = read_version(version_file)
    except ValueError as e:
        raise BumpError(str(e)) from e
    try:
        current_key = parse_version(current)
    except ValueError:
        current_key = None  # an unparseable current version can always be replaced
    old_version_text = _read(version_file)
    old_changelog = _read(changelog_file)
    released = released_versions(old_changelog)
    if released:
        latest = max(released, key=parse_version)
        if new_key < parse_version(latest):
            raise BumpError(
                f"{new_version} is older than the latest release {latest} "
                "in CHANGELOG.md"
            )
    # src/version.py holds the version in development: releasing it, or one of
    # its pre-releases, is allowed (bump_changelog refuses a version that
    # already has a section, so nothing can be released twice).
    if (
        current_key is not None
        and new_key < current_key
        and new_key[:3] != current_key[:3]
    ):
        raise BumpError(f"{new_version} is older than the current version {current}")
    try:
        datetime.date.fromisoformat(date)
    except ValueError as e:
        raise BumpError(f"invalid --date {date!r}: {e}") from e

    new_version_text = bump_version_file(old_version_text, new_version)
    new_changelog = bump_changelog(old_changelog, new_version, date)
    if not dry_run:
        _write(version_file, new_version_text)
        _write(changelog_file, new_changelog)
    return _diff(version_file, old_version_text, new_version_text) + _diff(
        changelog_file, old_changelog, new_changelog
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Set src/version.py and move CHANGELOG.md's Unreleased "
        "section to a new release section.",
        epilog="Then: git commit -am 'Release X.Y.Z', git tag -a X.Y.Z -m "
        "'ScoreSight X.Y.Z', git push origin main X.Y.Z",
    )
    parser.add_argument(
        "version", help="new version: X.Y.Z, X.Y.Z-beta.N or X.Y.Z-rc.N"
    )
    parser.add_argument(
        "--date",
        default=datetime.date.today().isoformat(),
        help="release date for the CHANGELOG heading (default: today)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the changes without writing"
    )
    args = parser.parse_args(argv)
    # the diff may contain characters the console's code page can't encode
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")

    try:
        diff = bump(args.version, args.date, VERSION_FILE, CHANGELOG_FILE, args.dry_run)
    except (BumpError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(diff)
    if args.dry_run:
        print("\n(dry run: nothing was written)")
    else:
        print(
            f"\nsrc/version.py and CHANGELOG.md are ready for {args.version}. "
            "Review the changes, then:\n"
            f'  git commit -am "Release {args.version}"\n'
            f'  git tag -a {args.version} -m "ScoreSight {args.version}"\n'
            f"  git push origin main {args.version}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

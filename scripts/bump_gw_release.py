"""Bump 光为 OCR's portable channel version (GW_RELEASE).

Auto-update compares this string with https://ocr.4018.cn/version.json.
It does not use src/version.py (that one is the upstream ScoreSight version).

Publishing GuangWeiOCR-Portable.zip requires both sides to move together:

    python scripts/bump_gw_release.py 2026.10.26.1
    # upload the zip, then publish version.json with the same version,
    # the zip URL, and the zip's SHA-256

If GW_RELEASE and version.json stay the same, installed copies will not
offer an update. `scripts/bump_version.py` only reminds you; it does not
change GW_RELEASE, because the two versions are not the same numbering.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAND_FILE = ROOT / "src" / "product_brand.py"
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gw_auto_update import is_newer  # noqa: E402

ASSIGN_RE = re.compile(
    r"""^(GW_RELEASE\s*=\s*)(["'])([^"']*)(\2)""",
    re.MULTILINE,
)


class BumpError(Exception):
    pass


def read_gw_release(text: str) -> str:
    matches = ASSIGN_RE.findall(text)
    if len(matches) != 1:
        raise BumpError(
            "src/product_brand.py must contain exactly one GW_RELEASE = '...'"
        )
    return matches[0][2]


def bump_text(text: str, new_version: str) -> str:
    current = read_gw_release(text)
    new_version = new_version.strip()
    if not new_version:
        raise BumpError("version is empty")
    if not is_newer(new_version, current):
        raise BumpError(
            f"{new_version} 并不比当前 GW_RELEASE {current} 新。"
            "自动更新只在网站 version.json 比已安装的 GW_RELEASE 新时才会提示。"
            "两边必须一起改。"
        )
    return ASSIGN_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{new_version}{match.group(4)}",
        text,
        count=1,
    )


def version_json_template(version: str) -> str:
    return (
        "{\n"
        f'  "version": "{version}",\n'
        '  "zip_url": "https://ocr.4018.cn/GuangWeiOCR-Portable.zip",\n'
        '  "sha256": "<zip 的 SHA-256，小写十六进制>",\n'
        '  "notes": "",\n'
        '  "released_at": ""\n'
        "}\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Set GW_RELEASE in src/product_brand.py for a portable publish."
    )
    parser.add_argument("version", help="new channel version, e.g. 2026.10.26.1")
    parser.add_argument(
        "--dry-run", action="store_true", help="print what would change, write nothing"
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=BRAND_FILE,
        help="brand file to edit (default: src/product_brand.py)",
    )
    args = parser.parse_args(argv)
    path: Path = args.file
    try:
        old = path.read_text(encoding="utf-8")
        new = bump_text(old, args.version)
    except (BumpError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    version = args.version.strip()
    if args.dry_run:
        print(f"would set GW_RELEASE to {version} in {path}")
    else:
        path.write_text(new, encoding="utf-8")
        print(f"GW_RELEASE is now {version} in {path}")
    print(
        "\n接下来必须把同一个版本写进 https://ocr.4018.cn/version.json ，"
        "并填上刚上传的 zip 的 sha256。\n"
        "GW_RELEASE 与 version.json 的 version 相同，则已安装的用户不会收到更新。\n"
    )
    print(version_json_template(version))
    return 0


if __name__ == "__main__":
    sys.exit(main())

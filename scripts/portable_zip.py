"""Build the 光为 OCR portable zip with real Unicode filenames.

Chinese Windows Explorer (zipfldr) decodes a zip entry name like this:

- general-purpose bit 11 set: the name is UTF-8
- bit 11 clear: the name is the system ANSI code page (GBK / GB18030)

PowerShell 5.1 ``Compress-Archive`` (and .NET Framework's ``ZipArchive``)
writes the name as UTF-8 bytes but leaves bit 11 clear. Explorer then shows
``使用说明.txt`` as ``浣跨敤璇存槑.txt`` and ``启动光为OCR.bat`` as
``鍚姩鍏変负OCR.bat``. The files on the packaging machine can already have
the right names; the damage is done when the zip is written.

This script zips with the stdlib ``zipfile`` module, which sets bit 11 for
any non-ASCII name, and also writes the Info-ZIP Unicode Path extra field
(0x7075) so tools that ignore bit 11 still see the Unicode name. It then
reads the zip back and refuses to finish if those names are missing or the
flag is clear.

GuangWei layout (what https://ocr.4018.cn/ ships), run from the repo root
after PyInstaller::

    python scripts/portable_zip.py dist/scoresight -o dist/GuangWeiOCR-Portable.zip

That stages ``GuangWeiOCR.exe`` (a copy of ``scoresight.exe``; both are kept),
``使用说明.txt`` and ``启动光为OCR.bat``, then zips the folder root.

The ScoreSight-shaped CI archive (a ``scoresight/`` prefix, no Chinese
launch files) is the same writer::

    python scripts/portable_zip.py --layout scoresight dist/scoresight -o dist/scoresight-portable.zip

Check an existing zip without repacking::

    python scripts/portable_zip.py --check dist/GuangWeiOCR-Portable.zip
"""

from __future__ import annotations

import argparse
import shutil
import struct
import sys
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "packaging" / "portable"

README_NAME = "使用说明.txt"
LAUNCHER_NAME = "启动光为OCR.bat"
GUANGWEI_EXE = "GuangWeiOCR.exe"
UPSTREAM_EXE = "scoresight.exe"

# ZIP general-purpose bit 11: language encoding flag (filename is UTF-8).
UTF8_NAME_FLAG = 0x800
# Info-ZIP Unicode Path extra field.
UNICODE_PATH_EXTRA = 0x7075

GUANGWEI_REQUIRED = (
    README_NAME,
    LAUNCHER_NAME,
    GUANGWEI_EXE,
    UPSTREAM_EXE,
)


class PackError(Exception):
    """The portable zip would extract with wrong names, or the inputs are unusable."""


def mojibake_names() -> dict[str, str]:
    """Correct filename -> what Chinese Explorer shows when bit 11 is clear.

    The zip stores UTF-8 bytes. Explorer then decodes them as GB18030.
    ``使用说明.txt`` is the screenshot string ``浣跨敤璇存槑.txt``.
    """
    return {
        name: name.encode("utf-8").decode("gb18030")
        for name in (README_NAME, LAUNCHER_NAME)
    }


@dataclass(frozen=True)
class ZipMember:
    """One entry as stored, not as a later tool might reinterpret it."""

    filename: str
    raw_name: bytes
    local_flags: int
    central_flags: int
    unicode_extra: str | None


def _strip_extra(extra: bytes, tag: int) -> bytes:
    out = bytearray()
    index = 0
    while index + 4 <= len(extra):
        found, length = struct.unpack_from("<HH", extra, index)
        end = index + 4 + length
        if end > len(extra):
            break
        if found != tag:
            out += extra[index:end]
        index = end
    if index < len(extra):
        out += extra[index:]
    return bytes(out)


def _unicode_path_extra(filename: str) -> bytes:
    """Info-ZIP Unicode Path (0x7075), version 1.

    The CRC covers the filename bytes stored in the zip header. Those bytes
    are the UTF-8 of ``filename`` (ASCII names are the same bytes).
    """
    raw = filename.encode("utf-8")
    crc = zlib.crc32(raw) & 0xFFFFFFFF
    payload = struct.pack("<BL", 1, crc) + raw
    return struct.pack("<HH", UNICODE_PATH_EXTRA, len(payload)) + payload


def _unicode_path_from_extra(extra: bytes, raw_name: bytes) -> str | None:
    index = 0
    while index + 4 <= len(extra):
        tag, length = struct.unpack_from("<HH", extra, index)
        end = index + 4 + length
        if end > len(extra):
            return None
        data = extra[index + 4 : end]
        index = end
        if tag != UNICODE_PATH_EXTRA or len(data) < 5:
            continue
        version, crc = struct.unpack_from("<BL", data)
        if version != 1:
            continue
        if crc != (zlib.crc32(raw_name) & 0xFFFFFFFF):
            continue
        return data[5:].decode("utf-8")
    return None


def _prepare_info(path: Path, arcname: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo.from_file(path, arcname)
    # from_file on Windows normalizes to backslashes; the zip spec wants "/".
    info.filename = info.filename.replace("\\", "/")
    if path.is_dir() and not info.filename.endswith("/"):
        info.filename += "/"
    info.orig_filename = info.filename
    # from_file() leaves CRC unset. ZipInfo stores it in a slot, and
    # FileHeader reads it while writing a directory entry.
    info.CRC = 0
    info.extra = _strip_extra(info.extra, UNICODE_PATH_EXTRA) + _unicode_path_extra(
        info.filename
    )
    if not info.is_dir():
        info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _local_header(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo
) -> tuple[int, bytes, bytes]:
    """Return (flags, raw filename, extra) from the local file header."""
    zf.fp.seek(info.header_offset)
    header = zf.fp.read(30)
    if len(header) != 30 or header[:4] != b"PK\x03\x04":
        raise PackError(f"zip 本地文件头损坏: {info.filename}")
    (
        _sig,
        _version,
        flags,
        _method,
        _time,
        _date,
        _crc,
        _csize,
        _usize,
        name_len,
        extra_len,
    ) = struct.unpack("<4sHHHHHIIIHH", header)
    raw = zf.fp.read(name_len)
    extra = zf.fp.read(extra_len)
    if len(raw) != name_len or len(extra) != extra_len:
        raise PackError(f"zip 本地文件头被截断: {info.filename}")
    return flags, raw, extra


def read_zip_members(zip_path: Path) -> list[ZipMember]:
    """Read stored names and the UTF-8 flag from a zip on disk."""
    members: list[ZipMember] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            local_flags, raw, extra = _local_header(zf, info)
            unicode_extra = _unicode_path_from_extra(extra, raw)
            try:
                if local_flags & UTF8_NAME_FLAG:
                    filename = raw.decode("utf-8")
                else:
                    filename = raw.decode("cp437")
            except UnicodeDecodeError as exc:
                raise PackError(f"zip 文件名无法解码: {raw!r}") from exc
            # Python may also apply 0x7075 while building ZipInfo. The bytes
            # above are what Explorer reads; prefer those over info.filename
            # when they disagree, but a valid archive has them equal.
            members.append(
                ZipMember(
                    filename=filename,
                    raw_name=raw,
                    local_flags=local_flags,
                    central_flags=info.flag_bits,
                    unicode_extra=unicode_extra,
                )
            )
    return members


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def write_portable_zip(source: Path, zip_path: Path, *, prefix: str = "") -> None:
    """Zip ``source`` so every non-ASCII name has the UTF-8 flag and 0x7075.

    ``prefix`` is a single top-level folder (``scoresight``) or empty, in
    which case the children of ``source`` sit at the zip root. That is the
    GuangWei layout: Explorer's Extract All creates one folder named after
    the zip, with ``使用说明.txt`` directly inside it.
    """
    source = source.resolve()
    zip_path = zip_path.resolve()
    if not source.is_dir():
        raise PackError(f"不是文件夹: {source}")
    if _is_inside(zip_path, source):
        raise PackError(f"zip 不能写在被打包的目录里: {zip_path}")
    prefix = prefix.strip("/")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    partial = zip_path.with_name(zip_path.name + ".partial")
    if partial.exists():
        partial.unlink()

    def add(zf: zipfile.ZipFile, path: Path, arcname: str) -> None:
        info = _prepare_info(path, arcname)
        if path.is_dir():
            zf.mkdir(info)
            return
        with path.open("rb") as src, zf.open(info, "w") as dest:
            shutil.copyfileobj(src, dest, 1024 * 1024)

    try:
        with zipfile.ZipFile(
            partial, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as zf:
            if prefix:
                add(zf, source, prefix)
            paths = sorted(source.rglob("*"), key=lambda item: item.as_posix())
            for path in paths:
                rel = path.relative_to(source).as_posix()
                arcname = f"{prefix}/{rel}" if prefix else rel
                add(zf, path, arcname)
        partial.replace(zip_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def verify_zip(zip_path: Path, required: tuple[str, ...]) -> list[ZipMember]:
    """Fail if Explorer would garble a name, or a required name is absent."""
    if not zip_path.is_file():
        raise PackError(f"找不到 zip: {zip_path}")
    try:
        members = read_zip_members(zip_path)
    except zipfile.BadZipFile as exc:
        raise PackError(f"不是合法的 zip: {zip_path}") from exc

    bad = set(mojibake_names().values())
    present = {member.filename.rstrip("/") for member in members}
    for member in members:
        base = member.filename.rstrip("/").rsplit("/", 1)[-1]
        if base in bad or member.filename in bad:
            shown = member.filename
            raise PackError(
                f"zip 里仍是乱码文件名 {shown!r}。"
                "这是 UTF-8 被当成 GBK 之后的结果，不能发布。"
            )
        non_ascii = any(ord(ch) > 127 for ch in member.filename)
        if not non_ascii:
            continue
        local_utf8 = bool(member.local_flags & UTF8_NAME_FLAG)
        central_utf8 = bool(member.central_flags & UTF8_NAME_FLAG)
        if not local_utf8 or not central_utf8:
            garbled = member.raw_name.decode("gb18030", errors="replace")
            raise PackError(
                f"{member.filename!r} 没有 ZIP UTF-8 标志（bit 11）。"
                f"中文资源管理器会把它显示成 {garbled!r}。"
                "不要用 Compress-Archive 打包。"
            )
        if member.raw_name != member.filename.encode("utf-8"):
            raise PackError(f"{member.filename!r} 的文件名字节不是 UTF-8")
        if member.unicode_extra != member.filename:
            raise PackError(
                f"{member.filename!r} 缺少 Info-ZIP Unicode 路径字段（0x7075），"
                "或其中的文件名与头里的 UTF-8 不一致"
            )

    missing = [name for name in required if name not in present]
    if missing:
        roots = sorted({member.filename.split("/", 1)[0] for member in members})
        raise PackError(f"zip 缺少 {missing!r}。根上的条目: {', '.join(roots[:40])}")
    return members


def _purge_mojibake(root: Path) -> None:
    """Drop staged files whose names are already the Explorer misread.

    A packaging folder that was itself unzipped by Explorer can contain
    ``浣跨敤璇存槑.txt`` on disk. Zipping that name faithfully would publish
    the garbage. The canonical assets are copied afterwards under the real
    Unicode names.
    """
    bad = set(mojibake_names().values())
    stale = [path for path in root.rglob("*") if path.name in bad]
    for path in stale:
        if path.is_file() or path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def _require_asset(name: str) -> Path:
    path = ASSETS / name
    if not path.is_file() or path.name != name:
        found: list[str] = []
        if ASSETS.is_dir():
            found = sorted(child.name for child in ASSETS.iterdir())
        raise PackError(
            f"仓库里缺少 {name!r}（应在 {ASSETS}）。当前文件: {found}。"
            "不要把使用说明或启动脚本存成乱码文件名。"
        )
    return path


def _copy_exe(src: Path, dest: Path) -> None:
    if dest.exists():
        return
    if not src.is_file():
        raise PackError(f"找不到 {src.name}，无法复制出 {dest.name}")
    shutil.copy2(src, dest)


def stage_guangwei_tree(onedir: Path, dest: Path) -> Path:
    """Copy a PyInstaller onedir and add the GuangWei root files.

    ``scoresight.exe`` and ``GuangWeiOCR.exe`` are both kept. English exe
    names stay English. Chinese names are the two root files from
    ``packaging/portable/``, copied by Unicode path, not by the system code
    page.
    """
    onedir = onedir.resolve()
    dest = dest.resolve()
    if not onedir.is_dir():
        raise PackError(f"不是文件夹: {onedir}")
    if dest == onedir or _is_inside(dest, onedir) or _is_inside(onedir, dest):
        raise PackError("暂存目录不能是 PyInstaller 目录本身，也不能互相包含")
    has_upstream = (onedir / UPSTREAM_EXE).is_file()
    has_guangwei = (onedir / GUANGWEI_EXE).is_file()
    if not has_upstream and not has_guangwei:
        raise PackError(f"{onedir} 里没有 {UPSTREAM_EXE} 或 {GUANGWEI_EXE}")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(onedir, dest)
    _purge_mojibake(dest)
    _copy_exe(dest / UPSTREAM_EXE, dest / GUANGWEI_EXE)
    _copy_exe(dest / GUANGWEI_EXE, dest / UPSTREAM_EXE)
    for name in (README_NAME, LAUNCHER_NAME):
        shutil.copy2(_require_asset(name), dest / name)
    names = {path.name for path in dest.iterdir()}
    missing = [name for name in GUANGWEI_REQUIRED if name not in names]
    if missing:
        raise PackError(f"暂存目录缺少 {missing!r}: {dest}")
    leaked = names.intersection(mojibake_names().values())
    if leaked:
        raise PackError(f"暂存目录仍有乱码文件名 {sorted(leaked)!r}")
    return dest


def pack_guangwei_portable(
    onedir: Path, zip_path: Path, *, stage: Path | None = None
) -> Path:
    """Stage the download-site layout and zip it with UTF-8 filenames."""
    zip_path = Path(zip_path)
    if stage is None:
        with tempfile.TemporaryDirectory(prefix="gw-portable-") as tmp:
            staged = stage_guangwei_tree(onedir, Path(tmp) / "GuangWeiOCR")
            write_portable_zip(staged, zip_path)
    else:
        staged = stage_guangwei_tree(onedir, stage)
        write_portable_zip(staged, zip_path)
    verify_zip(zip_path, GUANGWEI_REQUIRED)
    return zip_path


def pack_scoresight_portable(onedir: Path, zip_path: Path) -> Path:
    """Zip ``dist/scoresight`` as ``scoresight/...`` without renaming exes.

    Same UTF-8 rules as the GuangWei zip. This is the ScoreSight-shaped
    archive CI attaches to a GitHub release, not the ocr.4018.cn package.
    """
    onedir = Path(onedir)
    if not (onedir / UPSTREAM_EXE).is_file():
        raise PackError(f"{onedir} 里没有 {UPSTREAM_EXE}")
    write_portable_zip(onedir, zip_path, prefix="scoresight")
    verify_zip(zip_path, ("scoresight/scoresight.exe",))
    return zip_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Zip a portable build with the ZIP UTF-8 filename flag (bit 11) "
            "and the Info-ZIP Unicode Path extra field. "
            "Compress-Archive on Windows PowerShell 5.1 stores UTF-8 names "
            "without that flag; Chinese Explorer then shows mojibake."
        )
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="PyInstaller onedir, usually dist/scoresight",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Destination zip path",
    )
    parser.add_argument(
        "--layout",
        choices=("guangwei", "scoresight"),
        default="guangwei",
        help=(
            "guangwei: root entries, GuangWeiOCR.exe, 使用说明.txt, "
            "启动光为OCR.bat (default). "
            "scoresight: scoresight/ prefix, no extra Chinese launch files."
        ),
    )
    parser.add_argument(
        "--stage",
        help="Write the staged GuangWei folder here instead of a temp directory",
    )
    parser.add_argument(
        "--check",
        metavar="ZIP",
        help="Only check an existing GuangWei zip; do not pack",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.check:
            verify_zip(Path(args.check), GUANGWEI_REQUIRED)
            print(f"zip ok: {README_NAME}, {LAUNCHER_NAME}")
            return 0
        if not args.source or not args.output:
            parser.error("source and -o are required unless --check is set")
        source = Path(args.source)
        output = Path(args.output)
        if args.layout == "scoresight":
            pack_scoresight_portable(source, output)
            print(f"zip ok: {output}")
        else:
            stage = Path(args.stage) if args.stage else None
            pack_guangwei_portable(source, output, stage=stage)
            print(f"zip ok: {README_NAME}, {LAUNCHER_NAME}")
        return 0
    except PackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Portable zip filenames must survive Chinese Windows Explorer.

Explorer decodes a name as UTF-8 only when general-purpose bit 11 is set.
Without that flag it uses GBK, so the UTF-8 bytes of 使用说明.txt display
as 浣跨敤璇存槑.txt. PowerShell 5.1 Compress-Archive writes the zip that way.
"""

import importlib.util
import struct
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "portable_zip.py"
ASSETS = REPO_ROOT / "packaging" / "portable"


def _load():
    spec = importlib.util.spec_from_file_location("portable_zip", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # dataclass() looks the class's module up in sys.modules while the body runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


portable_zip = _load()


def _onedir(tmp_path):
    root = tmp_path / "scoresight"
    internal = root / "_internal"
    internal.mkdir(parents=True)
    (internal / "base_library.zip").write_bytes(b"not-a-real-zip")
    (root / "scoresight.exe").write_bytes(b"MZ-fake-scoresight")
    return root


def _members(zip_path):
    found = {
        member.filename: member for member in portable_zip.read_zip_members(zip_path)
    }
    return found


def _assert_unicode_name(member, expected):
    assert member.filename == expected
    assert member.raw_name == expected.encode("utf-8")
    assert member.local_flags & portable_zip.UTF8_NAME_FLAG
    assert member.central_flags & portable_zip.UTF8_NAME_FLAG
    assert member.unicode_extra == expected
    # The bytes Explorer would misuse if bit 11 were missing.
    assert member.raw_name.decode("gb18030") == portable_zip.mojibake_names()[expected]


def test_screenshot_mojibake_is_utf8_read_as_gb18030():
    garbled = portable_zip.mojibake_names()
    assert garbled["使用说明.txt"] == "浣跨敤璇存槑.txt"
    assert garbled["启动光为OCR.bat"] == "鍚姩鍏変负OCR.bat"


def test_asset_files_use_unicode_names():
    names = {path.name for path in ASSETS.iterdir()}
    assert portable_zip.README_NAME in names
    assert portable_zip.LAUNCHER_NAME in names
    assert "浣跨敤璇存槑.txt" not in names
    readme = (ASSETS / portable_zip.README_NAME).read_bytes()
    assert readme.startswith(b"\xef\xbb\xbf")
    assert "光为 OCR" in readme.decode("utf-8-sig")
    launcher = (ASSETS / portable_zip.LAUNCHER_NAME).read_bytes()
    assert launcher.startswith(b"@echo off\r\n")
    assert b"GuangWeiOCR.exe" in launcher
    assert b"scoresight.exe" in launcher
    assert b'cd /d "%~dp0"' in launcher


def test_guangwei_zip_keeps_unicode_root_names(tmp_path):
    onedir = _onedir(tmp_path)
    # A previous Explorer extract left the mojibake name on disk, plus a
    # stale copy of the real name. Neither may win over the repo asset.
    stale = "stale-readme".encode("utf-8")
    (onedir / "浣跨敤璇存槑.txt").write_bytes(stale)
    (onedir / "使用说明.txt").write_bytes(stale)
    zip_path = tmp_path / "GuangWeiOCR-Portable.zip"
    portable_zip.pack_guangwei_portable(onedir, zip_path)

    members = _members(zip_path)
    _assert_unicode_name(members["使用说明.txt"], "使用说明.txt")
    _assert_unicode_name(members["启动光为OCR.bat"], "启动光为OCR.bat")
    assert "浣跨敤璇存槑.txt" not in members
    assert "鍚姩鍏変负OCR.bat" not in members
    assert members["GuangWeiOCR.exe"].raw_name == b"GuangWeiOCR.exe"
    assert members["scoresight.exe"].raw_name == b"scoresight.exe"
    assert "scoresight.exe" in members
    assert "_internal/base_library.zip" in members

    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        assert zf.read("使用说明.txt") == (ASSETS / "使用说明.txt").read_bytes()
        assert zf.read("启动光为OCR.bat") == (ASSETS / "启动光为OCR.bat").read_bytes()
        assert zf.read("GuangWeiOCR.exe") == b"MZ-fake-scoresight"
        assert zf.read("scoresight.exe") == b"MZ-fake-scoresight"
        extracted = tmp_path / "out"
        zf.extractall(extracted)
    assert (extracted / "使用说明.txt").is_file()
    assert (extracted / "启动光为OCR.bat").is_file()
    assert not (extracted / "浣跨敤璇存槑.txt").exists()


def test_clearing_the_utf8_flag_is_the_explorer_bug(tmp_path):
    onedir = _onedir(tmp_path)
    zip_path = tmp_path / "broken.zip"
    portable_zip.pack_guangwei_portable(onedir, zip_path)
    _clear_utf8_flag(zip_path)

    raw_readme = "使用说明.txt".encode("utf-8")
    members = portable_zip.read_zip_members(zip_path)
    readme = next(member for member in members if member.raw_name == raw_readme)
    assert not (readme.local_flags & portable_zip.UTF8_NAME_FLAG)
    assert not (readme.central_flags & portable_zip.UTF8_NAME_FLAG)
    assert readme.raw_name.decode("gb18030") == "浣跨敤璇存槑.txt"
    with pytest.raises(portable_zip.PackError, match="UTF-8"):
        portable_zip.verify_zip(zip_path, portable_zip.GUANGWEI_REQUIRED)


def test_scoresight_layout_prefixes_and_still_flags_unicode(tmp_path):
    onedir = _onedir(tmp_path)
    notes = onedir / "notes"
    notes.mkdir()
    (notes / "使用说明.txt").write_text("inside", encoding="utf-8")
    zip_path = tmp_path / "scoresight-portable.zip"
    portable_zip.pack_scoresight_portable(onedir, zip_path)

    members = _members(zip_path)
    assert "scoresight/scoresight.exe" in members
    assert "GuangWeiOCR.exe" not in members
    assert "使用说明.txt" not in members
    nested = members["scoresight/notes/使用说明.txt"]
    assert nested.raw_name == "scoresight/notes/使用说明.txt".encode("utf-8")
    assert nested.local_flags & portable_zip.UTF8_NAME_FLAG
    assert nested.central_flags & portable_zip.UTF8_NAME_FLAG
    assert nested.unicode_extra == nested.filename


def test_check_cli_accepts_a_guangwei_zip_and_rejects_a_plain_one(tmp_path):
    onedir = _onedir(tmp_path)
    good = tmp_path / "good.zip"
    plain = tmp_path / "plain.zip"
    portable_zip.pack_scoresight_portable(onedir, plain)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(onedir), "-o", str(good)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "使用说明.txt" in completed.stdout
    assert "启动光为OCR.bat" in completed.stdout

    checked = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", str(good)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stderr

    rejected = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", str(plain)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 1
    assert "缺少" in rejected.stderr

    missing = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "nope"), "-o", str(good)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 1


def test_workflows_zip_with_the_utf8_writer():
    for rel in (
        ".github/workflows/build.yml",
        ".github/workflows/windows-portable.yml",
    ):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            assert "Compress-Archive" not in code, (rel, line)
        assert "scripts/portable_zip.py" in text, rel
    portable = (REPO_ROOT / ".github/workflows/windows-portable.yml").read_text(
        encoding="utf-8"
    )
    assert "GuangWeiOCR-Portable.zip" in portable
    build = (REPO_ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    assert "--layout scoresight" in build
    assert "windows-x64-portable.zip" in build


def _clear_utf8_flag(path: Path) -> None:
    """Clear bit 11 in local and central headers. Leave the UTF-8 bytes."""
    data = bytearray(path.read_bytes())

    def clear_at(offset: int) -> None:
        flags = int.from_bytes(data[offset : offset + 2], "little")
        data[offset : offset + 2] = (flags & ~portable_zip.UTF8_NAME_FLAG).to_bytes(
            2, "little"
        )

    pos = 0
    while pos + 30 <= len(data) and data[pos : pos + 4] == b"PK\x03\x04":
        name_len, extra_len = struct.unpack_from("<HH", data, pos + 26)
        compressed = int.from_bytes(data[pos + 18 : pos + 22], "little")
        clear_at(pos + 6)
        pos += 30 + name_len + extra_len + compressed

    eocd = data.rfind(b"PK\x05\x06")
    cd_size, cd_offset = struct.unpack_from("<II", data, eocd + 12)
    pos = cd_offset
    end = cd_offset + cd_size
    while pos + 46 <= end and data[pos : pos + 4] == b"PK\x01\x02":
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", data, pos + 28)
        clear_at(pos + 8)
        pos += 46 + name_len + extra_len + comment_len
    path.write_bytes(data)

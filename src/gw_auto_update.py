"""光为 OCR channel auto-update (ocr.4018.cn).

Portable builds check version.json on every startup. When a newer release is
available, download the zip in the background and apply on quit via a bat
helper (the exe is locked while running). Source/dev runs open the download
page only. Upstream ScoreSight's GitHub updater is used only when
SIMPLIFY_OUTPUT_UI is False.

A portable release is invisible to installed copies until GW_RELEASE in
product_brand.py and the site's version.json both move to a newer string.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from product_brand import (
    GW_DOWNLOAD_PAGE,
    GW_RELEASE,
    GW_VERSION_JSON_URLS,
    SIMPLIFY_OUTPUT_UI,
)
from sc_logging import logger

# (connect, read) seconds. The read timeout is between chunks, so a large zip
# can take as long as it needs while data is still arriving.
VERSION_FETCH_TIMEOUT = (5.0, 15.0)
DOWNLOAD_TIMEOUT = (10.0, 60.0)
VERSION_FETCH_ATTEMPTS = 3
DOWNLOAD_ATTEMPTS = 3
VERSION_FETCH_BACKOFF_S = (0.5, 1.0)
DOWNLOAD_BACKOFF_S = (1.0, 2.0)
DOWNLOAD_CHUNK = 256 * 1024
# A portable build is about 170 MB compressed; refuse a manifest that would
# unpack to something absurd.
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
STAGE_DIR_NAME = "_gw_update_stage"

UA = f"GuangWeiOCR/{GW_RELEASE}"

STATUS_CHECK_FAILED = "更新检查失败"
CHECK_FAILED_TEXT = "更新检查失败。请检查网络后重试。\n\n也可打开下载站手动获取新版本。"


class DownloadCancelled(Exception):
    """The user cancelled the download. Partial files are removed."""


@dataclass
class GwReleaseInfo:
    version: str
    zip_url: str
    sha256: str
    notes: str
    released_at: str


@dataclass
class GwUpdateResult:
    current: str
    latest: str | None = None
    update_available: bool = False
    info: GwReleaseInfo | None = None
    # "network", "manifest", "not guangwei channel", or None when the check worked
    error: str | None = None
    error_detail: str | None = None
    # set after a successful background download
    staged_dir: str | None = None


class CancelToken:
    """Set from the UI thread; the download thread notices between chunks.

    Closing the response unblocks a stalled read so cancel does not wait for
    the read timeout.
    """

    def __init__(self):
        self._flag = threading.Event()
        self._response = None
        self._lock = threading.Lock()

    def cancelled(self) -> bool:
        return self._flag.is_set()

    def cancel(self) -> None:
        self._flag.set()
        with self._lock:
            resp = self._response
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    def attach(self, resp) -> None:
        with self._lock:
            self._response = resp
        if self.cancelled():
            try:
                resp.close()
            except Exception:
                pass

    def detach(self) -> None:
        with self._lock:
            self._response = None


# One aboutToQuit / atexit hook per process. Later schedules only refresh the bat.
_apply_state = {"hooked": False, "bat": None}


def reset_apply_hook_for_tests() -> None:
    _apply_state["hooked"] = False
    _apply_state["bat"] = None


def is_guangwei_channel() -> bool:
    return bool(SIMPLIFY_OUTPUT_UI)


def is_frozen_portable() -> bool:
    return bool(getattr(sys, "frozen", False))


def describe_check(result: GwUpdateResult) -> str:
    """'failed', 'available', 'current', or 'ignored' (not the 光为 channel)."""
    if result.error == "not guangwei channel":
        return "ignored"
    if result.error:
        return "failed"
    if result.update_available and result.info is not None:
        return "available"
    return "current"


def up_to_date_text(version: str) -> str:
    return f"当前已是最新版本（{version}）。"


def _parse_loose_version(text: str) -> tuple:
    """Compare GW versions: prefer dotted numeric (0.4.2 / 2026.10.25.1)."""
    parts: list = []
    for chunk in str(text).strip().lstrip("vV").replace("-", ".").split("."):
        if not chunk:
            continue
        if chunk.isdigit():
            parts.append((0, int(chunk)))
        else:
            # split trailing digits e.g. gw1
            num = ""
            alpha = ""
            for ch in chunk:
                if ch.isdigit() and not alpha:
                    num += ch
                else:
                    alpha += ch
            if num:
                parts.append((0, int(num)))
            if alpha:
                parts.append((1, alpha))
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    """True when candidate is a strictly newer channel version than current.

    Equal strings are not newer. That is why a portable publish must bump
    GW_RELEASE and version.json together: if they match, nobody is offered
    an update.
    """
    candidate = str(candidate or "").strip()
    current = str(current or "").strip()
    if not candidate or candidate == current:
        return False
    try:
        return _parse_loose_version(candidate) > _parse_loose_version(current)
    except Exception:
        return False


def _valid_sha256(text: str) -> bool:
    sha = str(text or "").strip().lower()
    return len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)


def parse_version_payload(data) -> GwReleaseInfo:
    """Validate one version.json object. Raises ValueError when it can't be used."""
    if not isinstance(data, dict):
        raise ValueError("version.json 不是对象")
    version = str(data.get("version") or "").strip()
    zip_url = str(data.get("zip_url") or "").strip()
    sha = str(data.get("sha256") or "").strip().lower()
    if not version or not zip_url:
        raise ValueError("version.json 缺少 version 或 zip_url")
    if not zip_url.lower().startswith(("https://", "http://")):
        raise ValueError("zip_url 无效")
    if not _valid_sha256(sha):
        raise ValueError("version.json 缺少有效的 sha256")
    return GwReleaseInfo(
        version=version,
        zip_url=zip_url,
        sha256=sha,
        notes=str(data.get("notes") or ""),
        released_at=str(data.get("released_at") or ""),
    )


def _http_get(url, **kwargs):
    return requests.get(url, **kwargs)


def _backoff(seconds: float, cancel: CancelToken | None = None) -> None:
    if cancel is not None and cancel.cancelled():
        raise DownloadCancelled()
    if seconds > 0:
        time.sleep(seconds)
    if cancel is not None and cancel.cancelled():
        raise DownloadCancelled()


def fetch_version_json(
    *,
    attempts: int = VERSION_FETCH_ATTEMPTS,
) -> tuple[GwReleaseInfo | None, str | None, str | None]:
    """GET version.json. Returns (info, error_code, detail).

    Each attempt tries every mirror in GW_VERSION_JSON_URLS. error_code is
    "network" or "manifest" when nothing usable comes back.
    """
    detail = ""
    saw_invalid = False
    headers = {"User-Agent": UA, "Accept": "application/json"}
    for attempt in range(max(1, attempts)):
        for url in GW_VERSION_JSON_URLS:
            try:
                resp = _http_get(url, timeout=VERSION_FETCH_TIMEOUT, headers=headers)
            except Exception as exc:
                detail = f"{url}: {exc}"
                logger.info("GW update: fetch failed %s: %s", url, exc)
                continue
            try:
                status = int(getattr(resp, "status_code", 0) or 0)
                if status == 404:
                    detail = f"{url}: 404"
                    logger.info("GW update: %s -> 404", url)
                    continue
                if status != 200:
                    detail = f"{url}: HTTP {status}"
                    logger.info("GW update: %s", detail)
                    continue
                try:
                    data = resp.json()
                except Exception as exc:
                    saw_invalid = True
                    detail = f"{url}: {exc}"
                    logger.info("GW update: bad JSON %s: %s", url, exc)
                    continue
                try:
                    info = parse_version_payload(data)
                except ValueError as exc:
                    saw_invalid = True
                    detail = f"{url}: {exc}"
                    logger.info("GW update: unusable manifest %s: %s", url, exc)
                    continue
                logger.info("GW update: manifest %s from %s", info.version, url)
                return info, None, None
            finally:
                close = getattr(resp, "close", None)
                if close is not None:
                    try:
                        close()
                    except Exception:
                        pass
        if attempt + 1 < attempts:
            wait = VERSION_FETCH_BACKOFF_S[
                min(attempt, len(VERSION_FETCH_BACKOFF_S) - 1)
            ]
            logger.info("GW update: retrying version check in %.1fs", wait)
            _backoff(wait)
    code = "manifest" if saw_invalid else "network"
    logger.warning("GW update check failed (%s): %s", code, detail)
    return None, code, detail


def check_gw_update() -> GwUpdateResult:
    """Blocking check. Never raises.

    Equal versions are not an update (see is_newer). Network and manifest
    failures set error so a manual check can say so; startup stays quiet in
    the UI and only logs.
    """
    current = GW_RELEASE
    if not is_guangwei_channel():
        return GwUpdateResult(current=current, error="not guangwei channel")
    info, error, detail = fetch_version_json()
    if info is None:
        return GwUpdateResult(
            current=current,
            error=error or "network",
            error_detail=detail,
        )
    if not is_newer(info.version, current):
        logger.info(
            "GW update check: installed %s, site %s, no update",
            current,
            info.version,
        )
        return GwUpdateResult(current=current, latest=info.version, info=info)
    logger.info(
        "GW update check: installed %s, site %s, update available",
        current,
        info.version,
    )
    return GwUpdateResult(
        current=current,
        latest=info.version,
        update_available=True,
        info=info,
    )


def _portable_root() -> Path:
    if is_frozen_portable():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _stage_root() -> Path:
    root = _portable_root() / STAGE_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def cleanup_stage(stage: Path | None = None) -> None:
    """Remove the whole staging directory. Refuses any other path."""
    if stage is None:
        stage = _portable_root() / STAGE_DIR_NAME
    stage = stage.resolve()
    if stage.name != STAGE_DIR_NAME:
        raise RuntimeError(f"refuse to remove {stage}")
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)


def _discard_incoming(stage: Path) -> None:
    """Drop the in-progress zip and extract. A previously staged payload stays."""
    incoming = stage / "incoming"
    if incoming.exists():
        shutil.rmtree(incoming, ignore_errors=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _header_int(headers, name: str) -> int:
    if not headers:
        return 0
    raw = headers.get(name) if hasattr(headers, "get") else None
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _download_once(
    url: str,
    dest: Path,
    progress: Callable[[int, int], None] | None,
    cancel: CancelToken | None,
) -> None:
    """Download url to dest. A partial dest is resumed with Range when the server allows."""
    if cancel is not None and cancel.cancelled():
        raise DownloadCancelled()
    existing = dest.stat().st_size if dest.is_file() else 0
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if existing > 0:
        headers["Range"] = f"bytes={existing}-"
    resp = _http_get(url, stream=True, timeout=DOWNLOAD_TIMEOUT, headers=headers)
    if cancel is not None:
        cancel.attach(resp)
    try:
        if cancel is not None and cancel.cancelled():
            raise DownloadCancelled()
        status = int(getattr(resp, "status_code", 0) or 0)
        if status == 416 and existing > 0:
            # The local file is already at least as long as the remote object.
            return
        if status not in (200, 206):
            resp.raise_for_status()
            raise RuntimeError(f"HTTP {status} downloading {url}")
        resume = status == 206 and existing > 0
        length = _header_int(getattr(resp, "headers", None), "Content-Length")
        if resume:
            done = existing
            total = existing + length if length else 0
            mode = "ab"
        else:
            done = 0
            total = length
            mode = "wb"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(dest, mode) as fh:
                for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK):
                    if cancel is not None and cancel.cancelled():
                        raise DownloadCancelled()
                    if not chunk:
                        continue
                    fh.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
        except DownloadCancelled:
            raise
        except Exception:
            if cancel is not None and cancel.cancelled():
                raise DownloadCancelled() from None
            raise
    finally:
        if cancel is not None:
            cancel.detach()
        close = getattr(resp, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                pass


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    total = 0
    for info in zf.infolist():
        name = str(info.filename).replace("\\", "/")
        if not name or name.endswith("/"):
            continue
        parts = Path(name).parts
        if name.startswith("/") or any(part == ".." for part in parts):
            raise RuntimeError(f"压缩包路径不安全: {info.filename}")
        target = (root / name).resolve()
        if target != root and root not in target.parents:
            raise RuntimeError(f"压缩包路径不安全: {info.filename}")
        total += max(0, int(getattr(info, "file_size", 0) or 0))
        if total > MAX_UNCOMPRESSED_BYTES:
            raise RuntimeError("压缩包解压后过大")
    zf.extractall(root)


def _extract_payload(
    stage: Path, zip_path: Path, info: GwReleaseInfo, digest: str
) -> Path:
    incoming = stage / "incoming"
    extract_dir = incoming / "extracted"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        _safe_extract(zf, extract_dir)
    children = [
        p for p in extract_dir.iterdir() if p.name not in {"__MACOSX", ".DS_Store"}
    ]
    inner = children[0] if len(children) == 1 and children[0].is_dir() else extract_dir
    final = stage / "payload"
    if final.exists():
        shutil.rmtree(final)
    shutil.move(str(inner), str(final))
    _discard_incoming(stage)
    (stage / "READY.txt").write_text(
        json.dumps(
            {
                "version": info.version,
                "payload": str(final),
                "sha256": digest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return final


def download_and_stage(
    info: GwReleaseInfo,
    progress: Callable[[int, int], None] | None = None,
    cancel: CancelToken | None = None,
    on_phase: Callable[[str], None] | None = None,
    attempts: int | None = None,
) -> Path:
    """Download the zip, require sha256, extract to the stage dir.

    Retries keep a partial file and resume it. Cancel and a final failure
    delete the partial download. A payload already committed by an earlier
    successful download is left in place until a new one is ready.
    Development runs refuse to download so the source tree is never overwritten.
    """
    if not is_frozen_portable():
        raise RuntimeError("开发版不下载更新包，请打开下载站")
    if not _valid_sha256(info.sha256 or ""):
        raise RuntimeError("更新信息缺少 sha256，已拒绝下载")
    stage = _stage_root()
    incoming = stage / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    zip_path = incoming / "GuangWeiOCR-Portable.zip"
    meta_path = incoming / "partial.json"
    expected_meta = {
        "version": info.version,
        "zip_url": info.zip_url,
        "sha256": info.sha256.lower(),
    }
    previous = None
    if meta_path.is_file():
        try:
            previous = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            previous = None
    if previous != expected_meta:
        _discard_incoming(stage)
        incoming.mkdir(parents=True, exist_ok=True)
        zip_path = incoming / "GuangWeiOCR-Portable.zip"
        meta_path = incoming / "partial.json"
    meta_path.write_text(json.dumps(expected_meta), encoding="utf-8")

    tries = DOWNLOAD_ATTEMPTS if attempts is None else max(1, attempts)
    last_exc: Exception | None = None
    for attempt in range(tries):
        if cancel is not None and cancel.cancelled():
            _discard_incoming(stage)
            raise DownloadCancelled()
        try:
            if on_phase:
                on_phase("download")
            _download_once(info.zip_url, zip_path, progress, cancel)
            if cancel is not None and cancel.cancelled():
                _discard_incoming(stage)
                raise DownloadCancelled()
            if on_phase:
                on_phase("verify")
            digest = _sha256_file(zip_path)
            if digest != info.sha256.lower():
                zip_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"SHA-256 校验失败（得到 {digest}，期望 {info.sha256.lower()}）"
                )
            if on_phase:
                on_phase("extract")
            if cancel is not None and cancel.cancelled():
                _discard_incoming(stage)
                raise DownloadCancelled()
            return _extract_payload(stage, zip_path, info, digest)
        except DownloadCancelled:
            _discard_incoming(stage)
            raise
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "GW update download attempt %s/%s failed: %s",
                attempt + 1,
                tries,
                exc,
            )
            if attempt + 1 >= tries:
                _discard_incoming(stage)
                raise
            wait = DOWNLOAD_BACKOFF_S[min(attempt, len(DOWNLOAD_BACKOFF_S) - 1)]
            _backoff(wait, cancel)
    raise last_exc or RuntimeError("下载失败")


def _bat_path(path: str) -> str:
    text = str(path)
    if any(ch in text for ch in '"\r\n'):
        raise RuntimeError(f"路径不能写入更新脚本: {text}")
    return text


def _apply_bat_lines(
    *,
    payload: str,
    root: str,
    stage: str,
    pid: int,
    download_page: str,
) -> list[str]:
    """Windows script: wait for this process, kill it if it stays, then copy.

    taskkill does not use /T. The script is a child of the app, and /T would
    kill the script along with the old process. robocopy codes 0-7 are success;
    8 and above open the download page. A missing exe does the same.
    """
    src = _bat_path(payload)
    dst = _bat_path(root)
    stage_path = _bat_path(stage)
    page = _bat_path(download_page)
    return [
        "@echo off",
        "setlocal EnableExtensions",
        "chcp 65001 >nul",
        f'set "PID={int(pid)}"',
        f'set "SRC={src}"',
        f'set "DST={dst}"',
        f'set "STAGE={stage_path}"',
        f'set "PAGE={page}"',
        'cd /d "%DST%"',
        "echo 光为 OCR 正在应用更新，请稍候...",
        "set /a WAIT=0",
        ":wait_exit",
        'tasklist /FI "PID eq %PID%" 2>nul | findstr /C:" %PID% " >nul',
        "if errorlevel 1 goto do_copy",
        "set /a WAIT+=1",
        "if %WAIT% GEQ 20 goto force_kill",
        "timeout /t 1 /nobreak >nul",
        "goto wait_exit",
        ":force_kill",
        "echo 正在结束旧版本进程...",
        "taskkill /PID %PID% /F >nul 2>&1",
        "timeout /t 3 /nobreak >nul",
        ":do_copy",
        "echo 正在复制新文件...",
        'robocopy "%SRC%" "%DST%" /E /R:8 /W:2 /XJ /NFL /NDL /NP '
        '/XD "_gw_update_stage" /XF "apply_gw_update.bat" '
        '/LOG:"%DST%\\_gw_update_robocopy.log"',
        "if errorlevel 8 goto copy_fail",
        'set "EXE=%DST%\\GuangWeiOCR.exe"',
        'if not exist "%EXE%" set "EXE=%DST%\\scoresight.exe"',
        'if not exist "%EXE%" goto no_exe',
        "echo 更新完成，正在重新启动。",
        'start "" "%EXE%"',
        'rmdir /s /q "%STAGE%" >nul 2>&1',
        'del /q "%DST%\\_gw_update_robocopy.log" >nul 2>&1',
        'del /q "%~f0" >nul 2>&1',
        "exit /b 0",
        ":copy_fail",
        "echo.",
        "echo 复制更新文件失败。可能是文件被占用，或被安全软件拦截。",
        "echo 请从下载站手动下载，解压后覆盖本目录。",
        "echo 复制日志：%DST%\\_gw_update_robocopy.log",
        'start "" "%PAGE%"',
        'if exist "%DST%\\GuangWeiOCR.exe" start "" "%DST%\\GuangWeiOCR.exe"',
        'if not exist "%DST%\\GuangWeiOCR.exe" '
        'if exist "%DST%\\scoresight.exe" start "" "%DST%\\scoresight.exe"',
        "echo.",
        "pause",
        "exit /b 1",
        ":no_exe",
        "echo.",
        "echo 未找到 GuangWeiOCR.exe，正在打开下载站。",
        'start "" "%PAGE%"',
        "echo.",
        "pause",
        "exit /b 1",
    ]


def write_apply_bat(payload: Path, pid: int | None = None) -> Path:
    """Write apply_gw_update.bat next to the portable root; run it on quit."""
    root = _portable_root()
    bat = root / "apply_gw_update.bat"
    lines = _apply_bat_lines(
        payload=str(payload),
        root=str(root),
        stage=str(root / STAGE_DIR_NAME),
        pid=os.getpid() if pid is None else int(pid),
        download_page=GW_DOWNLOAD_PAGE,
    )
    # UTF-8 BOM so cmd.exe on a Chinese Windows reads the echoes correctly.
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8-sig")
    return bat


def _qt_app():
    try:
        from PySide6.QtCore import QCoreApplication

        return QCoreApplication.instance()
    except Exception:
        return None


def _install_quit_hook(callback) -> str:
    """Connect callback once to Qt aboutToQuit, or atexit when Qt isn't up."""
    app = _qt_app()
    if app is not None:
        app.aboutToQuit.connect(callback)
        return "qt"
    import atexit

    atexit.register(callback)
    return "atexit"


def _spawn_apply() -> None:
    bat = _apply_state.get("bat")
    if not bat:
        return
    try:
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        kwargs = {}
        if sys.platform == "win32":
            # A new console so a failed copy can pause and be read. Not /T on
            # taskkill: this process must outlive the app it waits for.
            kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        subprocess.Popen([comspec, "/c", str(bat)], close_fds=True, **kwargs)
    except Exception as exc:
        logger.error("GW update apply spawn failed: %s", exc)


def schedule_apply_on_quit(payload: Path, *, register=None) -> Path:
    """Register a single quit hook that runs the apply bat.

    Calling this again refreshes the bat (the latest payload) and does not
    connect aboutToQuit a second time. Development runs refuse: they must not
    overwrite the source tree on quit.
    """
    if not is_frozen_portable():
        raise RuntimeError("开发版不会在退出时覆盖安装目录")
    bat = write_apply_bat(payload, pid=os.getpid())
    _apply_state["bat"] = str(bat)
    if _apply_state["hooked"]:
        logger.info("GW update: apply hook already registered; refreshed %s", bat)
        return bat
    if register is not None:
        register(_spawn_apply)
    else:
        kind = _install_quit_hook(_spawn_apply)
        logger.info("GW update: apply hook registered via %s", kind)
    _apply_state["hooked"] = True
    return bat


def open_download_page() -> bool:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        return bool(QDesktopServices.openUrl(QUrl(GW_DOWNLOAD_PAGE)))
    except Exception:
        logger.exception("GW update: could not open download page")
        return False


def _ask(parent, text: str, yes: str, no: str) -> bool:
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(parent)
    box.setWindowTitle("光为 OCR 更新")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(text)
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    box.button(QMessageBox.StandardButton.Yes).setText(yes)
    box.button(QMessageBox.StandardButton.No).setText(no)
    return box.exec() == QMessageBox.StandardButton.Yes


def show_up_to_date_dialog(parent, version: str) -> None:
    from PySide6.QtWidgets import QMessageBox

    QMessageBox.information(parent, "光为 OCR", up_to_date_text(version))


def show_check_failed_dialog(parent) -> bool:
    """Manual-check failure. Returns True when the user opens the download page."""
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(parent)
    box.setWindowTitle("光为 OCR")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(CHECK_FAILED_TEXT)
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    box.button(QMessageBox.StandardButton.Yes).setText("打开下载站")
    box.button(QMessageBox.StandardButton.No).setText("关闭")
    if box.exec() == QMessageBox.StandardButton.Yes:
        open_download_page()
        return True
    return False


def _warn_download_failed(parent, exc: BaseException) -> None:
    from PySide6.QtWidgets import QMessageBox

    message = str(exc)
    if "sha256" in message.lower() or "SHA-256" in message:
        text = "下载未通过 SHA-256 校验，安装已取消。\n\n请重试，或打开下载站手动获取。"
    else:
        text = f"下载失败：{exc}\n\n也可打开下载站手动获取。"
    box = QMessageBox(parent)
    box.setWindowTitle("光为 OCR 更新")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(text)
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    box.button(QMessageBox.StandardButton.Yes).setText("打开下载站")
    box.button(QMessageBox.StandardButton.No).setText("关闭")
    if box.exec() == QMessageBox.StandardButton.Yes:
        open_download_page()


def _download_with_progress(parent, info: GwReleaseInfo) -> Path | None:
    """Download on a worker thread. Returns the payload, or None if cancelled.

    Progress widgets are touched only on the GUI thread. A ~170 MB zip must
    not run on the GUI thread.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QProgressDialog

    prog = QProgressDialog("正在下载更新包…", "取消", 0, 100, parent)
    prog.setWindowTitle("光为 OCR")
    prog.setWindowModality(Qt.WindowModality.WindowModal)
    prog.setMinimumDuration(0)
    prog.setAutoClose(False)
    prog.setAutoReset(False)
    prog.setMinimumWidth(420)
    prog.setValue(0)

    cancel = CancelToken()
    state = {"done": 0, "total": 0, "phase": "download"}
    lock = threading.Lock()

    def progress(done, total):
        with lock:
            state["done"] = done
            state["total"] = total

    def on_phase(phase):
        with lock:
            state["phase"] = phase

    prog.canceled.connect(cancel.cancel)

    err: list[BaseException] = []
    payload_holder: list[Path] = []

    def worker():
        try:
            payload_holder.append(
                download_and_stage(
                    info, progress=progress, cancel=cancel, on_phase=on_phase
                )
            )
        except DownloadCancelled as exc:
            err.append(exc)
        except Exception as exc:
            err.append(exc)

    thread = threading.Thread(target=worker, name="gw-update-download", daemon=True)
    thread.start()
    phase_text = {
        "download": "正在下载更新包…",
        "verify": "正在校验文件…",
        "extract": "正在解压更新包…",
    }
    while thread.is_alive():
        with lock:
            done, total, phase = state["done"], state["total"], state["phase"]
        label = phase_text.get(phase, "正在准备更新…")
        if phase == "download" and total > 0:
            prog.setMaximum(100)
            prog.setValue(min(99, int(done * 100 / max(total, 1))))
            prog.setLabelText(
                f"{label} {done / 1048576:.1f} / {total / 1048576:.1f} MB"
            )
        elif phase == "download":
            prog.setLabelText(f"{label} {done / 1048576:.1f} MB")
        else:
            prog.setLabelText(label)
        QApplication.processEvents()
        thread.join(0.05)
    # close() can emit canceled; don't treat that as the user aborting a
    # download that already finished.
    prog.blockSignals(True)
    if payload_holder and not cancel.cancelled() and not err:
        prog.setValue(100)
    prog.close()
    # A click on 取消 wins, including when it lands during extract.
    if cancel.cancelled() or (err and isinstance(err[0], DownloadCancelled)):
        return None
    if err:
        raise err[0]
    return payload_holder[0] if payload_holder else None


def show_update_dialog(parent, result: GwUpdateResult) -> bool:
    """Chinese dialog. Returns True if the user accepted download/apply.

    Portable+frozen: download and stage, apply on quit.
    Source/dev: open the download page only (do not overwrite the source tree).
    """
    from PySide6.QtWidgets import QMessageBox

    info = result.info
    if info is None:
        return False
    notes = (info.notes or "").strip()
    if len(notes) > 400:
        notes = notes[:400] + "…"
    body = f"发现新版本 {info.version}（当前 {result.current}）。\n\n"
    if notes:
        body += notes + "\n\n"
    if is_frozen_portable():
        body += "是否立即下载？下载完成后，关闭软件将自动更新并重新启动。"
        if not _ask(parent, body, "立即更新", "下次再说"):
            return False
        try:
            payload = _download_with_progress(parent, info)
        except Exception as exc:
            logger.exception("GW update download failed")
            _warn_download_failed(parent, exc)
            return False
        if payload is None:
            return False
        try:
            schedule_apply_on_quit(payload)
        except Exception as exc:
            QMessageBox.warning(parent, "光为 OCR 更新", f"更新失败：{exc}")
            return False
        QMessageBox.information(
            parent,
            "光为 OCR 更新",
            "更新包已就绪。请关闭本软件，将自动完成更新并重新启动。",
        )
        return True
    body += "当前为开发版运行，不会覆盖源码。是否打开下载站？"
    if not _ask(parent, body, "打开下载站", "关闭"):
        return False
    open_download_page()
    return True

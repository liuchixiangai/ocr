"""光为 OCR channel auto-update (ocr.4018.cn).

Portable builds check version.json on every startup. When a newer release is
available, download the zip in the background and apply on quit via a bat
helper (exe is locked while running). Source/dev runs: notify only.
Upstream ScoreSight GitHub updater is left alone when SIMPLIFY_OUTPUT_UI is False.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
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

REQUEST_TIMEOUT = (4.0, 12.0)
UA = f"GuangWeiOCR/{GW_RELEASE}"


@dataclass
class GwReleaseInfo:
    version: str
    zip_url: str
    sha256: str | None
    notes: str
    released_at: str


@dataclass
class GwUpdateResult:
    current: str
    latest: str | None = None
    update_available: bool = False
    info: GwReleaseInfo | None = None
    error: str | None = None
    # set after a successful background download
    staged_dir: str | None = None


def is_guangwei_channel() -> bool:
    return bool(SIMPLIFY_OUTPUT_UI)


def is_frozen_portable() -> bool:
    return bool(getattr(sys, "frozen", False))


def _parse_loose_version(text: str) -> tuple:
    """Compare GW versions: prefer dotted numeric (0.4.2 / 2026.10.25.1)."""
    parts: list = []
    for chunk in str(text).strip().lstrip("vV").replace("-", ".").split("."):
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
    try:
        return _parse_loose_version(candidate) > _parse_loose_version(current)
    except Exception:
        return candidate.strip() != current.strip() and bool(candidate.strip())


def fetch_version_json() -> GwReleaseInfo | None:
    """GET version.json from the first reachable mirror. 404 / errors -> None."""
    last_err = None
    for url in GW_VERSION_JSON_URLS:
        try:
            r = requests.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": UA, "Accept": "application/json"},
            )
            if r.status_code == 404:
                logger.info("GW update: %s -> 404 (skip)", url)
                continue
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code} from {url}"
                continue
            data = r.json()
            ver = str(data.get("version") or "").strip()
            zip_url = str(data.get("zip_url") or "").strip()
            if not ver or not zip_url:
                last_err = f"incomplete version.json at {url}"
                continue
            return GwReleaseInfo(
                version=ver,
                zip_url=zip_url,
                sha256=(str(data["sha256"]).lower() if data.get("sha256") else None),
                notes=str(data.get("notes") or ""),
                released_at=str(data.get("released_at") or ""),
            )
        except Exception as exc:
            last_err = str(exc)
            logger.info("GW update: fetch failed %s: %s", url, exc)
    if last_err:
        logger.info("GW update: no version.json (%s)", last_err)
    return None


def check_gw_update() -> GwUpdateResult:
    """Blocking check. Never raises. Silent when no update / unreachable."""
    current = GW_RELEASE
    if not is_guangwei_channel():
        return GwUpdateResult(current=current, error="not guangwei channel")
    info = fetch_version_json()
    if info is None:
        return GwUpdateResult(current=current)
    if not is_newer(info.version, current):
        return GwUpdateResult(current=current, latest=info.version, info=info)
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
    root = _portable_root() / "_gw_update_stage"
    root.mkdir(parents=True, exist_ok=True)
    return root


def download_and_stage(
    info: GwReleaseInfo, progress: Callable[[int, int], None] | None = None
) -> Path:
    """Download zip, verify optional sha256, extract to stage dir. Returns stage Path."""
    stage = _stage_root()
    # clear previous
    for child in list(stage.iterdir()):
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    zip_path = stage / "GuangWeiOCR-Portable.zip"
    with requests.get(
        info.zip_url,
        stream=True,
        timeout=(8.0, 120.0),
        headers={"User-Agent": UA},
    ) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        h = hashlib.sha256()
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    digest = h.hexdigest()
    if info.sha256 and digest.lower() != info.sha256.lower():
        zip_path.unlink(missing_ok=True)
        raise RuntimeError(f"sha256 mismatch: got {digest}, want {info.sha256}")
    (stage / "sha256.txt").write_text(digest, encoding="utf-8")
    extract_dir = stage / "extracted"
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)
    # If zip contains a single top folder, use it; else the extract root.
    children = [p for p in extract_dir.iterdir()]
    payload = (
        children[0] if len(children) == 1 and children[0].is_dir() else extract_dir
    )
    marker = stage / "READY.txt"
    marker.write_text(
        json.dumps(
            {
                "version": info.version,
                "payload": str(payload),
                "zip": str(zip_path),
                "sha256": digest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return payload


def write_apply_bat(payload: Path) -> Path:
    """Write apply_update.bat next to the portable root; run on quit."""
    root = _portable_root()
    bat = root / "apply_gw_update.bat"
    # Copy payload contents over root after a short delay, then relaunch.
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "echo 光为 OCR 正在应用更新，请稍候...",
        "timeout /t 2 /nobreak >nul",
        f'set "SRC={payload}"',
        f'set "DST={root}"',
        # robocopy mirrors new files; /E copy subdirs; exclude stage folder
        'robocopy "%SRC%" "%DST%" /E /XO /R:2 /W:1 /NFL /NDL /NJH /NJS /XD _gw_update_stage >nul',
        'set "EXE=%DST%\\GuangWeiOCR.exe"',
        'if not exist "%EXE%" set "EXE=%DST%\\scoresight.exe"',
        'start "" "%EXE%"',
        # cleanup stage in background
        f'start /b cmd /c "timeout /t 5 /nobreak >nul & rmdir /s /q ""{_stage_root()}"" 2>nul"',
        'del "%~f0" >nul 2>&1',
    ]
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return bat


def schedule_apply_on_quit(payload: Path) -> None:
    """Register atexit / Qt aboutToQuit to spawn apply bat."""
    bat = write_apply_bat(payload)

    def _spawn():
        try:
            os.spawnl(
                os.P_NOWAIT,
                os.environ.get("COMSPEC", "cmd.exe"),
                "cmd.exe",
                "/c",
                str(bat),
            )
        except Exception as exc:
            logger.error("GW update apply spawn failed: %s", exc)

    # Prefer Qt if available so we run before process teardown completes.
    try:
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(_spawn)
            return
    except Exception:
        pass
    import atexit

    atexit.register(_spawn)


def show_update_dialog(parent, result: GwUpdateResult) -> bool:
    """Chinese dialog. Returns True if user accepted download/apply.

    Portable+frozen: download & stage, apply on quit.
    Source/dev: open download page only (do not overwrite source tree).
    """
    from PySide6.QtWidgets import QMessageBox

    info = result.info
    assert info is not None
    notes = (info.notes or "").strip()
    if len(notes) > 400:
        notes = notes[:400] + "…"
    body = f"发现新版本 {info.version}（当前 {result.current}）。\n\n{notes}\n\n"
    if is_frozen_portable():
        body += "是否立即下载？下载完成后关闭软件将自动更新并重新启动。"
        box = QMessageBox(parent)
        box.setWindowTitle("光为 OCR 更新")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(body)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.button(QMessageBox.StandardButton.Yes).setText("立即更新")
        box.button(QMessageBox.StandardButton.No).setText("下次再说")
        if box.exec() != QMessageBox.StandardButton.Yes:
            return False
        try:
            from PySide6.QtWidgets import QProgressDialog
            from PySide6.QtCore import Qt

            prog = QProgressDialog("正在下载更新包…", "取消", 0, 100, parent)
            prog.setWindowTitle("光为 OCR")
            prog.setWindowModality(Qt.WindowModality.WindowModal)
            prog.setMinimumDuration(0)
            prog.setValue(0)
            cancelled = {"v": False}

            def progress(done, total):
                if prog.wasCanceled():
                    cancelled["v"] = True
                    return
                if total > 0:
                    prog.setValue(min(99, int(done * 100 / total)))
                else:
                    prog.setValue(min(99, prog.value() + 1))

            # download on this thread briefly; keep UI pumping
            err_holder: list[Exception] = []
            payload_holder: list[Path] = []

            def worker():
                try:
                    payload_holder.append(download_and_stage(info, progress))
                except Exception as exc:
                    err_holder.append(exc)

            t = threading.Thread(target=worker, daemon=True)
            t.start()
            while t.is_alive():
                from PySide6.QtWidgets import QApplication

                QApplication.processEvents()
                t.join(0.05)
                if prog.wasCanceled():
                    cancelled["v"] = True
                    break
            prog.setValue(100)
            prog.close()
            if cancelled["v"]:
                return False
            if err_holder:
                QMessageBox.warning(
                    parent,
                    "光为 OCR 更新",
                    f"下载失败：{err_holder[0]}\n\n也可打开下载站手动获取。",
                )
                return False
            schedule_apply_on_quit(payload_holder[0])
            QMessageBox.information(
                parent,
                "光为 OCR 更新",
                "更新包已就绪。请关闭本软件，将自动完成更新并重新启动。",
            )
            return True
        except Exception as exc:
            QMessageBox.warning(parent, "光为 OCR 更新", f"更新失败：{exc}")
            return False
    # Dev / source: notify + open page
    body += "当前为开发版运行，不会覆盖源码。是否打开下载站？"
    box = QMessageBox(parent)
    box.setWindowTitle("光为 OCR 更新")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(body)
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    box.button(QMessageBox.StandardButton.Yes).setText("打开下载站")
    box.button(QMessageBox.StandardButton.No).setText("关闭")
    if box.exec() == QMessageBox.StandardButton.Yes:
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl

            QDesktopServices.openUrl(QUrl(GW_DOWNLOAD_PAGE))
        except Exception:
            pass
        return True
    return False

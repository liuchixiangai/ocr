"""光为 OCR product branding (fork of ScoreSight).

User-visible name is 光为 OCR. Upstream project name ScoreSight and the MIT
license attribution must remain in About / LICENSE / notices.
"""

from __future__ import annotations

import os
from pathlib import Path

# Shown in the window title, About dialog title, and first-run wizard.
APP_DISPLAY_NAME = "光为 OCR"

# Upstream project this fork is based on (keep for MIT attribution).
UPSTREAM_NAME = "ScoreSight"
UPSTREAM_URL = "https://github.com/royshil/scoresight"
UPSTREAM_DOCS = "https://royshil.github.io/scoresight/"
LICENSE_NAME = "MIT"

# GuangWei simplified Output tab: hide destination toggles + folder picker,
# always write to Desktop/光为OCR输出. Set False to keep upstream ScoreSight UI.
SIMPLIFY_OUTPUT_UI = True

# GuangWei channel version for ocr.4018.cn auto-update (independent of
# upstream ScoreSight __version__). A portable publish must bump this AND
# ocr.4018.cn/version.json to the same newer string (scripts/bump_gw_release.py).
# If they stay equal, installed copies will not offer an update.
GW_RELEASE = "2026.10.25.1"

# Download site (auto-update + "open download page").
GW_DOWNLOAD_SITE = "https://ocr.4018.cn"
GW_VERSION_JSON_URLS = (
    "https://ocr.4018.cn/version.json",
    "http://ocr.4018.cn/version.json",
    "http://211.101.245.47/version.json",
)
GW_DOWNLOAD_PAGE = "https://ocr.4018.cn/"


# Support QQ group (status-bar help link for Chinese edition).
QQ_GROUP_NUMBER = "124356408"
# Opens the group card in QQ when the desktop client is installed.
# Desktop QQ often rejects mqqapi; kept for reference only.
QQ_GROUP_OPEN_URL = (
    "mqqapi://card/show_pslcard?src_type=internal&version=1"
    "&uin=124356408&card_type=group&source=qrcode"
)
# Browser fallback if the mqqapi protocol is not registered.
QQ_GROUP_WEB_URL = (
    "https://qm.qq.com/cgi-bin/qm/qr?k=&jump_from=webapi&authKey=&group_code=124356408"
)

# Folder name under the user's Desktop (ASCII-safe path join; name is Chinese).
DEFAULT_OUTPUT_DIR_NAME = "光为OCR输出"


def default_output_folder() -> str:
    """Writable Desktop\\光为OCR输出 (create separately). Prefer Desktop over
    the software directory so a portable USB stick can stay read-only.
    """
    desktop = ""
    try:
        from PySide6.QtCore import QStandardPaths

        desktop = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DesktopLocation
        )
    except Exception:
        desktop = ""
    if not desktop:
        home = Path.home()
        for name in ("Desktop", "桌面"):
            candidate = home / name
            if candidate.is_dir():
                desktop = str(candidate)
                break
        if not desktop:
            desktop = str(home / "Desktop")
    return str(Path(desktop) / DEFAULT_OUTPUT_DIR_NAME)


def ensure_default_output_folder() -> str:
    """Create Desktop\\光为OCR输出 if missing; return its absolute path."""
    folder = default_output_folder()
    os.makedirs(folder, exist_ok=True)
    return folder

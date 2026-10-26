"""Update an installed ScoreSight in place on Windows (Update now in the update
dialog): download the new version's installer, check it and run it.

Only a copy installed by the Windows installer is updated this way: a frozen
Windows build with the installer's uninstaller next to it (installed_folder()).
The portable zip, macOS, Linux and a run from source download the new version
by hand, from the release page.

1. download_installer() downloads the release's setup.exe and its
   SHA256SUMS.txt, and checks the installer's size and SHA-256 against them.
2. check_signature() checks that the installer is signed by the publisher of
   the running scoresight.exe. A build that isn't signed (from a pull request,
   say) can only check the SHA-256.
3. start_installer() runs Setup unattended (/SILENT: its progress window, not
   the wizard), told to install over the running copy: its folder (/DIR) and
   whether it was installed for all users (install_mode(): /ALLUSERS or
   /CURRENTUSER). Otherwise Setup would update the installation registered
   last, which isn't the running one when there are two. Setup asks for
   administrator rights itself (UAC) for an installation for all users.
4. Setup (scoresight.iss, PrepareToInstall) writes the ready file once it runs
   with those rights: ScoreSight then closes as when its window is closed. Setup
   waits until ScoreSight has quit (the mutex the app holds, hold_mutex()),
   installs, and starts the new version (/RELAUNCH=1). If Setup ends without
   writing the ready file (the UAC prompt was declined), ScoreSight keeps
   running.

Blocking functions run on a worker thread (app_update_ui.py) and raise
UpdateError with what went wrong, in English (the log and the message).
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Callable

import requests

from sc_logging import logger
from version import __version__

# where the release workflow's assets are downloaded from
DOWNLOAD_URL_PREFIX = "https://github.com/royshil/scoresight/releases/download/"
INSTALLER_NAME = "scoresight-{version}-windows-x64-setup.exe"
SUMS_NAME = "SHA256SUMS.txt"
# the installer's uninstaller, in the installation folder
UNINSTALLER_NAME = "unins000.exe"
# the installer's uninstall entry (AppId ScoreSight), under HKCU for an
# installation for the current user, under HKLM for all users
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ScoreSight_is1"
ALL_USERS = "/ALLUSERS"
CURRENT_USER = "/CURRENTUSER"
# the downloads, in the temporary folder (removed when the next update starts)
DOWNLOAD_FOLDER_NAME = "scoresight-update"

# (connect, read) timeouts in seconds
REQUEST_TIMEOUT = (5, 30)
CHUNK = 1 << 16
SIGNATURE_TIMEOUT_S = 60


class UpdateError(Exception):
    """The update can't go on; the message says why (in English)."""


class UpdateCancelled(UpdateError):
    pass


@dataclass(frozen=True)
class InstallerAsset:
    """A release's Windows installer, from GitHub's release JSON."""

    version: str
    url: str
    size: int
    sums_url: str

    @property
    def name(self) -> str:
        return self.url.rsplit("/", 1)[-1]


def installer_asset(release) -> InstallerAsset | None:
    """The Windows installer of a release (a GitHub API release object), with
    its SHA256SUMS.txt; None if the release has no such assets."""
    if not isinstance(release, dict):
        return None
    version = str(release.get("tag_name") or "").strip().removeprefix("v")
    assets = {
        asset.get("name"): asset
        for asset in release.get("assets") or []
        if isinstance(asset, dict)
    }
    installer = assets.get(INSTALLER_NAME.format(version=version))
    sums = assets.get(SUMS_NAME)
    if not version or installer is None or sums is None:
        return None
    url = installer.get("browser_download_url")
    sums_url = sums.get("browser_download_url")
    size = installer.get("size")
    if not all(
        isinstance(link, str) and link.startswith(DOWNLOAD_URL_PREFIX)
        for link in (url, sums_url)
    ):
        return None
    if not isinstance(size, int) or size <= 0:
        return None
    return InstallerAsset(version, url, size, sums_url)


def installed_folder() -> str | None:
    """The installation folder of the running ScoreSight when the Windows
    installer installed it; None otherwise (it can't be updated in place)."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    folder = os.path.dirname(os.path.abspath(sys.executable))
    if not os.path.isfile(os.path.join(folder, UNINSTALLER_NAME)):
        return None
    return folder


def install_mode(folder: str, key: str = UNINSTALL_KEY) -> str | None:
    """How the installation in folder was made: ALL_USERS or CURRENT_USER,
    from the installer's uninstall entry that names it; None if none does."""
    if sys.platform != "win32":
        return None
    import winreg

    for root, mode in (
        (winreg.HKEY_CURRENT_USER, CURRENT_USER),
        (winreg.HKEY_LOCAL_MACHINE, ALL_USERS),
    ):
        # a 64-bit installation's entry, or a 32-bit one's (0.1.x)
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(root, key, 0, winreg.KEY_READ | view) as entry:
                    location, _ = winreg.QueryValueEx(entry, "InstallLocation")
                if os.path.samefile(location, folder):
                    return mode
            except OSError:
                continue
    return None


def download_folder() -> str:
    return os.path.join(tempfile.gettempdir(), DOWNLOAD_FOLDER_NAME)


def remove_downloads():
    """Remove what earlier updates downloaded (never raises)."""
    shutil.rmtree(download_folder(), ignore_errors=True)


def parse_sums(text: str) -> dict[str, str]:
    """{file name: SHA-256} of a sha256sum file ("<hash>  <name>" lines)."""
    sums = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and len(parts[0]) == 64:
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


def _http_get(url: str, stream: bool = False):
    return requests.get(
        url,
        headers={"User-Agent": f"ScoreSight/{__version__}"},
        timeout=REQUEST_TIMEOUT,
        stream=stream,
    )


def download_installer(
    asset: InstallerAsset,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    http_get: Callable = _http_get,
) -> str:
    """Download the installer into download_folder() and check it against the
    release's SHA256SUMS.txt; returns its path. progress(done, total) is called
    as the bytes arrive (on the calling thread)."""
    remove_downloads()
    folder = download_folder()
    try:
        os.makedirs(folder, exist_ok=True)
        response = http_get(asset.sums_url)
        response.raise_for_status()
        expected = parse_sums(response.text).get(asset.name)
        if expected is None:
            raise UpdateError(f"{SUMS_NAME} of {asset.version} has no {asset.name}")
        path = os.path.join(folder, asset.name)
        digest = hashlib.sha256()
        done = 0
        with http_get(asset.url, stream=True) as response:
            response.raise_for_status()
            with open(path, "wb") as f:
                for chunk in response.iter_content(CHUNK):
                    if cancelled is not None and cancelled():
                        raise UpdateCancelled("the download was cancelled")
                    f.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, asset.size)
    except UpdateError:
        remove_downloads()
        raise
    except (OSError, requests.RequestException) as e:
        remove_downloads()
        raise UpdateError(f"the download failed: {e}") from e
    if done != asset.size or digest.hexdigest() != expected:
        remove_downloads()
        raise UpdateError(
            f"the downloaded installer ({done} bytes, SHA-256 {digest.hexdigest()}) "
            f"isn't the release's ({asset.size} bytes, SHA-256 {expected})"
        )
    logger.info("Downloaded the installer of %s to %s", asset.version, path)
    return path


def _powershell() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def authenticode_signer(path: str) -> str | None:
    """The subject of the certificate that signed the file, when its
    Authenticode signature is valid; None when it isn't signed (validly).
    Raises UpdateError when the signature can't be checked."""
    # the path goes in an environment variable, never in the script
    script = (
        "$s = Get-AuthenticodeSignature -LiteralPath $env:SCORESIGHT_SIGNED_FILE; "
        "if ($s.Status -eq 'Valid') { $s.SignerCertificate.Subject }"
    )
    # Windows PowerShell finds its own modules: PowerShell 7's PSModulePath (a
    # ScoreSight started from pwsh) makes it load pwsh's, which fails
    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper() != "PSMODULEPATH"
    }
    env["SCORESIGHT_SIGNED_FILE"] = path
    try:
        completed = subprocess.run(
            [_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=SIGNATURE_TIMEOUT_S,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise UpdateError(f"the installer's signature couldn't be checked: {e}") from e
    if completed.returncode != 0:
        raise UpdateError(
            "the installer's signature couldn't be checked: "
            f"{completed.stderr.strip() or completed.returncode}"
        )
    return completed.stdout.strip() or None


def check_signature(
    installer: str,
    running: str | None = None,
    signer: Callable[[str], str | None] = authenticode_signer,
):
    """Check that the installer is signed by the publisher of the running
    scoresight.exe; raises UpdateError if it isn't. When the running app isn't
    signed, only the SHA-256 was checked (download_installer)."""
    running = running or sys.executable
    publisher = signer(running)
    if publisher is None:
        logger.warning(
            "%s isn't signed: the installer is checked by its SHA-256 only", running
        )
        return
    installer_signer = signer(installer)
    if installer_signer != publisher:
        raise UpdateError(
            f"the installer is signed by {installer_signer or 'nobody'}, "
            f"not by {publisher}"
        )
    logger.info("The installer is signed by %s", installer_signer)


# --- running the installer ----------------------------------------------------------


def mutex_name() -> str:
    """The mutex the updating app holds until it has quit (Setup waits for it)."""
    return f"ScoreSightUpdate-{os.getpid()}"


def hold_mutex(name: str):
    """Create the mutex; it is released when the process ends (the handle is
    never closed). Returns the handle, or None when it can't be created (Setup
    then closes the app with the Restart Manager instead)."""
    if sys.platform != "win32":
        return None
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        logger.warning(
            "Couldn't create the mutex %s: %d", name, ctypes.get_last_error()
        )
    return handle


def installer_arguments(
    ready_file: str,
    mutex: str,
    log_file: str | None = None,
    folder: str | None = None,
    mode: str | None = None,
) -> list[str]:
    """Setup's command line options for an update (see scoresight.iss). With
    the running app's folder and install mode, Setup updates that installation:
    otherwise it updates the one registered last (another one, if there are
    two)."""
    arguments = [
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/RELAUNCH=1",
        f"/UPDATEREADY={ready_file}",
        f"/UPDATEMUTEX={mutex}",
    ]
    if folder:
        arguments.append(f"/DIR={folder}")
    if mode:
        arguments.append(mode)
    if log_file:
        arguments.append(f"/LOG={log_file}")
    return arguments


def start_installer(
    installer: str,
    ready_file: str,
    mutex: str,
    log_file: str | None = None,
    folder: str | None = None,
    mode: str | None = None,
    popen: Callable = subprocess.Popen,
):
    """Start Setup (it asks for administrator rights itself when it needs
    them); returns its process. Raises UpdateError if it can't start."""
    command = [
        installer,
        *installer_arguments(ready_file, mutex, log_file, folder, mode),
    ]
    logger.info("Starting the installer: %s", subprocess.list2cmdline(command))
    try:
        return popen(command, close_fds=True)
    except OSError as e:
        raise UpdateError(f"the installer couldn't start: {e}") from e

"""app_update and app_update_ui: Update now on Windows (the installer found in a
release, downloaded, checked and run, and the app closing once it installs)."""

import hashlib
import os
import sys

import pytest
import requests

import app_update
import app_update_ui
import update_check
from app_update import InstallerAsset, UpdateError
from update_check import UpdateCheckResult

DOWNLOADS = "https://github.com/royshil/scoresight/releases/download/0.5.0/"
PAYLOAD = b"MZ" + bytes(range(256)) * 300


def asset_json(name, size=1000, url=None):
    return {
        "name": name,
        "size": size,
        "browser_download_url": url or DOWNLOADS + name,
    }


def release(tag="0.5.0", assets=None):
    if assets is None:
        assets = [
            asset_json("scoresight-0.5.0-windows-x64-setup.exe", 128_000_000),
            asset_json("scoresight-0.5.0-macos-arm64.dmg"),
            asset_json("SHA256SUMS.txt", 500),
        ]
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "html_url": f"https://github.com/royshil/scoresight/releases/tag/{tag}",
        "assets": assets,
    }


# --- the installer of a release ------------------------------------------------------


def test_the_installer_of_a_release():
    asset = app_update.installer_asset(release())
    assert asset == InstallerAsset(
        "0.5.0",
        DOWNLOADS + "scoresight-0.5.0-windows-x64-setup.exe",
        128_000_000,
        DOWNLOADS + "SHA256SUMS.txt",
    )
    assert asset.name == "scoresight-0.5.0-windows-x64-setup.exe"
    # a tag with a v: the files are named without it
    tagged = release("v0.5.0")
    assert app_update.installer_asset(tagged).version == "0.5.0"


@pytest.mark.parametrize(
    "assets",
    [
        [],
        # no checksums
        [asset_json("scoresight-0.5.0-windows-x64-setup.exe")],
        # another version's installer
        [
            asset_json("scoresight-0.4.0-windows-x64-setup.exe"),
            asset_json("SHA256SUMS.txt"),
        ],
        # not from the release's downloads
        [
            asset_json(
                "scoresight-0.5.0-windows-x64-setup.exe",
                url="https://example.com/scoresight-0.5.0-windows-x64-setup.exe",
            ),
            asset_json("SHA256SUMS.txt"),
        ],
        [
            asset_json("scoresight-0.5.0-windows-x64-setup.exe", size=0),
            asset_json("SHA256SUMS.txt"),
        ],
    ],
)
def test_releases_without_a_usable_installer(assets):
    assert app_update.installer_asset(release(assets=assets)) is None


def test_odd_releases_have_no_installer():
    assert app_update.installer_asset(None) is None
    assert app_update.installer_asset({"tag_name": "0.5.0"}) is None
    assert app_update.installer_asset({"assets": "nope"}) is None


def test_the_update_check_keeps_the_installer():
    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return release()

    result = update_check.check_for_updates("0.4.0", http_get=lambda url: Response())
    assert result.update_available
    assert result.installer == app_update.installer_asset(release())
    # up to date: nothing to install
    result = update_check.check_for_updates("0.5.0", http_get=lambda url: Response())
    assert not result.update_available and result.installer is None


# --- installed or not -----------------------------------------------------------------


def test_only_an_installed_windows_build_updates_itself(monkeypatch, tmp_path):
    exe = tmp_path / "scoresight.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "platform", "win32")
    # from source
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert app_update.installed_folder() is None
    # the portable zip: no uninstaller
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert app_update.installed_folder() is None
    (tmp_path / "unins000.exe").write_bytes(b"")
    assert app_update.installed_folder() == str(tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert app_update.installed_folder() is None


# --- the download ---------------------------------------------------------------------


class FakeResponse:
    def __init__(self, body=b"", status_code=200, error=None):
        self.body = body
        self.status_code = status_code
        self.error = error

    @property
    def text(self):
        return self.body.decode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def iter_content(self, size):
        for start in range(0, len(self.body), size):
            if self.error is not None and start:
                raise self.error
            yield self.body[start : start + size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def sums_for(body, name="scoresight-0.5.0-windows-x64-setup.exe"):
    lines = [
        f"{'0' * 64}  scoresight-0.5.0-macos-arm64.dmg",
        f"{hashlib.sha256(body).hexdigest()}  {name}",
    ]
    return "\n".join(lines).encode() + b"\n"


class FakeServer:
    def __init__(self, installer=PAYLOAD, sums=None, sums_status=200, error=None):
        self.responses = {
            DOWNLOADS + "SHA256SUMS.txt": FakeResponse(
                sums_for(PAYLOAD) if sums is None else sums, sums_status
            ),
            DOWNLOADS + "scoresight-0.5.0-windows-x64-setup.exe": FakeResponse(
                installer, error=error
            ),
        }
        self.urls = []

    def __call__(self, url, stream=False):
        self.urls.append((url, stream))
        return self.responses[url]


@pytest.fixture
def downloads(monkeypatch, tmp_path):
    folder = tmp_path / "scoresight-update"
    monkeypatch.setattr(app_update, "download_folder", lambda: str(folder))
    return folder


def asset(size=len(PAYLOAD)):
    return InstallerAsset(
        "0.5.0",
        DOWNLOADS + "scoresight-0.5.0-windows-x64-setup.exe",
        size,
        DOWNLOADS + "SHA256SUMS.txt",
    )


def test_parse_sums():
    text = f"{'a' * 64}  one.exe\n{'B' * 64} *two.zip\n\nnot a line\n"
    assert app_update.parse_sums(text) == {"one.exe": "a" * 64, "two.zip": "b" * 64}


def test_the_installer_is_downloaded_and_checked(downloads):
    (downloads / "old").mkdir(parents=True)
    server = FakeServer()
    progress = []
    path = app_update.download_installer(
        asset(),
        progress=lambda done, total: progress.append((done, total)),
        http_get=server,
    )
    assert path == str(downloads / "scoresight-0.5.0-windows-x64-setup.exe")
    with open(path, "rb") as f:
        assert f.read() == PAYLOAD
    # an earlier update's downloads are gone
    assert sorted(os.listdir(downloads)) == ["scoresight-0.5.0-windows-x64-setup.exe"]
    assert progress[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert [done for done, _ in progress] == sorted(done for done, _ in progress)
    # the checksums first, the installer streamed
    assert server.urls == [
        (DOWNLOADS + "SHA256SUMS.txt", False),
        (DOWNLOADS + "scoresight-0.5.0-windows-x64-setup.exe", True),
    ]


@pytest.mark.parametrize(
    "server, size, message",
    [
        (
            FakeServer(installer=PAYLOAD[:-1] + b"!"),
            len(PAYLOAD),
            "isn't the release's",
        ),
        (FakeServer(), len(PAYLOAD) + 1, "isn't the release's"),
        (FakeServer(sums=sums_for(PAYLOAD, "other.exe")), len(PAYLOAD), "has no"),
        (FakeServer(sums_status=404), len(PAYLOAD), "the download failed"),
        (
            FakeServer(error=requests.ConnectionError("reset")),
            len(PAYLOAD),
            "the download failed: reset",
        ),
    ],
)
def test_a_bad_download_is_removed(downloads, server, size, message):
    with pytest.raises(UpdateError, match=message):
        app_update.download_installer(asset(size), http_get=server)
    assert not downloads.exists()


def test_a_cancelled_download_is_removed(downloads):
    calls = []

    def cancelled():
        calls.append(1)
        return len(calls) > 1

    with pytest.raises(app_update.UpdateCancelled):
        app_update.download_installer(
            asset(), cancelled=cancelled, http_get=FakeServer()
        )
    assert not downloads.exists()


# --- the signature --------------------------------------------------------------------

PUBLISHER = "CN=Roy Shilkrot, O=Roy Shilkrot, L=Arlington, S=ma, C=US"


def signers(**by_path):
    return lambda path: by_path[path]


def test_the_installer_is_signed_by_the_apps_publisher():
    app_update.check_signature(
        "setup.exe",
        "app.exe",
        signers(**{"setup.exe": PUBLISHER, "app.exe": PUBLISHER}),
    )
    for other in (None, "CN=Someone Else"):
        with pytest.raises(UpdateError, match="not by CN=Roy Shilkrot"):
            app_update.check_signature(
                "setup.exe",
                "app.exe",
                signers(**{"setup.exe": other, "app.exe": PUBLISHER}),
            )


def test_an_unsigned_app_checks_the_sha256_only():
    # a build from a pull request, say: the installer's signature isn't looked at
    app_update.check_signature("setup.exe", "app.exe", signers(**{"app.exe": None}))


@pytest.mark.skipif(sys.platform != "win32", reason="Authenticode is Windows'")
def test_an_unsigned_file_has_no_signer(tmp_path):
    path = tmp_path / "unsigned file.exe"
    path.write_bytes(PAYLOAD)
    assert app_update.authenticode_signer(str(path)) is None


# --- running the installer ------------------------------------------------------------


def test_the_installers_command_line(tmp_path):
    ready = str(tmp_path / "with space" / "installing.txt")
    assert app_update.installer_arguments(ready, "ScoreSightUpdate-7", "C:\\l.log") == [
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/RELAUNCH=1",
        f"/UPDATEREADY={ready}",
        "/UPDATEMUTEX=ScoreSightUpdate-7",
        "/LOG=C:\\l.log",
    ]
    assert "/LOG" not in " ".join(app_update.installer_arguments(ready, "m"))
    # this installation, and how it was installed
    assert app_update.installer_arguments(
        ready, "m", folder=r"C:\Program Files\ScoreSight", mode="/ALLUSERS"
    )[-2:] == [r"/DIR=C:\Program Files\ScoreSight", "/ALLUSERS"]
    commands = []
    app_update.start_installer(
        "setup.exe", ready, "m", popen=lambda command, **kw: commands.append(command)
    )
    assert commands == [["setup.exe", *app_update.installer_arguments(ready, "m")]]

    def refused(command, **kw):
        raise OSError("refused")

    with pytest.raises(UpdateError, match="couldn't start: refused"):
        app_update.start_installer("setup.exe", ready, "m", popen=refused)


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows registry")
def test_the_install_mode_of_a_folder(tmp_path):
    import winreg

    # a throw-away key, not the real installation's entry
    key = rf"Software\ScoreSight-test-{os.getpid()}\Uninstall\ScoreSight_is1"
    folder = tmp_path / "ScoreSight"
    folder.mkdir()
    assert app_update.install_mode(str(folder), key) is None
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as entry:
        # as Inno Setup writes it, with a trailing backslash
        winreg.SetValueEx(entry, "InstallLocation", 0, winreg.REG_SZ, f"{folder}\\")
    try:
        assert app_update.install_mode(str(folder), key) == app_update.CURRENT_USER
        # another installation's folder
        other = tmp_path / "other"
        other.mkdir()
        assert app_update.install_mode(str(other), key) is None
    finally:
        parts = key.split("\\")
        for depth in range(len(parts), 1, -1):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, "\\".join(parts[:depth]))


def test_the_mutex_names_the_process():
    assert app_update.mutex_name() == f"ScoreSightUpdate-{os.getpid()}"


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows mutex")
def test_the_mutex_is_held():
    import ctypes

    name = f"ScoreSightUpdate-test-{os.getpid()}"
    assert app_update.hold_mutex(name)
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.OpenMutexW.restype = ctypes.c_void_p
    SYNCHRONIZE = 0x00100000
    handle = kernel32.OpenMutexW(SYNCHRONIZE, False, name)
    assert handle
    kernel32.CloseHandle(ctypes.c_void_p(handle))


# --- the dialog's Update now button ---------------------------------------------------


def update_result(installer=True):
    return UpdateCheckResult(
        "0.4.0",
        "0.5.0",
        update_available=True,
        installer=asset() if installer else None,
    )


def test_update_now_is_offered_when_the_app_can_update_itself(qapp):
    asked = []
    dialog = update_check.create_update_dialog(update_result(), None, asked.append)
    try:
        dialog.update_now_button.click()
        assert [result.latest_version for result in asked] == ["0.5.0"]
    finally:
        dialog.deleteLater()
    for result, update_now in (
        (update_result(installer=False), asked.append),
        (update_result(), None),
        (UpdateCheckResult("0.5.0", "0.5.0"), asked.append),
    ):
        dialog = update_check.create_update_dialog(result, None, update_now)
        assert not hasattr(dialog, "update_now_button")
        dialog.deleteLater()


# --- the update, in the app -----------------------------------------------------------


class FakeProcess:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


@pytest.fixture
def updater(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox, QWidget

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.image_viewer = None
            self.confirmations = []
            self.answer = True
            self.closed = False

        def confirm(self, title, text, action_text):
            self.confirmations.append(text)
            return self.answer

        def closeEvent(self, event):
            self.closed = True
            super().closeEvent(event)

    installer = tmp_path / "scoresight-0.5.0-windows-x64-setup.exe"
    calls = {"download": [], "signature": [], "start": [], "messages": []}

    def download(asset, progress=None, cancelled=None):
        calls["download"].append(asset)
        progress(asset.size // 2, asset.size)
        progress(asset.size, asset.size)
        installer.write_bytes(PAYLOAD)
        return str(installer)

    process = FakeProcess()

    def start(path, ready_file, mutex, log_file=None, folder=None, mode=None):
        calls["start"].append((path, ready_file, mutex, log_file))
        calls["installation"] = (folder, mode)
        return process

    monkeypatch.setattr(app_update, "download_installer", download)
    monkeypatch.setattr(app_update, "check_signature", calls["signature"].append)
    monkeypatch.setattr(app_update, "start_installer", start)
    monkeypatch.setattr(app_update, "hold_mutex", lambda name: None)
    monkeypatch.setattr(
        app_update, "install_mode", lambda folder: f"/CURRENTUSER for {folder}"
    )
    monkeypatch.setattr(
        QMessageBox, "exec", lambda box: calls["messages"].append(box.text()) or 0
    )
    window = Window()
    appUpdater = app_update_ui.AppUpdater(window, installed_folder=lambda: "C:\\x")
    yield appUpdater, window, calls, process
    appUpdater.finish()
    window.deleteLater()


def test_update_now_closes_the_app_once_setup_installs(updater, qtbot):
    appUpdater, window, calls, process = updater
    assert appUpdater.available()
    appUpdater.start(update_result())
    assert appUpdater.busy
    qtbot.waitUntil(lambda: bool(calls["start"]), timeout=5000)
    path, ready_file, mutex, log_file = calls["start"][0]
    # the running installation is updated
    assert calls["installation"] == ("C:\\x", "/CURRENTUSER for C:\\x")
    assert calls["signature"] == [path]
    assert os.path.dirname(ready_file) == os.path.dirname(path)
    assert mutex == app_update.mutex_name()
    assert log_file is None or os.path.basename(log_file).startswith(
        "scoresight_setup_"
    )
    # nothing to confirm without a source open
    assert window.confirmations == []
    # Setup at the UAC prompt: ScoreSight keeps running
    appUpdater.poll()
    assert not window.closed
    # Setup installing
    with open(ready_file, "w") as f:
        f.write("installing")
    qtbot.waitUntil(lambda: window.closed, timeout=5000)
    assert calls["messages"] == []


def test_setup_ending_before_installing_keeps_the_app(updater, qtbot):
    appUpdater, window, calls, process = updater
    appUpdater.start(update_result())
    qtbot.waitUntil(lambda: bool(calls["start"]), timeout=5000)
    # the UAC prompt declined
    process.code = 1
    qtbot.waitUntil(lambda: bool(calls["messages"]), timeout=5000)
    assert "ended before installing" in calls["messages"][0]
    assert not window.closed and not appUpdater.busy


def test_a_failed_download_keeps_the_app(updater, qtbot, monkeypatch):
    appUpdater, window, calls, process = updater

    def fail(asset, progress=None, cancelled=None):
        raise UpdateError("the download failed: no network")

    monkeypatch.setattr(app_update, "download_installer", fail)
    appUpdater.start(update_result())
    qtbot.waitUntil(lambda: bool(calls["messages"]), timeout=5000)
    assert calls["messages"][0].startswith("the download failed: no network")
    assert calls["start"] == [] and not window.closed and not appUpdater.busy


def test_update_now_asks_first_while_a_source_is_open(updater, qtbot):
    appUpdater, window, calls, process = updater
    window.image_viewer = object()
    window.answer = False
    appUpdater.start(update_result())
    assert len(window.confirmations) == 1 and "0.5.0" in window.confirmations[0]
    assert not appUpdater.busy and calls["download"] == []
    window.answer = True
    appUpdater.start(update_result())
    qtbot.waitUntil(lambda: bool(calls["start"]), timeout=5000)

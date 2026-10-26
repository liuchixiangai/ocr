"""The DirectShow camera enumerator wrapper (src/win32DeviceEnum)."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from win32DeviceEnum import enum_devices_dshow

S_OK = 0
S_FALSE = 1
RPC_E_CHANGED_MODE = -2147417850  # 0x80010106 as a signed HRESULT


@pytest.fixture
def fake_extension(monkeypatch):
    """Replace the compiled extension with a recording fake."""
    calls = []
    fake = SimpleNamespace(
        hr=S_OK,
        devices=["Camera A", "Camera B"],
        calls=calls,
    )

    def enumerate_devices():
        calls.append("enumerate")
        if isinstance(fake.devices, Exception):
            raise fake.devices
        return list(fake.devices)

    fake.InitializeCOM = lambda: calls.append("init") or fake.hr
    fake.UninitializeCOM = lambda: calls.append("uninit")
    fake.EnumerateVideoDevicesDShow = enumerate_devices
    monkeypatch.setitem(sys.modules, "win32DeviceEnumBind", fake)
    monkeypatch.setattr(sys, "path", list(sys.path))
    return fake


@pytest.mark.parametrize("hr", [S_OK, S_FALSE])
def test_com_initialization_is_balanced(fake_extension, hr):
    fake_extension.hr = hr

    devices = enum_devices_dshow.enumerate_video_devices_dshow()

    assert devices == [(0, "Camera A"), (1, "Camera B")]
    assert fake_extension.calls == ["init", "enumerate", "uninit"]


def test_failed_com_initialization_is_not_uninitialized(fake_extension):
    # e.g. the thread already uses a multithreaded apartment: COM works, but
    # CoUninitialize would undo someone else's initialization.
    fake_extension.hr = RPC_E_CHANGED_MODE

    devices = enum_devices_dshow.enumerate_video_devices_dshow()

    assert devices == [(0, "Camera A"), (1, "Camera B")]
    assert fake_extension.calls == ["init", "enumerate"]


def test_com_is_uninitialized_when_enumeration_fails(fake_extension):
    fake_extension.devices = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        enum_devices_dshow.enumerate_video_devices_dshow()

    assert fake_extension.calls == ["init", "enumerate", "uninit"]


def test_sys_path_does_not_grow(fake_extension):
    for _ in range(3):
        enum_devices_dshow.enumerate_video_devices_dshow()
    assert sys.path.count(enum_devices_dshow._EXTENSION_DIR) == 1


EXTENSION_DIR = Path(enum_devices_dshow._EXTENSION_DIR)


@pytest.mark.skipif(sys.platform != "win32", reason="DirectShow is Windows-only")
@pytest.mark.skipif(
    not any(EXTENSION_DIR.glob("win32DeviceEnumBind.*.pyd")),
    reason="win32DeviceEnumBind is not built (src/win32DeviceEnum/setup.py)",
)
def test_real_enumeration():
    devices = enum_devices_dshow.enumerate_video_devices_dshow()
    assert all(
        isinstance(index, int) and isinstance(name, str) for index, name in devices
    )
    assert [index for index, _ in devices] == list(range(len(devices)))

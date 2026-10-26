import sys
from os import path

# The extension (win32DeviceEnumBind.<ABI tag>.pyd, built by setup.py) lives
# next to this file.
_EXTENSION_DIR = path.abspath(path.dirname(__file__))


def enumerate_video_devices_dshow() -> list[tuple[int, str]]:
    """The DirectShow video input devices as (index, name), in DirectShow
    enumeration order, which OpenCV's camera indices follow.

    A COM failure raises RuntimeError. An empty list means the search
    succeeded and Windows reported no video input device. A missing
    win32DeviceEnumBind raises ImportError. Neither failure is an empty list.
    """
    if _EXTENSION_DIR not in sys.path:
        sys.path.append(_EXTENSION_DIR)
    import win32DeviceEnumBind

    # Initialize COM. S_FALSE (already initialized on this thread, e.g. by Qt)
    # still has to be balanced by an uninitialize; a failure such as
    # RPC_E_CHANGED_MODE (another apartment type) must not be.
    hr = win32DeviceEnumBind.InitializeCOM()
    try:
        device_names = win32DeviceEnumBind.EnumerateVideoDevicesDShow()
    finally:
        if hr >= 0:
            win32DeviceEnumBind.UninitializeCOM()

    return list(enumerate(device_names))

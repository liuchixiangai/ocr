import os
import sys

# When running from source this file lives in <repo>/src/, so the repository
# root (which holds tesseract/, translations/, icons/, obs_data/ and .env) is
# one level up. Deriving it from __file__ lets the app run from any CWD.
SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_base_path() -> str:
    """Directory that bundled resources are resolved against.

    PyInstaller unpacks/collects the bundled data under ``sys._MEIPASS`` when the
    app is frozen; from source it is the repository root.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    return SOURCE_ROOT


def resource_path(*relative_paths: str) -> str:
    """Get absolute path to a resource, works for dev and for PyInstaller."""
    return os.path.join(resource_base_path(), *relative_paths)

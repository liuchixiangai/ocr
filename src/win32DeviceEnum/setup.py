"""Build the DirectShow camera enumerator extension (Windows only, needs MSVC).

    pip install -r requirements-build.txt
    python src/win32DeviceEnum/setup.py build_ext --inplace

This writes win32DeviceEnumBind.<ABI tag>.pyd (for example
win32DeviceEnumBind.cp313-win_amd64.pyd) into this folder, where
enum_devices_dshow.py and scoresight.spec look for it. Build it once per
Python version.
"""

import os
import sys

from setuptools import setup

if sys.platform != "win32":
    sys.exit("win32DeviceEnumBind uses DirectShow and only builds on Windows.")

try:
    from pybind11.setup_helpers import Pybind11Extension, build_ext
except ImportError:
    sys.exit("pybind11 is missing: pip install -r requirements-build.txt")

# The source path below and the output of build_ext --inplace are relative to
# the current directory, so it works from any directory.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

setup(
    name="win32DeviceEnumBind",
    version="0.0.1",
    ext_modules=[
        Pybind11Extension(
            "win32DeviceEnumBind",
            ["video_devices_enumerator_ds.cpp"],
            cxx_std=17,
            libraries=["strmiids", "ole32", "oleaut32", "uuid", "quartz"],
        ),
    ],
    cmdclass={"build_ext": build_ext},
    zip_safe=False,
)

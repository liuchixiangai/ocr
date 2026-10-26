import platform
import sys
from camera_info import CameraInfo
from sc_logging import logger

# This file contains the code to get the camera information for the current OS


def get_camera_info_windows():
    from win32DeviceEnum import enum_devices_dshow

    device_info = []
    cameras = enum_devices_dshow.enumerate_video_devices_dshow()
    for camera in cameras:
        device_info.append(
            CameraInfo(
                camera[1], str(camera[0]), camera[0], CameraInfo.CameraType.OPENCV
            )
        )

    return device_info


def get_camera_info_mac():
    import AVFoundation as AV

    device_info = []
    devices = AV.AVCaptureDevice.devicesWithMediaType_(AV.AVMediaTypeVideo)

    for i, device in enumerate(devices):
        device_info.append(
            CameraInfo(
                device.localizedName(),
                device.uniqueID(),
                i,
                CameraInfo.CameraType.OPENCV,
            )
        )

    # sort by the ID, since opencv sorts by ID
    device_info.sort(key=lambda x: x.uuid)
    # update the ID
    for i, camera in enumerate(device_info):
        camera.id = i

    return device_info


# V4L2 (linux/videodev2.h): VIDIOC_QUERYCAP = _IOR('V', 0, struct v4l2_capability)
_VIDIOC_QUERYCAP = 0x80685600
_V4L2_CAPABILITY_SIZE = 104
_V4L2_CAP_VIDEO_CAPTURE = 0x00000001
_V4L2_CAP_VIDEO_CAPTURE_MPLANE = 0x00001000
_V4L2_CAP_DEVICE_CAPS = 0x80000000


def query_v4l2_device(device_path: str):
    """(card name, can capture video) for a V4L2 device node, or None if it
    can't be queried (e.g. no permission)."""
    import os

    try:
        import fcntl
    except ImportError:  # not a POSIX system
        return None
    try:
        fd = os.open(device_path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        buffer = bytearray(_V4L2_CAPABILITY_SIZE)
        fcntl.ioctl(fd, _VIDIOC_QUERYCAP, buffer)
    except OSError:
        return None
    finally:
        os.close(fd)
    card = bytes(buffer[16:48]).split(b"\0", 1)[0].decode("utf-8", "replace")
    capabilities = int.from_bytes(buffer[84:88], sys.byteorder)
    device_caps = int.from_bytes(buffer[88:92], sys.byteorder)
    # the node's own capabilities (capabilities covers the whole device)
    caps = device_caps if capabilities & _V4L2_CAP_DEVICE_CAPS else capabilities
    can_capture = bool(
        caps & (_V4L2_CAP_VIDEO_CAPTURE | _V4L2_CAP_VIDEO_CAPTURE_MPLANE)
    )
    return card, can_capture


def _read_sysfs(device_number: int, attribute: str) -> str | None:
    try:
        with open(
            f"/sys/class/video4linux/video{device_number}/{attribute}",
            encoding="utf-8",
        ) as f:
            return f.read().strip()
    except OSError:
        return None


def get_camera_info_linux(
    device_paths: list[str] | None = None,
    query_device=query_v4l2_device,
    read_sysfs=_read_sysfs,
):
    """List the /dev/videoN capture devices with N as the OpenCV index (V4L2
    numbering can have gaps) and skip nodes that can't capture video, such as
    the metadata node every UVC webcam also exposes."""
    import glob
    import re

    if device_paths is None:
        device_paths = glob.glob("/dev/video*")
    devices = []
    for device_path in device_paths:
        match = re.fullmatch(r"/dev/video(\d+)", device_path)
        if match:
            devices.append((int(match.group(1)), device_path))

    device_info = []
    for number, device_path in sorted(devices):
        queried = query_device(device_path)
        if queried is not None:
            name, can_capture = queried
        else:
            # Can't open it: fall back to sysfs, where a UVC camera's capture
            # node has index 0 and its metadata node index 1.
            name = read_sysfs(number, "name")
            index = read_sysfs(number, "index")
            can_capture = index is None or index == "0"
        if not can_capture:
            continue
        description = f"{name} ({device_path})" if name else f"Camera {number}"
        device_info.append(
            CameraInfo(description, device_path, number, CameraInfo.CameraType.OPENCV)
        )
    return device_info


def get_camera_info() -> list[CameraInfo]:
    logger.info("Getting cameras info")
    # Imported here, by the source discovery (a background thread at startup),
    # not by the app's startup: cyndilib and the NDI library take a while to
    # load. Importing it starts the NDI discovery, which then runs while the
    # cameras are listed.
    from ndi import NDICapture

    os_name = platform.system()
    cameras = []
    if os_name == "Windows":
        cameras += get_camera_info_windows()
    elif os_name == "Darwin":
        cameras += get_camera_info_mac()
    elif os_name == "Linux":
        cameras += get_camera_info_linux()

    # Add NDI cameras
    cameras += NDICapture.get_camera_info_ndi()

    return cameras

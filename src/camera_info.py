class CameraInfo:
    class CameraType:
        OPENCV = "OPENCV"
        NDI = "NDI"
        IP = "IP"
        VIRTUAL = "Virtual"
        FILE = "File"
        URL = "URL"
        SCREEN_CAPTURE = "Screen Capture"

    def __init__(self, description: str, uuid: str, id: str | int, type: str):
        self.description = description
        self.uuid = uuid
        self.id = id
        self.type = type

    def __str__(self):
        return f"{self.description} ({self.id})"


def is_obs_virtual_camera(source_name: str | None) -> bool:
    """OBS Studio's virtual camera, not vMix Video or a capture card.

    OpenCV's DirectShow backend special-cases the exact name "OBS Virtual
    Camera" and asks for NV12. OBS's SetFormat reports success even for a size
    the canvas is not outputting, and the preview is then black. vMix Video
    negotiates the size for real, so a name containing vmix stays on the
    normal camera path.
    """
    text = (source_name or "").casefold().replace("_", " ").replace("-", " ")
    text = " ".join(text.split())
    if "vmix" in text:
        return False
    if "obs virtual camera" in text:
        return True
    # The older obs-virtualcam plugin registers "OBS-Camera".
    if text == "obs camera" or text.startswith("obs camera "):
        return True
    # OpenCV treats this the same way as OBS Virtual Camera.
    return "streamlabs" in text and "virtual" in text


# OpenCV 4.14's DirectShow backend (cap_dshow.cpp) forces MEDIASUBTYPE_NV12
# only for these exact friendly names, then stops at the first SetFormat that
# returns S_OK. OBS returns S_OK even when the canvas did not change, so a
# FOURCC passed into VideoCapture does not select a different pixel format
# for these two names. Other names (legacy "OBS-Camera") still honour it.
_OPENCV_NV12_FORCE = frozenset(
    {
        "obs virtual camera",
        "streamlabs desktop virtual webcam",
    }
)


def _device_name_key(source_name: str | None) -> str:
    return " ".join((source_name or "").strip().casefold().split())


def opencv_forces_obs_nv12(source_name: str | None) -> bool:
    """True when OpenCV will ignore a requested FOURCC and ask for NV12."""
    return _device_name_key(source_name) in _OPENCV_NV12_FORCE


def msmf_index_for_name(names: list[str] | None, source_name: str | None) -> int | None:
    """Index of `source_name` in a Media Foundation friendly-name list.

    Exact match after trimming and collapsing spaces, case-insensitive.
    The first match wins when the same name is listed twice. None when the
    list is empty or the name is not in it: a DirectShow index must not be
    used in its place, because Media Foundation numbers devices differently
    and that index can be a capture card.
    """
    target = _device_name_key(source_name)
    if not names or not target:
        return None
    for index, name in enumerate(names):
        if _device_name_key(str(name)) == target:
            return index
    return None

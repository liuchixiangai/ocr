import atexit
import time
import cv2
from cyndilib.wrapper.ndi_recv import RecvColorFormat, RecvBandwidth
from cyndilib.finder import Finder
from cyndilib.receiver import Receiver, ReceiveFrameType
from cyndilib.video_frame import VideoRecvFrame
from cyndilib.metadata_frame import MetadataRecvFrame

# None of their names are used here. They were star-imported, probably so that
# PyInstaller (which can't see imports made from cyndilib's compiled code)
# bundles these submodules; keep them as plain imports for that.
import cyndilib.locks  # noqa: F401
import cyndilib.buffertypes  # noqa: F401
import cyndilib.send_frame_status  # noqa: F401
import cyndilib.callback  # noqa: F401
import cyndilib.wrapper.ndi_send  # noqa: F401
import numpy as np

from base_video_capture import BaseVideoCapture
from camera_info import CameraInfo
from sc_logging import logger


def ReceiveFrameTypeToString(frame_type: ReceiveFrameType) -> str:
    if frame_type == ReceiveFrameType.recv_audio:
        return "recv_audio"
    if frame_type == ReceiveFrameType.recv_metadata:
        return "recv_metadata"
    if frame_type == ReceiveFrameType.recv_video:
        return "recv_video"
    if frame_type == ReceiveFrameType.recv_error:
        return "recv_error"
    if frame_type == ReceiveFrameType.nothing:
        return "nothing"
    if frame_type == ReceiveFrameType.recv_status_change:
        return "recv_status_change"
    if frame_type == ReceiveFrameType.recv_buffers_full:
        return "recv_buffers_full"
    return "recv_unknown"


class NDICapture(BaseVideoCapture):
    # Created at import (get_camera_info imports this module before it lists
    # the cameras) so that NDI discovery, which runs in the NDI library's own
    # threads, has a head start before the sources are first listed.
    finder: Finder | None = Finder()
    _finder_created = time.monotonic()
    # how long after the finder is created discovery may still be finding the
    # sources that are already on the network
    DISCOVERY_WARMUP_S = 2.0

    @staticmethod
    def get_finder() -> Finder:
        if NDICapture.finder is None:
            NDICapture.finder = Finder()
            NDICapture._finder_created = time.monotonic()
        return NDICapture.finder

    @staticmethod
    def close_finder():
        """Stop NDI discovery (at exit)."""
        finder, NDICapture.finder = NDICapture.finder, None
        if finder is not None:
            try:
                finder.close()
            except Exception:
                logger.exception("Error closing the NDI finder")

    @staticmethod
    def get_camera_info_ndi(timeout: float = 1.0):
        """List the NDI sources found so far. Only while discovery is warming up
        (right after startup) and nothing was found yet does it wait, up to
        timeout seconds, for sources to appear."""
        logger.info("Getting NDI sources...")
        finder = NDICapture.get_finder()
        # update_sources() returns the current list. wait_for_sources() only
        # tells whether the list *changed* within the timeout, so it must not
        # decide whether there are sources.
        names = finder.update_sources()
        warmup_left = NDICapture.DISCOVERY_WARMUP_S - (
            time.monotonic() - NDICapture._finder_created
        )
        if not names and warmup_left > 0 and timeout > 0:
            finder.wait_for_sources(min(timeout, warmup_left))
            names = finder.update_sources()
        # create the camera info objects
        sources = [
            CameraInfo(name, name, i, CameraInfo.CameraType.NDI)
            for i, name in enumerate(names)
        ]
        logger.info(f"Found {len(sources)} NDI sources")

        return sources

    def __init__(self, id: str):
        self.receiver = None
        self.source = None
        self.video_frame = None
        self.metadata_frame = None
        self.receiver = Receiver(
            color_format=RecvColorFormat.BGRX_BGRA,
            bandwidth=RecvBandwidth.highest,
        )
        self.source = NDICapture.get_finder().get_source(id)
        self.receiver.set_source(self.source)
        self.video_frame = VideoRecvFrame()
        self.metadata_frame = MetadataRecvFrame()
        self.receiver.set_video_frame(self.video_frame)
        self.receiver.set_metadata_frame(self.metadata_frame)
        self.receiver.set_source_tally_program(True)
        self.receiver.set_source_tally_preview(False)

        logger.info(f"NDI Capture created with id {self.receiver.source.name}")

    def __del__(self):
        self.release()

    def isOpened(self):
        return self.receiver is not None

    def release(self):
        if self.receiver is not None:
            self.receiver.disconnect()
            self.receiver = None
        self.source = None
        self.video_frame = None
        self.metadata_frame = None

    def read(self):
        if self.receiver is None:
            return False, None
        if self.receiver.is_connected():
            video_grab_start_time = time.time()
            while (
                self.receiver is not None
                and self.receiver.is_connected()
                and time.time() - video_grab_start_time < 0.03
            ):
                try:
                    ret = self.receiver.receive(ReceiveFrameType.recv_all, 1000)
                except Exception as e:
                    logger.error(f"Error receiving frame: {e}")
                    time.sleep(1)
                    self.receiver.reconnect()
                    break
                if ret == ReceiveFrameType.recv_video:
                    if min(self.video_frame.xres, self.video_frame.yres) != 0:
                        # create a new uint8 numpy array of size self.video_frame.get_buffer_size()
                        frame = np.empty(
                            self.video_frame.get_buffer_size(), dtype=np.uint8
                        )
                        # copy the frame to the numpy array
                        self.video_frame.fill_p_data(frame)
                        # create a new numpy array with the frame data
                        frame = frame.reshape(
                            self.video_frame.yres, self.video_frame.xres, 4
                        )
                        frame = cv2.cvtColor(frame, cv2.COLOR_RGBA2RGB)
                        return True, frame
                    else:
                        logger.error("NDI Capture video frame is empty")
                elif ret == ReceiveFrameType.recv_metadata:
                    # log the dictionary
                    logger.debug("Metadata: " + str(self.metadata_frame.attrs))
                    # skip metadata
                    continue
                elif ret == ReceiveFrameType.recv_audio:
                    # skip audio
                    continue
                else:
                    logger.error(
                        "NDI Capture did not return video: "
                        + ReceiveFrameTypeToString(ret)
                    )
                    if ret == ReceiveFrameType.recv_error:
                        self.receiver.reconnect()
        else:
            logger.error("NDI Capture is not connected")
            time.sleep(1)
            self.receiver.reconnect()

        return False, None

    def set(self, propId, value):
        pass

    def get(self, propId):
        return 0.0


# stop NDI discovery when the app exits (after the capture threads stopped)
atexit.register(NDICapture.close_finder)

import cv2
import numpy as np

from sc_logging import logger


# This class is used to stabilize the frames of the video.
# It uses ORB features to match keypoints between frames and calculate an affine transform to
# warp the frame.
#
# stabilize_frame() runs on the capture thread; reset() may be called from the
# GUI thread, so it only sets a flag that the capture thread acts on.
class FrameStabilizer:
    # number of frames averaged into the reference frame
    BURN_IN_FRAMES = 10
    # fewer keypoints/matches than this and the frame is passed through unchanged
    MIN_MATCHES = 4

    def __init__(self):
        self.stabilizationFrame = None
        self.stabilizationFrameCount = 0
        self.stabilizationBurnInCompleted = False
        self.stabilizationKPs = None
        self.stabilizationDesc = None
        self.orb = None
        self.matcher = None
        self._accumulator = None
        self._reset_requested = False

    def reset(self):
        """Start over with a new reference frame (thread-safe: the state is
        cleared by the next stabilize_frame call)."""
        self._reset_requested = True

    def _clear(self):
        self.stabilizationFrame = None
        self.stabilizationFrameCount = 0
        self.stabilizationBurnInCompleted = False
        self.stabilizationKPs = None
        self.stabilizationDesc = None
        self._accumulator = None

    def stabilize_frame(self, frame_rgb):
        if self._reset_requested:
            self._reset_requested = False
            self._clear()
        if self._accumulator is not None and frame_rgb.shape != self._accumulator.shape:
            # the frame size changed (rotation, crop, resolution): start over
            self._clear()

        if not self.stabilizationBurnInCompleted:
            self._burn_in(frame_rgb)
            # the frame is passed through unchanged until the reference is ready
            return frame_rgb

        try:
            return self._align(frame_rgb)
        except cv2.error:
            logger.debug("Frame stabilization failed for this frame", exc_info=True)
            return frame_rgb

    def _burn_in(self, frame_rgb):
        # average the first frames into the reference frame
        if self._accumulator is None:
            self._accumulator = frame_rgb.astype(np.float32)
            self.stabilizationFrameCount = 1
        else:
            self.stabilizationFrameCount += 1
            cv2.accumulateWeighted(
                frame_rgb, self._accumulator, 1.0 / self.stabilizationFrameCount
            )
        if self.stabilizationFrameCount < self.BURN_IN_FRAMES:
            return

        reference = cv2.convertScaleAbs(self._accumulator)
        if self.orb is None:
            self.orb = cv2.ORB_create()
            self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        kps, desc = self.orb.detectAndCompute(reference, None)
        if desc is None or len(kps) < self.MIN_MATCHES:
            # nothing to lock on to (e.g. a black frame): try again with new frames
            self._clear()
            return
        self.stabilizationFrame = reference
        self.stabilizationKPs = kps
        self.stabilizationDesc = desc
        self.stabilizationBurnInCompleted = True

    def _align(self, frame_rgb):
        reference_kps = self.stabilizationKPs
        reference_desc = self.stabilizationDesc
        if reference_kps is None or reference_desc is None:
            return frame_rgb
        # extract features from the current frame
        kps, desc = self.orb.detectAndCompute(frame_rgb, None)
        if desc is None or len(kps) < self.MIN_MATCHES:
            # featureless frame (e.g. a cut to black): nothing to align
            return frame_rgb
        # match the features
        matches = self.matcher.match(reference_desc, desc)
        if len(matches) < self.MIN_MATCHES:
            return frame_rgb
        # calculate an affine transform from the matched keypoints
        src_pts = np.float32([reference_kps[m.queryIdx].pt for m in matches]).reshape(
            -1, 1, 2
        )
        dst_pts = np.float32([kps[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
        h, _ = cv2.estimateAffinePartial2D(src_pts, dst_pts)
        if h is None:
            return frame_rgb
        # warp the frame
        return cv2.warpAffine(
            frame_rgb,
            h,
            (frame_rgb.shape[1], frame_rgb.shape[0]),
            flags=cv2.WARP_INVERSE_MAP | cv2.INTER_LINEAR,
        )

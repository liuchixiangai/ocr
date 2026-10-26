"""FrameStabilizer (src/frame_stabilizer.py)."""

import threading

import cv2
import numpy as np

from frame_stabilizer import FrameStabilizer


def textured_frame(shift=(0, 0), size=(240, 320)):
    rng = np.random.default_rng(1)
    base = (rng.random((size[0] // 8, size[1] // 8)) * 255).astype(np.uint8)
    base = cv2.resize(base, (size[1], size[0]), interpolation=cv2.INTER_NEAREST)
    frame = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)
    matrix = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
    return cv2.warpAffine(frame, matrix, (size[1], size[0]))


def burn_in(stabilizer, frame):
    for _ in range(FrameStabilizer.BURN_IN_FRAMES):
        out = stabilizer.stabilize_frame(frame)
        # the frames are passed through unchanged, not blended
        assert out is frame
    assert stabilizer.stabilizationBurnInCompleted


def test_burn_in_passes_frames_through():
    stabilizer = FrameStabilizer()
    first = textured_frame()
    later = textured_frame(shift=(3, 0))
    assert stabilizer.stabilize_frame(first) is first
    assert stabilizer.stabilize_frame(later) is later


def test_aligns_a_shifted_frame():
    stabilizer = FrameStabilizer()
    reference = textured_frame()
    burn_in(stabilizer, reference)
    aligned = stabilizer.stabilize_frame(textured_frame(shift=(6, 4)))
    error = np.abs(aligned[40:-40, 40:-40].astype(int) - reference[40:-40, 40:-40])
    assert error.mean() < 10


def test_featureless_frames_do_not_raise():
    stabilizer = FrameStabilizer()
    burn_in(stabilizer, textured_frame())
    black = np.zeros((240, 320, 3), np.uint8)
    assert stabilizer.stabilize_frame(black) is black
    flat = np.full((240, 320, 3), 128, np.uint8)
    assert stabilizer.stabilize_frame(flat) is flat


def test_featureless_reference_retries_burn_in():
    stabilizer = FrameStabilizer()
    black = np.zeros((240, 320, 3), np.uint8)
    for _ in range(FrameStabilizer.BURN_IN_FRAMES * 2):
        assert stabilizer.stabilize_frame(black) is black
    assert not stabilizer.stabilizationBurnInCompleted
    burn_in(stabilizer, textured_frame())


def test_frame_size_change_starts_over():
    stabilizer = FrameStabilizer()
    burn_in(stabilizer, textured_frame())
    rotated = np.ascontiguousarray(np.rot90(textured_frame()))
    assert stabilizer.stabilize_frame(rotated) is rotated
    assert not stabilizer.stabilizationBurnInCompleted
    # also during burn-in
    stabilizer.stabilize_frame(textured_frame(size=(100, 100)))
    stabilizer.stabilize_frame(textured_frame(size=(120, 160)))


def test_reset_from_another_thread_is_applied_by_the_next_frame():
    stabilizer = FrameStabilizer()
    burn_in(stabilizer, textured_frame())
    resetter = threading.Thread(target=stabilizer.reset)
    resetter.start()
    resetter.join()
    # the state is cleared by the stabilizing thread, never in the middle of a frame
    assert stabilizer.stabilizationBurnInCompleted
    frame = textured_frame(shift=(5, 0))
    assert stabilizer.stabilize_frame(frame) is frame
    assert not stabilizer.stabilizationBurnInCompleted

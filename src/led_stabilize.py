"""LED scoreboard stability helpers for 光为 OCR.

Inspired by Score2Stream's image-stack / wait-to-match ideas (MIT):
- LedPatchStack: fuse the last N gray (or BGR) crops with max or median
  before OCR, to reduce LED scanline / PWM flicker.
- LedValueStabilizer: only accept a new OCR value after it has been read
  unchanged for at least X milliseconds (debounce before publish).

Settings (per box, via defaults.normalize_settings_dict):
  led_stack_frames: int 0..15 (0 = off)
  led_stack_mode: "max" | "median"
  led_stable_ms: int 0..5000 (0 = off)
"""

from __future__ import annotations

from collections import deque
import time

import numpy as np


def clamp_stack_frames(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(15, n))


def clamp_stable_ms(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(5000, n))


def normalize_stack_mode(value) -> str:
    mode = str(value or "max").strip().lower()
    return mode if mode in ("max", "median") else "max"


class LedPatchStack:
    """Per-box rolling stack of same-size patches; returns fused image."""

    def __init__(self):
        self._frames: deque[np.ndarray] = deque()
        self._shape = None
        self._dtype = None

    def clear(self):
        self._frames.clear()
        self._shape = None
        self._dtype = None

    def push_and_fuse(self, patch: np.ndarray, n_frames: int, mode: str = "max"):
        """Append patch; return fused image when n_frames >= 2, else patch.

        Patches that change size reset the stack (box was moved/resized).
        """
        n = clamp_stack_frames(n_frames)
        if n < 2 or patch is None or not hasattr(patch, "shape"):
            return patch
        shape = tuple(patch.shape)
        if self._shape != shape or self._dtype != patch.dtype:
            self.clear()
            self._shape = shape
            self._dtype = patch.dtype
        # store a copy so later OCR preprocessing can't mutate history
        self._frames.append(np.array(patch, copy=True))
        while len(self._frames) > n:
            self._frames.popleft()
        if len(self._frames) == 1:
            return patch
        stack = np.stack(list(self._frames), axis=0)
        mode = normalize_stack_mode(mode)
        if mode == "median":
            fused = np.median(stack, axis=0)
        else:
            fused = np.max(stack, axis=0)
        if np.issubdtype(patch.dtype, np.integer):
            fused = np.clip(fused, 0, np.iinfo(patch.dtype).max)
        return fused.astype(patch.dtype, copy=False)


class LedValueStabilizer:
    """Debounce OCR values: publish only after stable for led_stable_ms."""

    def __init__(self, clock=None):
        self._clock = clock or time.monotonic
        self._candidate: str | None = None
        self._candidate_since: float | None = None
        self._published: str | None = None

    def clear(self):
        self._candidate = None
        self._candidate_since = None
        self._published = None

    def stabilize(self, text: str, stable_ms: int) -> tuple[str, bool]:
        """Return (text_to_use, is_stable_enough).

        When stable_ms is 0, returns (text, True) immediately.
        When a new candidate appears, keep returning the previously published
        value (or the candidate if none yet) with is_stable_enough=False until
        the hold time elapses; then publish the candidate.
        """
        ms = clamp_stable_ms(stable_ms)
        if ms <= 0:
            self._published = text
            self._candidate = text
            self._candidate_since = self._clock()
            return text, True

        now = self._clock()
        if text != self._candidate:
            self._candidate = text
            self._candidate_since = now

        # First ever reading: publish immediately so a single-frame self-test /
        # cold start still works; debounce only applies to *changes*.
        if self._published is None:
            self._published = self._candidate
            return self._candidate or "", True

        since = now if self._candidate_since is None else self._candidate_since
        elapsed_ms = (now - since) * 1000.0
        if elapsed_ms >= ms:
            self._published = self._candidate
            return self._candidate or "", True

        # not yet stable: hold last published
        return self._published, False

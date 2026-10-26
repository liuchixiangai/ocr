from collections import Counter, deque
import enum

import numpy as np
from PySide6.QtCore import QRectF

from led_stabilize import LedPatchStack, LedValueStabilizer


class OCRResultPerCharacterSmoother:
    # This class is used to smooth the OCR results of a box over time.
    # It holds the last n accepted OCR results (strings) and returns the most
    # common one. Whole strings are compared, not characters, so a change in
    # the number of digits (9 -> 10) can't produce a mix of old and new digits.

    def __init__(self, max_history=5):
        self.max_history = max_history
        self.history: deque[str] = deque(maxlen=max_history)

    def get_smoothed_result(self, result: str) -> str:
        """Add result to the history and return the most common recent result.

        Ties go to the most recent value. An empty result (a failed read) is not
        added, so it can't come back as the output later.
        """
        if result:
            self.history.append(result)
        if not self.history:
            return result
        counts = Counter(self.history)
        best = None
        best_key = None
        for position, value in enumerate(self.history):
            key = (counts[value], position)
            if best_key is None or key > best_key:
                best, best_key = value, key
        return best

    def clear(self):
        self.history.clear()


class TextDetectionTarget(QRectF):
    # number of recent character width/height ratios used for normalize_wh_ratio
    WH_RATIO_WINDOW = 50

    def __init__(
        self,
        x,
        y,
        width,
        height,
        name: str,
        settings: dict | None = None,
        mini_rects: list[QRectF] | None = None,
    ):
        super().__init__(x, y, width, height)
        self.name = name
        self.settings = settings if settings is not None else {}
        self.ocrResultPerCharacterSmoother = OCRResultPerCharacterSmoother()
        self.last_image = None
        self.last_text = None
        # when the box started to be blank ("When blank", camera_thread)
        self.blank_since = None
        self.mini_rects: list[QRectF] = mini_rects if mini_rects is not None else []
        # Runtime OCR state for normalize_wh_ratio (not persisted): the recent
        # character width/height ratios and the settings they were measured with.
        self.wh_ratios: deque[float] = deque(maxlen=self.WH_RATIO_WINDOW)
        self.wh_ratios_key = None
        # 光为 OCR LED helpers (runtime only, not persisted)
        self.led_patch_stack = LedPatchStack()
        self.led_value_stabilizer = LedValueStabilizer()

    def median_wh_ratio(self, key) -> float | None:
        """The median of the recent character width/height ratios measured with
        the settings identified by key, or None if there are none yet. A
        different key (the box's preprocessing changed) starts over."""
        if key != self.wh_ratios_key:
            self.wh_ratios.clear()
            self.wh_ratios_key = key
        if not self.wh_ratios:
            return None
        return float(np.median(self.wh_ratios))

    def add_wh_ratios(self, key, ratios):
        if key != self.wh_ratios_key:
            self.wh_ratios.clear()
            self.wh_ratios_key = key
        self.wh_ratios.extend(ratios)


class TextDetectionTargetWithResult(TextDetectionTarget):
    class ResultState(enum.Enum):
        Success = 0
        FailedFilter = 1
        Empty = 2
        SameNoChange = 3

    def __init__(
        self,
        detection_target: TextDetectionTarget,
        result: str,
        result_state: ResultState,
        effectiveRect=None,
        extras=None,
    ):
        super().__init__(
            detection_target.x(),
            detection_target.y(),
            detection_target.width(),
            detection_target.height(),
            detection_target.name,
            detection_target.settings,
        )
        self.result = result
        self.result_state = result_state
        self.effectiveRect = effectiveRect
        self.extras = extras

    def to_dict(self):
        return {
            "name": self.name,
            "text": self.result,
            "state": self.result_state.name,
            "rect": {
                "x": self.x(),
                "y": self.y(),
                "width": self.width(),
                "height": self.height(),
            },
        }


class TextDetectionResult:
    def __init__(
        self,
        text: str,
        state: TextDetectionTargetWithResult.ResultState,
        rect: QRectF | None = None,
        extra=None,
        *,
        unchanged_image: bool = False,
    ):
        self.text = text
        self.state = state
        self.rect = rect
        self.extra = extra
        # The OCR was skipped because the box's image hasn't changed since its
        # last OCR (skip_similar_image). There is no text; the capture thread
        # reports the box's last good reading as SameNoChange instead.
        self.unchanged_image = unchanged_image

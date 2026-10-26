"""Run Python's cyclic garbage collector on the GUI thread only.

Python normally collects reference cycles on whichever thread happens to be
allocating when a generation's threshold is reached: often a background thread
(the HTTP server, the capture thread, an output worker, source discovery).
Collecting there can finalize Qt wrapper objects while the GUI thread is using
Qt, which crashes in native code. A CI run caught exactly that: an access
violation while the HTTP server thread was garbage-collecting (during an
import) and the GUI thread was replacing the vMix table's model.

So the app turns the automatic collection off and runs it from a timer on the
GUI thread, with the same per-generation thresholds (pyqtgraph does the same).
"""

import gc

from PySide6.QtCore import QObject, QTimer

# How often the GUI thread checks whether a collection is due.
INTERVAL_MS = 500


class GuiThreadGarbageCollector(QObject):
    """Disables the automatic garbage collection and collects from a timer on
    the thread this object lives in (create it on the GUI thread)."""

    def __init__(self, parent: QObject | None = None, interval_ms: int = INTERVAL_MS):
        super().__init__(parent)
        self.threshold = gc.get_threshold()
        gc.disable()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check)
        self.timer.start(interval_ms)

    def check(self) -> int:
        """Collect the generations whose allocation counts are over their
        thresholds, as the automatic collector would. Returns the number of
        unreachable objects found."""
        count0, count1, count2 = gc.get_count()
        if count0 <= self.threshold[0]:
            return 0
        found = gc.collect(0)
        if count1 > self.threshold[1]:
            found += gc.collect(1)
            if count2 > self.threshold[2]:
                found += gc.collect(2)
        return found

    def stop(self):
        """Stop collecting from the timer and turn the automatic collection
        back on."""
        self.timer.stop()
        gc.enable()

"""Plausibility holds: with a sport chosen, a change the sport doesn't allow
(a basketball score going from 21 to 81, a score going down, a period
skipped, the clock jumping) doesn't go on air until the game follows it:
until it has been read steadily for game_state.HOLD_SECONDS. A misread that
flickers is never sent; a real correction is, a few seconds late. Changes the
sport allows go out at once.

PlausibilityHolds sits between the OCR and the outputs, like Hold value
(held_values.py): MainWindow.ocrResult asks the game (GameEngine.held_back)
which readings to hold back, after Hold value and before the template fields
are filled in, so the templates and every output keep the value they had:
- while a field is held back, its result is replaced by the last good value
  the outputs got, as a SameNoChange (for them nothing changed);
- after that, the field's next good reading is sent as a Success, even when
  the OCR reports it unchanged: the outputs had the old value.
Fields under Hold value and template fields aren't held back: the operator's
value is sent as it is, and a template is built from the values sent. The
OCR's readings themselves aren't changed (the boxes list shows them), and the
game follows them. Each hold is logged: what was held back and why, then
whether it was sent or dropped.
"""

from sc_logging import logger
from text_detection_target import TextDetectionTargetWithResult

ResultState = TextDetectionTargetWithResult.ResultState
GOOD_STATES = (ResultState.Success, ResultState.SameNoChange)


def _replaced(
    result: TextDetectionTargetWithResult, text: str, state: ResultState
) -> TextDetectionTargetWithResult:
    """A copy of result with another text and state."""
    return TextDetectionTargetWithResult(
        result, text, state, result.effectiveRect, result.extras
    )


class PlausibilityHolds:
    """The fields held back, by name, and the last good value sent of each."""

    def __init__(self):
        # the last good value the outputs got, by field
        self._sent: dict[str, str] = {}
        # the fields held back: the reading that started the hold
        self._held: dict[str, str] = {}

    def __contains__(self, name: str) -> bool:
        return name in self._held

    def held(self) -> dict[str, str]:
        """{field name: the value the outputs keep} of the fields held back."""
        return {name: self._sent[name] for name in self._held}

    def apply(
        self,
        results: list[TextDetectionTargetWithResult],
        reasons: dict[str, str],
    ) -> list[TextDetectionTargetWithResult]:
        """The results to send: those named in reasons ({field name: why}, from
        GameEngine.held_back) replaced by the field's last good value sent,
        and the first good reading after a hold made a Success. The other
        results are the same objects."""
        present = {result.name for result in results}
        for name in [name for name in self._sent if name not in present]:
            # deleted or renamed: a field given its name later starts fresh
            del self._sent[name]
            self._held.pop(name, None)
        sent = []
        for result in results:
            name = result.name
            good = result.result_state in GOOD_STATES and result.result is not None
            text = str(result.result) if good else None
            if name in reasons and name in self._sent:
                if name not in self._held:
                    self._held[name] = text
                    logger.info(
                        "Plausibility hold: %s read %s, %s kept: %s; it is sent "
                        "once read steadily for a few seconds",
                        name,
                        text,
                        self._sent[name],
                        reasons[name],
                    )
                result = _replaced(result, self._sent[name], ResultState.SameNoChange)
            elif good:
                if name in self._held:
                    first = self._held.pop(name)
                    if text == first:
                        logger.info(
                            "Plausibility hold: %s read %s steadily, sent", name, text
                        )
                    elif text == self._sent[name]:
                        logger.info(
                            "Plausibility hold: %s back to %s, %s dropped",
                            name,
                            text,
                            first,
                        )
                    else:
                        logger.info(
                            "Plausibility hold: %s read %s, sent (%s dropped)",
                            name,
                            text,
                            first,
                        )
                    if result.result_state != ResultState.Success:
                        result = _replaced(result, text, ResultState.Success)
                self._sent[name] = text
            sent.append(result)
        return sent

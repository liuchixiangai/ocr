"""Hold value: send a value typed by the operator to every output instead of a
field's readings, for example to correct a misread during a game, until it is
released.

HeldValues sits between the OCR and the outputs (MainWindow.ocrResult calls
apply() before the template fields are filled in, then finish_released()
after), so the templates and every output see the held value:
- while a field is held, its result is replaced by the held value: a Success
  the first time (and after the held value is changed), a SameNoChange after
  that, like a reading that doesn't change;
- after it is released, the field's next good reading is sent as a Success,
  even when the OCR reports it unchanged (SameNoChange): the outputs and the
  templates had the held value, so for them the reading is new.
The capture thread's state isn't touched, so the OCR goes on as if nothing was
held. The held values are not saved: they end with the session.

HoldValueDialog asks for the value to hold.
"""

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from template_fields import is_template_field
from text_detection_target import TextDetectionTargetWithResult

ResultState = TextDetectionTargetWithResult.ResultState
GOOD_STATES = (ResultState.Success, ResultState.SameNoChange)


def _replaced(
    result: TextDetectionTargetWithResult,
    text: str,
    state: ResultState,
    as_template: bool = True,
) -> TextDetectionTargetWithResult:
    """A copy of result with another text and state (the OCR's result object
    isn't changed: the viewer shows the reading itself). With as_template
    False, a template field's copy isn't evaluated as a template."""
    settings = result.settings
    if not as_template and is_template_field(result):
        settings = dict(settings, templatefield=False)
    copy = TextDetectionTargetWithResult(
        result, text, state, result.effectiveRect, result.extras
    )
    copy.settings = settings
    return copy


class HeldValues:
    """The fields whose value is held, by name."""

    def __init__(self):
        self._values: dict[str, str] = {}
        # the held values that were sent (as a Success) already
        self._sent: set[str] = set()
        # released fields whose next good reading is sent as a Success
        self._released: set[str] = set()

    def __contains__(self, name: str) -> bool:
        return name in self._values

    def __bool__(self) -> bool:
        return bool(self._values)

    def value(self, name: str) -> str | None:
        """The value held for a field, or None if it isn't held."""
        return self._values.get(name)

    def values(self) -> dict[str, str]:
        """{field name: held value} of every held field."""
        return dict(self._values)

    def hold(self, name: str, value: str):
        """Send value instead of the field's readings (from the next cycle)."""
        value = "" if value is None else str(value)
        if self._values.get(name) != value:
            # a new value: sent as a Success once
            self._sent.discard(name)
        self._values[name] = value
        self._released.discard(name)

    def release(self, name: str) -> bool:
        """Send the field's readings again; False if it wasn't held."""
        if name not in self._values:
            return False
        del self._values[name]
        self._sent.discard(name)
        self._released.add(name)
        return True

    def forget(self, name: str):
        """The field is gone (deleted or taken off the picture): drop its hold
        without anything to send."""
        self._values.pop(name, None)
        self._sent.discard(name)
        self._released.discard(name)

    def rename(self, old_name: str, new_name: str):
        if old_name in self._values:
            self._values[new_name] = self._values.pop(old_name)
            if old_name in self._sent:
                self._sent.discard(old_name)
                self._sent.add(new_name)
        if old_name in self._released:
            self._released.discard(old_name)
            self._released.add(new_name)

    def apply(
        self, results: list[TextDetectionTargetWithResult]
    ) -> list[TextDetectionTargetWithResult]:
        """The results to send: the held fields' results replaced by their
        held values, and the first good reading of a released field (other
        than a template field, see finish_released) made a Success. The other
        results are the same objects. Call it before the template fields are
        filled in: a held template field is then sent as it is, not
        evaluated."""
        if not self._values and not self._released:
            return results
        sent = []
        for result in results:
            name = result.name
            if name in self._values:
                state = (
                    ResultState.SameNoChange
                    if name in self._sent
                    else ResultState.Success
                )
                self._sent.add(name)
                result = _replaced(result, self._values[name], state, as_template=False)
            elif (
                name in self._released
                and not is_template_field(result)
                and result.result_state in GOOD_STATES
            ):
                self._released.discard(name)
                if result.result_state != ResultState.Success:
                    result = _replaced(result, result.result, ResultState.Success)
            sent.append(result)
        return sent

    def finish_released(self, results: list[TextDetectionTargetWithResult]):
        """After the template fields are filled in: a released template field
        with a good value is sent as a Success (in place, like the template
        fields themselves)."""
        if not self._released:
            return
        for result in results:
            if (
                result.name in self._released
                and is_template_field(result)
                and result.result_state in GOOD_STATES
            ):
                self._released.discard(result.name)
                result.result_state = ResultState.Success


# the dialog's result when the user chose Release
RELEASE = 2


class HoldValueDialog(QDialog):
    """Asks for the value to hold for a field, pre-filled with its current
    value. exec() returns Accepted (hold value()), RELEASE (only offered
    while the field is held) or Rejected."""

    def __init__(self, field_name: str, value: str, held: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(
            QCoreApplication.translate("HoldValue", "Hold value: {name}").format(
                name=field_name
            )
        )
        layout = QVBoxLayout(self)
        explanation = QLabel(
            QCoreApplication.translate(
                "HoldValue",
                "Every output gets this value instead of the readings of "
                "“{name}”, until you release it. The readings go on in the "
                "background.",
            ).format(name=field_name),
            self,
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.lineEdit = QLineEdit(value, self)
        self.lineEdit.setAccessibleName(
            QCoreApplication.translate("HoldValue", "Value to send")
        )
        self.lineEdit.selectAll()
        layout.addWidget(self.lineEdit)
        buttons = QDialogButtonBox(self)
        self.holdButton = buttons.addButton(
            QCoreApplication.translate("HoldValue", "Hold"),
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.releaseButton = None
        if held:
            self.releaseButton = buttons.addButton(
                QCoreApplication.translate("HoldValue", "Release"),
                QDialogButtonBox.ButtonRole.ActionRole,
            )
            self.releaseButton.clicked.connect(lambda: self.done(RELEASE))
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.holdButton.setDefault(True)
        self.setMinimumWidth(360)
        self.setWindowModality(Qt.WindowModality.WindowModal)

    def value(self) -> str:
        return self.lineEdit.text()

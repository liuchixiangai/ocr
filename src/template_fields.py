"""Template fields: custom fields whose value is built from other fields, for
example "{{Home Score}} - {{Away Score}}".

Semantics (see TemplateFieldEvaluator):
- {{Name}} is replaced with the last good reading (Success) of the box Name, so
  a rejected or empty reading (e.g. "SIM" from skip-similar-image, or text that
  failed the box's format) never ends up in a template; the template keeps
  showing the last good value instead.
- {{Name}} may reference another template field. Circular references can't be
  evaluated.
- Placeholders that don't name a box are left as they are.
- Until every referenced box has had a good reading, the template is Empty
  (text ""). After that it is Success when its text changed since the previous
  cycle and SameNoChange when it didn't, like a box with "update on change".
"""

import re

from text_detection_target import TextDetectionTargetWithResult

ResultState = TextDetectionTargetWithResult.ResultState

PLACEHOLDER = re.compile(r"\{\{(.+?)\}\}")


def is_template_field(result: TextDetectionTargetWithResult) -> bool:
    return bool(result.settings and result.settings.get("templatefield"))


def _template_text(result: TextDetectionTargetWithResult) -> str:
    return (result.settings or {}).get("templatefield_text") or ""


class TemplateFieldEvaluator:
    """Evaluates template fields from the readings of successive OCR cycles."""

    def __init__(self):
        # last good reading of every (non-template) box, by name
        self.last_good: dict[str, str] = {}
        # text each template field had in the previous cycle
        self.last_rendered: dict[str, str] = {}

    def observe(self, results: list[TextDetectionTargetWithResult]) -> None:
        """Remember the good readings of this cycle's (non-template) boxes."""
        # forget the boxes (and templates) that were removed or renamed, so a
        # box later given one of their names starts fresh instead of showing
        # the old box's value
        present = {result.name for result in results}
        for known in (self.last_good, self.last_rendered):
            for name in [name for name in known if name not in present]:
                del known[name]
        for result in results:
            if is_template_field(result):
                continue
            state = result.result_state
            # SameNoChange repeats the previous reading of the box, which may have
            # been a rejected one, so it only counts when nothing better is known
            if state == ResultState.Success or (
                state == ResultState.SameNoChange and result.name not in self.last_good
            ):
                self.last_good[result.name] = (
                    "" if result.result is None else str(result.result)
                )

    def _render_all(
        self, results: list[TextDetectionTargetWithResult]
    ) -> dict[str, str | None]:
        """{template name: text, or None if it can't be evaluated (yet)}."""
        templates = {r.name: r for r in results if is_template_field(r)}
        box_names = {r.name for r in results}
        rendered: dict[str, str | None] = {}

        def resolve(name: str, visiting: frozenset) -> str | None:
            if name in rendered:
                return rendered[name]
            if name in visiting:  # circular reference
                return None
            template = _template_text(templates[name])
            if not template:
                rendered[name] = None
                return None
            missing = False

            def substitute(match: re.Match) -> str:
                nonlocal missing
                ref = match.group(1)
                if ref in templates:
                    value = resolve(ref, visiting | {name})
                elif ref in box_names:
                    value = self.last_good.get(ref)
                else:
                    return match.group(0)  # no such box: leave it as it is
                if value is None:
                    missing = True
                    return match.group(0)
                return value

            text = PLACEHOLDER.sub(substitute, template)
            rendered[name] = None if missing else text
            return rendered[name]

        for name in templates:
            resolve(name, frozenset())
        return rendered

    def apply(self, results: list[TextDetectionTargetWithResult]) -> None:
        """Set result and result_state of every template field in results."""
        self.observe(results)
        rendered = self._render_all(results)
        for result in results:
            if not is_template_field(result):
                continue
            text = rendered.get(result.name)
            if text is None:
                result.result = ""
                result.result_state = ResultState.Empty
                self.last_rendered.pop(result.name, None)
                continue
            result.result = text
            result.result_state = (
                ResultState.SameNoChange
                if self.last_rendered.get(result.name) == text
                else ResultState.Success
            )
            self.last_rendered[result.name] = text

    def evaluate(
        self,
        results: list[TextDetectionTargetWithResult],
        template_field: TextDetectionTargetWithResult,
    ) -> str | None:
        """The text of one template field for this cycle, or None if its template
        is empty or it can't be evaluated yet. Doesn't change any result."""
        self.observe(results)
        rendered = self._render_all(results)
        if template_field.name in rendered:
            return rendered[template_field.name]
        # not in results: evaluate it on its own
        return self._render_all([*results, template_field]).get(template_field.name)


# The evaluator used by the app: one per process, fed every OCR cycle.
_evaluator = TemplateFieldEvaluator()


def apply_template_fields(results: list[TextDetectionTargetWithResult]) -> None:
    """Evaluate the template fields in this cycle's results in place: set their
    text and their state (Success, SameNoChange or Empty)."""
    _evaluator.apply(results)


def evaluate_template_field(
    results: list[TextDetectionTargetWithResult],
    template_field: TextDetectionTargetWithResult,
):
    """The text of template_field for this cycle, or None if its template is
    empty or a box it references has had no good reading yet.

    Prefer apply_template_fields(), which also sets the result state."""
    return _evaluator.evaluate(results, template_field)

"""How far to trust one analysis, and when to hand it to a person instead.

The confidence is the level an analysis has earned from every signal behind
it, never more than its weakest part:

* **The classification** - the top rule's confidence, or retry history's. With
  nothing to classify by, it is low: v1 makes no claim about what it cannot
  place, and a diagnosis no rule corroborates is the model's word alone.
* **The model's own rating** of its diagnosis, when there is one.
* **Two signs of trouble**, each costing one level: a diagnosis that cited
  lines which are not in the evidence, and a model that places the failure in a
  different class from the rules.

It stays an ordinal - high, medium, low - like every confidence in this tool.
A decimal would claim a precision nothing here was calibrated to; `make eval`
reports how often each level is right instead, which is the evidence the level
can actually be held to.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Confidence, FailureClass, Report
from .taxonomy import classify, model_disagreement

_ORDER: tuple[Confidence, ...] = (Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH)

#: An analysis below this is presented as needing human review, which makes
#: low the only level under it. It is where the corpus draws the line: `make
#: eval` reports class accuracy per level, and low is the one that is wrong
#: more often than right. The JSON report carries the level as well as the
#: flag, so a consumer that wants a stricter bar can apply one.
REVIEW_BELOW = Confidence.MEDIUM


def rank(confidence: Confidence) -> int:
    """Low 0, medium 1, high 2: for comparing levels, never for display."""
    return _ORDER.index(confidence)


def _lower(confidence: Confidence) -> Confidence:
    return _ORDER[max(0, rank(confidence) - 1)]


@dataclass(frozen=True, slots=True)
class Assessment:
    confidence: Confidence
    #: Every reason the confidence is below high, in the order they applied.
    #: Empty exactly when the confidence is high.
    reasons: tuple[str, ...] = ()

    @property
    def needs_review(self) -> bool:
        return rank(self.confidence) < rank(REVIEW_BELOW)


def assess(report: Report) -> Assessment:
    """Derived on demand from the report, like the classification and the cost."""
    classification = classify(report)
    level = classification.confidence
    reasons: list[str] = []

    rule = report.top_hit.rule if classification.source == "rules" and report.top_hit else None
    if rule is not None and rule.confidence is not Confidence.HIGH:
        reasons.append(f"{rule.id} is a {rule.confidence}-confidence rule")
    # Below the rule's own level - a transient rule that failed every attempt -
    # or with no rule behind it at all, the basis is what explains the level.
    if level is not Confidence.HIGH and (rule is None or rank(level) < rank(rule.confidence)):
        reasons.append(classification.basis)

    if (diagnosis := report.diagnosis) is not None:
        if diagnosis.confidence is not Confidence.HIGH:
            reasons.append(f"the model rated its diagnosis {diagnosis.confidence}")
            if rank(diagnosis.confidence) < rank(level):
                level = diagnosis.confidence
        if diagnosis.unresolved_citations:
            invented = ", ".join(f"L{n}" for n in diagnosis.unresolved_citations)
            reasons.append(f"the diagnosis cited {invented}, which is not in the evidence")
            level = _lower(level)
        # Only a disagreement with a class the rules actually chose. Against
        # `unclassified` the model is not contradicting anything, and the low
        # classification has already said what there is to say.
        other = model_disagreement(report)
        if other is not None and classification.failure_class is not FailureClass.UNCLASSIFIED:
            reasons.append(
                f"the model reads it as {other}, the rules as {classification.failure_class}"
            )
            level = _lower(level)

    return Assessment(confidence=level, reasons=tuple(reasons))

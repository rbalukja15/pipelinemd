"""How far to trust an analysis (#18): its confidence, and when it needs a person."""

from __future__ import annotations

import itertools
from dataclasses import replace

import pytest

from pipelinemd.assessment import REVIEW_BELOW, assess, rank
from pipelinemd.distill import distill
from pipelinemd.models import (
    Category,
    Citation,
    Confidence,
    Diagnosis,
    FailureClass,
    JobRef,
    Report,
    RetryHistory,
    RuleHit,
)
from pipelinemd.rules import get_rule
from pipelinemd.taxonomy import classify

HIGH, MEDIUM, LOW = Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW

# One rule per confidence level, all placed in a v1 class.
HIGH_RULE = "npm.eresolve"  # test
MEDIUM_RULE = "runner.canceled"  # runner
LOW_RULE = "ci.cache-failed"  # cache_artifact


def hit(rule_id: str) -> RuleHit:
    rule = get_rule(rule_id)
    assert rule is not None, rule_id
    return RuleHit(rule=rule, line_number=1, line_text="", score=100.0)


def diagnosis(
    confidence: Confidence = HIGH,
    failure_class: FailureClass = FailureClass.TEST,
    unresolved: tuple[int, ...] = (),
) -> Diagnosis:
    return Diagnosis(
        summary="s",
        root_cause="r",
        confidence=confidence,
        category=Category.DEPENDENCY,
        failure_class=failure_class,
        citations=(Citation(line_number=1, text="x"),),
        unresolved_citations=unresolved,
        model="claude-opus-5",
    )


def report(
    *rule_ids: str,
    diagnosis: Diagnosis | None = None,
    retry: RetryHistory | None = None,
) -> Report:
    return Report(
        job=JobRef(name="build"),
        distilled=distill("ERROR: Job failed: exit code 1\n"),
        hits=[hit(rule_id) for rule_id in rule_ids],
        diagnosis=diagnosis,
        retry=retry,
    )


def test_the_rule_levels_these_tests_rely_on() -> None:
    """If the catalog re-rates one of these, the tests below stop meaning anything."""
    assert [get_rule(r).confidence for r in (HIGH_RULE, MEDIUM_RULE, LOW_RULE)] == [  # type: ignore[union-attr]
        HIGH,
        MEDIUM,
        LOW,
    ]


# -- rules only --------------------------------------------------------------


def test_a_high_confidence_rule_alone_is_high_and_says_nothing_more() -> None:
    result = assess(report(HIGH_RULE))
    assert result.confidence is HIGH
    assert result.reasons == ()
    assert not result.needs_review


def test_a_medium_rule_is_medium_and_names_itself_as_the_reason() -> None:
    result = assess(report(MEDIUM_RULE))
    assert result.confidence is MEDIUM
    assert result.reasons == (f"{MEDIUM_RULE} is a medium-confidence rule",)
    assert not result.needs_review


def test_a_low_rule_needs_review() -> None:
    result = assess(report(LOW_RULE))
    assert result.confidence is LOW
    assert result.needs_review
    assert result.reasons == (f"{LOW_RULE} is a low-confidence rule",)


def test_nothing_fired_needs_review_and_says_why() -> None:
    result = assess(report())
    assert result.confidence is LOW
    assert result.needs_review
    assert result.reasons == ("no rule fired",)


def test_a_rule_outside_v1_needs_review() -> None:
    result = assess(report("git.lfs-quota"))
    assert result.needs_review
    assert "outside the v1 taxonomy" in result.reasons[0]


def test_a_transient_rule_that_failed_every_attempt_gives_the_retry_reason() -> None:
    """Its level comes from the retry history, not the rule, so the basis explains it."""
    result = assess(report("net.dns", retry=RetryHistory(("failed", "failed"))))
    assert result.confidence is LOW
    assert "failed on every attempt" in result.reasons[0]


def test_a_medium_transient_rule_that_failed_every_attempt_gives_both_reasons() -> None:
    """Medium from the rule, low from the retries: each step down is explained."""
    assert get_rule("net.connection-timeout").confidence is MEDIUM  # type: ignore[union-attr]
    history = RetryHistory(("failed", "failed"))
    result = assess(report("net.connection-timeout", retry=history))
    assert result.confidence is LOW
    assert result.reasons[0] == "net.connection-timeout is a medium-confidence rule"
    assert "failed on every attempt" in result.reasons[1]


def test_a_passing_retry_is_high_whatever_the_rule_was() -> None:
    result = assess(report(LOW_RULE, retry=RetryHistory(("failed", "success"))))
    assert result.confidence is HIGH
    assert not result.needs_review


# -- with a diagnosis --------------------------------------------------------


def test_a_grounded_agreeing_high_diagnosis_on_a_high_rule_is_high() -> None:
    result = assess(report(HIGH_RULE, diagnosis=diagnosis()))
    assert result.confidence is HIGH
    assert result.reasons == ()


def test_the_models_own_rating_can_lower_the_analysis() -> None:
    result = assess(report(HIGH_RULE, diagnosis=diagnosis(confidence=MEDIUM)))
    assert result.confidence is MEDIUM
    assert result.reasons == ("the model rated its diagnosis medium",)


def test_the_model_cannot_raise_the_analysis_above_the_rules() -> None:
    """The weakest part decides: a confident model does not firm up a medium rule."""
    result = assess(report(MEDIUM_RULE, diagnosis=diagnosis(failure_class=FailureClass.RUNNER)))
    assert result.confidence is MEDIUM


def test_a_diagnosis_no_rule_corroborates_needs_review() -> None:
    """Grounded and confident, but the model's word alone - and no fix type follows."""
    result = assess(report(diagnosis=diagnosis()))
    assert result.confidence is LOW
    assert result.needs_review


def test_invented_citations_cost_one_level() -> None:
    result = assess(report(HIGH_RULE, diagnosis=diagnosis(unresolved=(41203,))))
    assert result.confidence is MEDIUM
    assert result.reasons == ("the diagnosis cited L41203, which is not in the evidence",)


def test_a_model_that_places_it_elsewhere_costs_one_level() -> None:
    result = assess(report(HIGH_RULE, diagnosis=diagnosis(failure_class=FailureClass.YAML)))
    assert result.confidence is MEDIUM
    assert result.reasons == ("the model reads it as yaml, the rules as test",)


def test_two_signs_of_trouble_send_a_high_analysis_to_review() -> None:
    bad = diagnosis(failure_class=FailureClass.YAML, unresolved=(41203,))
    result = assess(report(HIGH_RULE, diagnosis=bad))
    assert result.confidence is LOW
    assert result.needs_review
    assert len(result.reasons) == 2


def test_a_model_declining_to_classify_is_not_a_sign_of_trouble() -> None:
    result = assess(report(HIGH_RULE, diagnosis=diagnosis(failure_class=FailureClass.UNCLASSIFIED)))
    assert result.confidence is HIGH


def test_naming_a_class_where_the_rules_named_none_is_not_counted_twice() -> None:
    """Against `unclassified` the model contradicts nothing; "no rule fired" says it all."""
    result = assess(report(diagnosis=diagnosis(failure_class=FailureClass.YAML)))
    assert result.reasons == ("no rule fired",)


def test_the_assessment_follows_the_report_it_is_derived_from() -> None:
    """Derived on demand: attaching a diagnosis later changes it, with no recompute step."""
    base = report(HIGH_RULE)
    assert assess(base).confidence is HIGH
    assert assess(replace(base, diagnosis=diagnosis(confidence=LOW))).confidence is LOW


# -- invariants --------------------------------------------------------------


def test_only_low_needs_review() -> None:
    assert REVIEW_BELOW is MEDIUM
    assert [rank(level) < rank(REVIEW_BELOW) for level in (HIGH, MEDIUM, LOW)] == [
        False,
        False,
        True,
    ]


_RULES = ((HIGH_RULE,), (MEDIUM_RULE,), (LOW_RULE,), ())
_MODEL = (None, HIGH, MEDIUM, LOW)
_CLASSES = (None, FailureClass.YAML, FailureClass.UNCLASSIFIED)  # None: the rule's own


@pytest.mark.parametrize(
    ("rules", "model", "unresolved", "model_class"),
    list(itertools.product(_RULES, _MODEL, (False, True), _CLASSES)),
)
def test_every_combination_keeps_the_promises(
    rules: tuple[str, ...],
    model: Confidence | None,
    unresolved: bool,
    model_class: FailureClass | None,
) -> None:
    """Across every mix of rule level, model rating and trouble sign:

    * never above the classification, nor above the model's own rating;
    * a reason for every step below high, and none at high;
    * review exactly when low.
    """
    base = report(*rules)
    rules_level = assess(base).confidence
    diag = None
    if model is not None:
        diag = diagnosis(
            confidence=model,
            failure_class=model_class or classify(base).failure_class,
            unresolved=(41203,) if unresolved else (),
        )
    result = assess(replace(base, diagnosis=diag))

    assert rank(result.confidence) <= rank(rules_level)
    if model is not None:
        assert rank(result.confidence) <= rank(model)
    assert (result.confidence is HIGH) == (not result.reasons)
    assert result.needs_review == (result.confidence is LOW)

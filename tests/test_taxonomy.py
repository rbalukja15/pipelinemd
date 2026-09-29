"""The v1 taxonomy: which class a failure is in, and what kind of fix it wants."""

from __future__ import annotations

import pytest

from pipelinemd.distill import distill
from pipelinemd.models import (
    Category,
    Confidence,
    Diagnosis,
    FailureClass,
    FixType,
    JobRef,
    Report,
    RetryHistory,
    RetryVerdict,
    RuleHit,
)
from pipelinemd.rules import ALL_RULES, get_rule
from pipelinemd.taxonomy import (
    MODEL_CLASSES,
    RULE_CLASS,
    SUGGESTED_FIX_TYPE,
    V1_CLASSES,
    class_of,
    classify,
    classify_hits,
    coerce_failure_class,
)


def hit(rule_id: str) -> RuleHit:
    rule = get_rule(rule_id)
    assert rule is not None, rule_id
    return RuleHit(rule=rule, line_number=1, line_text="", score=100.0)


# -- the table ---------------------------------------------------------------


def test_every_rule_is_placed_in_the_taxonomy() -> None:
    """A rule added without a class would silently become unclassified."""
    unplaced = sorted(rule.id for rule in ALL_RULES if rule.id not in RULE_CLASS)
    assert not unplaced, f"add these to RULE_CLASS: {unplaced}"


def test_the_table_names_no_rule_that_does_not_exist() -> None:
    """A stale entry is a rule that was renamed or deleted; its class went with it."""
    stale = sorted(set(RULE_CLASS) - {rule.id for rule in ALL_RULES})
    assert not stale


def test_no_rule_is_flaky_unless_it_is_transient_by_nature() -> None:
    """Flaky from a rule means the failure is transient whatever the environment.

    Everything else - a timing-dependent test, a runner that fell over - is
    only flaky if another attempt passed, which is retry history's call. If
    this list grows, the new entry needs the same argument the network ones
    have.
    """
    flaky = {rule for rule, cls in RULE_CLASS.items() if cls is FailureClass.FLAKY}
    assert flaky == {"net.dns", "net.connection-refused", "net.connection-timeout"}


def test_every_class_has_a_fix_type_decision() -> None:
    assert set(SUGGESTED_FIX_TYPE) == set(FailureClass)


def test_unclassified_suggests_no_fix() -> None:
    """v1 makes no claim about what it cannot place, so it suggests nothing."""
    assert SUGGESTED_FIX_TYPE[FailureClass.UNCLASSIFIED] is None


def test_only_the_yaml_class_can_lead_to_an_automated_write() -> None:
    """`yaml_patch` is what #24's MR generator acts on.

    Handing it to a class whose fixes are mixed would invite an automated MR for
    a failure that YAML cannot fix. This is the invariant that makes the
    conservative choices in SUGGESTED_FIX_TYPE deliberate rather than timid.
    """
    writers = {cls for cls, fix in SUGGESTED_FIX_TYPE.items() if fix is FixType.YAML_PATCH}
    assert writers == {FailureClass.YAML}


def test_the_model_may_not_answer_flaky() -> None:
    """#17: flaky detection is a deterministic signal, "not LLM guess"."""
    assert FailureClass.FLAKY not in MODEL_CLASSES
    assert FailureClass.UNCLASSIFIED in MODEL_CLASSES


def test_unclassified_is_not_a_v1_class() -> None:
    assert FailureClass.UNCLASSIFIED not in V1_CLASSES
    assert len(V1_CLASSES) == 7


# -- retry history -----------------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "verdict"),
    [
        (("failed",), RetryVerdict.INCONCLUSIVE),
        (("failed", "success"), RetryVerdict.PASSED_ON_ANOTHER_ATTEMPT),
        # Order does not matter: same commit, different outcome, either way round.
        (("success", "failed"), RetryVerdict.PASSED_ON_ANOTHER_ATTEMPT),
        (("failed", "failed"), RetryVerdict.FAILED_EVERY_ATTEMPT),
        (("failed", "failed", "failed"), RetryVerdict.FAILED_EVERY_ATTEMPT),
        # A canceled attempt says nothing about whether the job can pass.
        (("failed", "canceled"), RetryVerdict.INCONCLUSIVE),
        (("canceled", "canceled"), RetryVerdict.INCONCLUSIVE),
        ((), RetryVerdict.INCONCLUSIVE),
    ],
)
def test_retry_verdict(statuses: tuple[str, ...], verdict: RetryVerdict) -> None:
    assert RetryHistory(statuses=statuses).verdict is verdict


# -- classification ----------------------------------------------------------


def test_nothing_fired_is_unclassified_and_says_so() -> None:
    result = classify_hits([])
    assert result.failure_class is FailureClass.UNCLASSIFIED
    assert result.confidence is Confidence.LOW
    assert result.fix_type is None
    assert result.basis == "no rule fired"


def test_a_rule_outside_v1_is_unclassified_but_keeps_its_name() -> None:
    """#17's acceptance criterion: unknown failures return low-confidence unclassified.

    The rule is still named, so a reader can see *which* signature fired - the
    class just declines to stretch a v1 bucket over it.
    """
    result = classify_hits([hit("git.lfs-quota")])
    assert result.failure_class is FailureClass.UNCLASSIFIED
    assert result.confidence is Confidence.LOW
    assert result.rule_id == "git.lfs-quota"
    assert "outside the v1 taxonomy" in result.basis


def test_the_top_rule_decides_the_class_and_lends_its_confidence() -> None:
    result = classify_hits([hit("npm.eresolve"), hit("ci.artifact-missing")])
    assert result.failure_class is FailureClass.TEST
    assert result.fix_type is FixType.CODE_PATCH
    assert result.confidence is get_rule("npm.eresolve").confidence  # type: ignore[union-attr]
    assert result.source == "rules"


def test_another_attempt_passing_makes_it_flaky_whatever_the_rule_said() -> None:
    """Same job, same commit, different outcome: the strongest signal there is."""
    result = classify_hits([hit("test.jest-failed")], RetryHistory(("failed", "success")))
    assert result.failure_class is FailureClass.FLAKY
    assert result.fix_type is FixType.FLAKY_RETRY
    assert result.confidence is Confidence.HIGH
    assert result.source == "retry-history"
    assert result.rule_id == "test.jest-failed", "the rule is still reported"


def test_retry_history_can_classify_a_log_nothing_recognised() -> None:
    """It comes from GitLab's records, not from reading the log."""
    result = classify_hits([], RetryHistory(("failed", "success")))
    assert result.failure_class is FailureClass.FLAKY


def test_a_transient_rule_that_failed_every_time_keeps_its_class_but_loses_confidence() -> None:
    """A timeout on every one of three attempts is probably an egress rule, not bad luck."""
    history = RetryHistory(("failed", "failed", "failed"))
    result = classify_hits([hit("net.connection-timeout")], history)
    assert result.failure_class is FailureClass.FLAKY
    assert result.confidence is Confidence.LOW
    assert "failed on every attempt" in result.basis


def test_a_transient_rule_with_no_retries_keeps_the_rules_confidence() -> None:
    result = classify_hits([hit("net.connection-timeout")], RetryHistory(("failed",)))
    assert result.confidence is get_rule("net.connection-timeout").confidence  # type: ignore[union-attr]


def test_consistent_failure_only_weakens_a_flaky_class() -> None:
    """A test that fails identically three times is a test failure - more so, not less."""
    result = classify_hits([hit("test.jest-failed")], RetryHistory(("failed", "failed", "failed")))
    assert result.failure_class is FailureClass.TEST
    assert result.confidence is get_rule("test.jest-failed").confidence  # type: ignore[union-attr]


def _report(hits: list[RuleHit], diagnosis_class: FailureClass) -> Report:
    return Report(
        job=JobRef(name="build"),
        distilled=distill("ERROR: Job failed: exit code 1\n"),
        hits=hits,
        diagnosis=Diagnosis(
            summary="s",
            root_cause="r",
            confidence=Confidence.HIGH,
            category=Category.SCRIPT,
            failure_class=diagnosis_class,
        ),
    )


def test_the_model_does_not_override_the_rules() -> None:
    """The class sets the fix type, and `yaml_patch` can lead to a write.

    A model is not reproducible, so its reading is shown beside the
    classification rather than replacing it.
    """
    report = _report([hit("npm.eresolve")], FailureClass.YAML)
    assert classify(report).failure_class is FailureClass.TEST


def test_the_model_does_not_fill_a_gap_either() -> None:
    """Even where the rules are silent: a guessed class must not reach a fix type."""
    report = _report([], FailureClass.YAML)
    result = classify(report)
    assert result.failure_class is FailureClass.UNCLASSIFIED
    assert result.fix_type is None


def test_classify_reads_the_retry_history_on_the_report() -> None:
    report = Report(
        job=JobRef(name="build"),
        distilled=distill("ERROR: Job failed: exit code 1\n"),
        hits=[hit("test.jest-failed")],
        retry=RetryHistory(("failed", "success")),
    )
    assert classify(report).failure_class is FailureClass.FLAKY


# -- the model's answer ------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("test", FailureClass.TEST),
        (" CI_VARS ", FailureClass.CI_VARS),
        ("unclassified", FailureClass.UNCLASSIFIED),
        # Excluded from the schema; if it arrives anyway it is not trusted.
        ("flaky", FailureClass.UNCLASSIFIED),
        ("network", FailureClass.UNCLASSIFIED),
        (None, FailureClass.UNCLASSIFIED),
        (42, FailureClass.UNCLASSIFIED),
    ],
)
def test_coerce_failure_class(raw: object, expected: FailureClass) -> None:
    assert coerce_failure_class(raw) is expected


def test_class_of_an_unknown_rule_is_unclassified() -> None:
    from dataclasses import replace

    rule = get_rule("npm.eresolve")
    assert rule is not None
    assert class_of(replace(rule, id="not.a-rule")) is FailureClass.UNCLASSIFIED


def test_categories_and_classes_are_different_questions() -> None:
    """Both dependency and lint land in `test`: the fix is a change to the project."""
    assert class_of(get_rule("npm.eresolve")) is FailureClass.TEST  # type: ignore[arg-type]
    assert get_rule("npm.eresolve").category is Category.DEPENDENCY  # type: ignore[union-attr]
    assert class_of(get_rule("lint.eslint")) is FailureClass.TEST  # type: ignore[arg-type]

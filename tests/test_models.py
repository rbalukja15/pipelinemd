"""The shared vocabulary: pure properties, no I/O.

models.py has no dependencies and no behaviour beyond these derived values,
so it is the one module that can be tested entirely in isolation.
"""

from __future__ import annotations

from pipelinemd.models import (
    Category,
    Confidence,
    Diagnosis,
    DistilledLog,
    DistillStats,
    EvidenceBlock,
    EvidenceLine,
    Fix,
    JobRef,
    Report,
    Rule,
    RuleHit,
    Section,
    TraceLine,
)


def _line(number: int, text: str, **kwargs: object) -> EvidenceLine:
    return EvidenceLine(number=number, text=text, **kwargs)  # type: ignore[arg-type]


# -- enums ------------------------------------------------------------------


def test_enums_are_string_valued() -> None:
    assert Category.DEPENDENCY == "dependency"
    assert Confidence.HIGH == "high"
    assert Category("runner") is Category.RUNNER


# -- sections ---------------------------------------------------------------


def test_section_that_closed_is_not_open() -> None:
    section = Section(name="step_script", start_line=1, end_line=9, duration_s=8.0)
    assert not section.failed_open


def test_section_without_an_end_is_still_open() -> None:
    """gitlab-runner never closes the section the job died in."""
    assert Section(name="step_script", start_line=1).failed_open


# -- evidence ---------------------------------------------------------------


def test_evidence_line_collapse_flag() -> None:
    assert not _line(1, "x").collapsed
    assert _line(1, "x", repeat=4).collapsed


def test_evidence_block_bounds() -> None:
    block = EvidenceBlock(label="tail", lines=(_line(10, "a"), _line(12, "b")))
    assert (block.start, block.end) == (10, 12)


def test_empty_evidence_block_has_zero_bounds() -> None:
    empty = EvidenceBlock(label="tail", lines=())
    assert (empty.start, empty.end) == (0, 0)


# -- stats ------------------------------------------------------------------


def test_reduction_fraction() -> None:
    assert DistillStats(0, 1000, 1000, 100, 0).reduction == 0.9


def test_reduction_of_an_empty_trace_is_zero_not_a_crash() -> None:
    assert DistillStats(0, 0, 0, 0, 0).reduction == 0.0


# -- distilled log ----------------------------------------------------------


def _log() -> DistilledLog:
    return DistilledLog(
        lines=[
            TraceLine(number=1, raw_number=1, text="$ npm ci"),
            TraceLine(number=2, raw_number=2, text="npm ERR! boom"),
        ],
        evidence=[
            EvidenceBlock(label="a", lines=(_line(1, "$ npm ci"),)),
            EvidenceBlock(label="b", lines=(_line(9, "npm ERR! boom"),)),
        ],
        sections=[
            Section(name="get_sources", start_line=1, end_line=2, duration_s=1.0),
            Section(name="step_script", start_line=2),
        ],
    )


def test_evidence_text_marks_the_gap_between_blocks() -> None:
    text = _log().evidence_text()
    assert "7 lines omitted" in text, "a jump in line numbers must be visible"
    assert "npm ERR! boom" in text


def test_evidence_text_shows_repeat_counts() -> None:
    log = DistilledLog(evidence=[EvidenceBlock(label="a", lines=(_line(1, "same", repeat=12),))])
    assert "[x12]" in log.evidence_text()


def test_clean_text_joins_every_line() -> None:
    assert _log().clean_text() == "$ npm ci\nnpm ERR! boom"


def test_failing_section_is_the_one_left_open() -> None:
    section = _log().failing_section
    assert section is not None and section.name == "step_script"


def test_no_failing_section_when_all_closed() -> None:
    log = DistilledLog(sections=[Section(name="a", start_line=1, end_line=2)])
    assert log.failing_section is None


def test_empty_log_renders_empty_text() -> None:
    assert DistilledLog().evidence_text() == ""
    assert DistilledLog().clean_text() == ""


# -- job reference ----------------------------------------------------------


def test_job_label_includes_stage_and_id() -> None:
    job = JobRef(name="build", stage="test", id=98765)
    assert job.label == "build (test) #98765"


def test_job_label_degrades_to_just_a_name() -> None:
    assert JobRef(name="build").label == "build"


def test_job_label_without_a_stage() -> None:
    assert JobRef(name="build", id=7).label == "build #7"


# -- report -----------------------------------------------------------------


def _rule() -> Rule:
    return Rule(
        id="npm.eresolve",
        title="conflict",
        category=Category.DEPENDENCY,
        patterns=("ERESOLVE",),
        explanation="why",
        fixes=("do this",),
    )


def test_top_hit_is_the_first_hit() -> None:
    hits = [
        RuleHit(rule=_rule(), line_number=2, line_text="a", score=90.0),
        RuleHit(rule=_rule(), line_number=3, line_text="b", score=10.0),
    ]
    report = Report(job=JobRef(name="build"), distilled=DistilledLog(), hits=hits)
    assert report.top_hit is not None and report.top_hit.score == 90.0


def test_top_hit_is_none_when_nothing_matched() -> None:
    assert Report(job=JobRef(name="build"), distilled=DistilledLog()).top_hit is None


def test_rule_defaults_are_conservative() -> None:
    rule = _rule()
    assert rule.confidence is Confidence.MEDIUM
    assert rule.excludes == () and rule.requires == () and rule.docs == ()


def test_diagnosis_defaults() -> None:
    diagnosis = Diagnosis(
        summary="s",
        root_cause="r",
        confidence=Confidence.LOW,
        category=Category.SCRIPT,
    )
    assert diagnosis.fixes == ()
    assert (diagnosis.input_tokens, diagnosis.output_tokens) == (0, 0)


def test_fix_patch_is_optional() -> None:
    assert Fix(title="t", detail="d").patch is None

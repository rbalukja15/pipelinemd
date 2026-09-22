"""Integrity of the labelled corpus.

These check the *data*, not the tool's behaviour against it: a corpus with a
typo'd class or a marker that appears in no trace yields an eval number that is
quietly wrong, which is worse than one that is missing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pipelinemd.corpus import PROVENANCE, V1_CLASSES, CorpusCase, load_corpus
from pipelinemd.errors import PipelinemdError
from pipelinemd.rules import ALL_RULES

CASES = load_corpus()
RULE_IDS = {rule.id for rule in ALL_RULES}


def test_corpus_meets_the_size_the_issue_asks_for() -> None:
    assert len(CASES) >= 60


def test_every_v1_class_is_represented() -> None:
    covered = {case.failure_class for case in CASES}
    assert covered == V1_CLASSES, f"missing: {sorted(V1_CLASSES - covered)}"


def test_ids_are_unique() -> None:
    ids = [case.id for case in CASES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_case_is_well_formed(case: CorpusCase) -> None:
    assert case.failure_class in V1_CLASSES
    assert case.provenance in PROVENANCE
    assert case.trace_path.is_file()
    assert case.id == case.trace_path.stem, "id and filename must agree"
    assert case.evidence_marker.strip()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_expected_rule_exists_in_the_catalog(case: CorpusCase) -> None:
    """A label pointing at a deleted rule would silently score as a miss."""
    if case.expected_rule is not None:
        assert case.expected_rule in RULE_IDS


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_marker_occurs_in_its_own_trace(case: CorpusCase) -> None:
    assert case.evidence_marker in case.read()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_trace_looks_like_a_real_failed_job(case: CorpusCase) -> None:
    raw = case.read()
    assert "Running with gitlab-runner" in raw
    assert "ERROR: Job failed" in raw


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_trace_carries_no_credential_shaped_text(case: CorpusCase) -> None:
    """The corpus is committed; redaction protects reports, not files on disk."""
    raw = case.read()
    for pattern in (
        r"\b(?:glpat|glrt|gldt)-[A-Za-z0-9_\-]{20,}",
        r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
        r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    ):
        assert not re.search(pattern, raw), f"{case.id} carries {pattern}"


#: A gap note has to name the absence, not just describe the trace. Truthiness
#: would pass on any prose at all, which is how the claim and the assertion
#: drifted apart in the first place.
GAP_REASON = re.compile(r"no catalog rule|coverage gap|deliberately unlabelled", re.IGNORECASE)


def test_gaps_are_declared_not_hidden() -> None:
    """Unlabelled cases are the point: they make missing coverage visible."""
    gaps = [case for case in CASES if not case.expects_rule]
    assert gaps, "a corpus with no known gaps is probably not honest"
    unexplained = [case.id for case in gaps if not GAP_REASON.search(case.notes)]
    assert not unexplained, f"gap notes must say why no rule fires: {unexplained}"


def test_provenance_is_recorded_for_every_case() -> None:
    assert all(case.provenance in PROVENANCE for case in CASES)


def test_a_missing_corpus_says_so_clearly(tmp_path: Path) -> None:
    """The corpus ships with the repo, not the wheel; the error must explain that."""
    with pytest.raises(PipelinemdError, match="not the wheel"):
        load_corpus(tmp_path)


def test_a_malformed_record_is_rejected_rather_than_skipped(tmp_path: Path) -> None:
    (tmp_path / "corpus.jsonl").write_text(
        '{"id": "x", "failure_class": "not-a-class", "provenance": "authored", '
        '"trace": "traces/x.log", "evidence_marker": "m"}\n',
        encoding="utf-8",
    )
    with pytest.raises(PipelinemdError, match="unknown failure_class"):
        load_corpus(tmp_path)


# ---------------------------------------------------------------------------
# Loader strictness
#
# The docstring promises that nothing malformed loads quietly. Each of these
# is a way a record could have been wrong and still produced a CorpusCase -
# and every one of them would have moved an eval number rather than failing.
# ---------------------------------------------------------------------------

VALID_RECORD: dict[str, object] = {
    "id": "x",
    "failure_class": "test",
    "expected_rule": "test.pytest-failed",
    "exit_code": 1,
    "evidence_marker": "m",
    "trace": "traces/x.log",
    "provenance": "authored",
}


def write_corpus(directory: Path, *records: object) -> Path:
    """A corpus of one or more records, with a real trace behind the default."""
    traces = directory / "traces"
    traces.mkdir(exist_ok=True)
    (traces / "x.log").write_text("boom\n", encoding="utf-8")
    lines = [r if isinstance(r, str) else json.dumps(r) for r in records]
    (directory / "corpus.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


def record(**overrides: object) -> dict[str, object]:
    merged = {**VALID_RECORD, **overrides}
    return {k: v for k, v in merged.items() if v is not ...}


def test_the_baseline_record_actually_loads(tmp_path: Path) -> None:
    """Otherwise every test below could pass for the wrong reason."""
    (case,) = load_corpus(write_corpus(tmp_path, record()))
    assert case.id == "x"
    assert case.expects_rule


def test_an_empty_expected_rule_is_not_a_gap_declaration(tmp_path: Path) -> None:
    """Only null declares a gap: "" would shrink the rule@1 denominator instead."""
    with pytest.raises(PipelinemdError, match="expected_rule must be a rule id or null"):
        load_corpus(write_corpus(tmp_path, record(expected_rule="")))


def test_a_null_expected_rule_still_loads_as_a_gap(tmp_path: Path) -> None:
    (case,) = load_corpus(write_corpus(tmp_path, record(expected_rule=None)))
    assert not case.expects_rule


def test_a_missing_exit_code_is_rejected_not_defaulted(tmp_path: Path) -> None:
    """0 is not a plausible default for a corpus of failed jobs."""
    with pytest.raises(PipelinemdError, match="exit_code is required"):
        load_corpus(write_corpus(tmp_path, record(exit_code=...)))


def test_a_non_integer_exit_code_raises_a_corpus_error(tmp_path: Path) -> None:
    """int("boom") raises ValueError, which the CLI does not catch."""
    with pytest.raises(PipelinemdError, match="exit_code must be an integer"):
        load_corpus(write_corpus(tmp_path, record(exit_code="boom")))


def test_a_boolean_exit_code_raises_a_corpus_error(tmp_path: Path) -> None:
    """bool is an int in Python; True would have loaded as exit code 1."""
    with pytest.raises(PipelinemdError, match="exit_code must be an integer"):
        load_corpus(write_corpus(tmp_path, record(exit_code=True)))


@pytest.mark.parametrize("line", ["42", "[]", '"a string"'])
def test_a_json_line_that_is_not_an_object_is_rejected(tmp_path: Path, line: str) -> None:
    """These reach record.get() and raise AttributeError, which crashes the CLI."""
    with pytest.raises(PipelinemdError, match="not a JSON object"):
        load_corpus(write_corpus(tmp_path, line))


def test_a_trace_path_may_not_escape_the_corpus_directory(tmp_path: Path) -> None:
    """People add cases by pull request; `..` is easy to miss in review."""
    (tmp_path.parent / "elsewhere.log").write_text("boom\n", encoding="utf-8")
    with pytest.raises(PipelinemdError, match="points outside the corpus directory"):
        load_corpus(write_corpus(tmp_path, record(trace="../elsewhere.log")))


def test_an_absolute_trace_path_is_rejected_too(tmp_path: Path) -> None:
    with pytest.raises(PipelinemdError, match="points outside the corpus directory"):
        load_corpus(write_corpus(tmp_path, record(trace="/etc/hostname")))


def test_a_missing_trace_field_says_so(tmp_path: Path) -> None:
    with pytest.raises(PipelinemdError, match="trace is required"):
        load_corpus(write_corpus(tmp_path, record(trace=...)))


def test_a_duplicate_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PipelinemdError, match="duplicate corpus id"):
        load_corpus(write_corpus(tmp_path, record(), record()))


def test_every_loader_rejection_is_a_pipelinemd_error(tmp_path: Path) -> None:
    """The CLI maps PipelinemdError to an exit code; anything else is a traceback."""
    for broken in (
        record(exit_code="boom"),
        record(exit_code=...),
        record(expected_rule=""),
        record(trace=...),
        record(provenance="invented"),
    ):
        with pytest.raises(PipelinemdError):
            load_corpus(write_corpus(tmp_path, broken))

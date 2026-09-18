"""Integrity of the labelled corpus.

These check the *data*, not the tool's behaviour against it: a corpus with a
typo'd class or a marker that appears in no trace yields an eval number that is
quietly wrong, which is worse than one that is missing.
"""

from __future__ import annotations

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
    import re

    raw = case.read()
    for pattern in (
        r"\b(?:glpat|glrt|gldt)-[A-Za-z0-9_\-]{20,}",
        r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
        r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    ):
        assert not re.search(pattern, raw), f"{case.id} carries {pattern}"


def test_gaps_are_declared_not_hidden() -> None:
    """Unlabelled cases are the point: they make missing coverage visible."""
    gaps = [case for case in CASES if not case.is_labelled]
    assert gaps, "a corpus with no known gaps is probably not honest"
    assert all(case.notes for case in gaps), "every gap must say why"


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

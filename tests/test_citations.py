"""Resolving a diagnosis's cited line numbers back against the evidence."""

from __future__ import annotations

from pipelinemd.diagnose.citations import (
    citable_lines,
    coerce_line_numbers,
    resolve_citations,
)
from pipelinemd.distill import distill
from pipelinemd.models import DistilledLog, EvidenceBlock, EvidenceLine


def _log(*lines: EvidenceLine) -> DistilledLog:
    return DistilledLog(evidence=[EvidenceBlock(label="test", lines=lines)])


def _line(number: int, text: str, repeat: int = 1) -> EvidenceLine:
    return EvidenceLine(number=number, text=text, repeat=repeat)


# -- what may be cited ------------------------------------------------------


def test_every_shown_line_is_citable() -> None:
    log = _log(_line(10, "a"), _line(11, "b"))
    assert sorted(citable_lines(log)) == [10, 11]


def test_only_the_head_of_a_collapsed_run_is_citable() -> None:
    """`100 [x5]` prints one number, so only that number was ever shown."""
    log = _log(_line(100, "npm WARN deprecated", repeat=5))
    assert sorted(citable_lines(log)) == [100]


def test_lines_outside_the_evidence_are_not_citable() -> None:
    """The excerpt is all the model saw, so nothing else can be cited."""
    log = _log(_line(10, "a"))
    assert 9 not in citable_lines(log)
    assert 11 not in citable_lines(log)


# -- resolution -------------------------------------------------------------


def test_resolved_citation_carries_the_line_text() -> None:
    log = _log(
        _line(
            42,
            "npm ERR! code ERESOLVE",
        ),
        _line(43, "next"),
    )
    citations, unresolved = resolve_citations(log, [42])
    assert unresolved == ()
    assert len(citations) == 1
    assert citations[0].line_number == 42
    assert citations[0].text == "npm ERR! code ERESOLVE"


def test_invented_line_numbers_are_reported_not_dropped() -> None:
    log = _log(_line(10, "real"))
    citations, unresolved = resolve_citations(log, [10, 41203])
    assert [c.line_number for c in citations] == [10]
    assert unresolved == (41203,)


def test_order_is_preserved_and_duplicates_dropped() -> None:
    log = _log(_line(1, "a"), _line(2, "b"), _line(3, "c"))
    citations, _ = resolve_citations(log, [3, 1, 3, 2, 1])
    assert [c.line_number for c in citations] == [3, 1, 2]


def test_an_offset_inside_a_collapsed_run_is_not_citable() -> None:
    """101-103 were never printed, so citing one is a guess, not a reading."""
    log = _log(_line(100, "same line", repeat=4))
    citations, unresolved = resolve_citations(log, [102])
    assert citations == ()
    assert unresolved == (102,)


def test_a_citation_carries_the_collapse_count() -> None:
    """A reader must be able to tell one quote stands for ten lines."""
    log = _log(_line(100, "npm WARN deprecated", repeat=10))
    citations, _ = resolve_citations(log, [100])
    assert citations[0].repeat == 10


def test_a_fuzzy_collapsed_run_cannot_manufacture_a_quote() -> None:
    """Regression: the resolver must not hand back a line's neighbour's text.

    `_collapse` folds runs that merely look alike - "Downloading package-0"
    through "package-9" share one entry - so expanding the run by range and
    reusing the head's text would report `line 4 said "package-0"` when line 4
    said "package-2". That is a fabricated quote carrying a grounding badge,
    which is the exact failure this module exists to prevent.
    """
    raw = (
        "$ npm ci\n"
        + "".join(f"Downloading package-{i}\n" for i in range(10))
        + "ERROR: Job failed: exit code 1\n"
    )
    distilled = distill(raw)
    folded = [
        line
        for block in distilled.evidence
        for line in block.lines
        if line.text.startswith("Downloading")
    ]
    assert folded and folded[0].repeat > 1, "the fixture must actually collapse"

    head = folded[0].number
    offset = head + 2
    assert distilled.lines[offset - 1].text != folded[0].text, "offset differs"

    citations, unresolved = resolve_citations(distilled, [offset])
    assert citations == (), "an unshown offset must never resolve"
    assert unresolved == (offset,)

    # The head still resolves, and its text is genuinely that line's text.
    citations, _ = resolve_citations(distilled, [head])
    assert citations[0].text == distilled.lines[head - 1].text


def test_section_is_carried_through() -> None:
    log = _log(EvidenceLine(number=5, text="boom", section="step_script"))
    citations, _ = resolve_citations(log, [5])
    assert citations[0].section == "step_script"


def test_nothing_cited_resolves_to_nothing() -> None:
    assert resolve_citations(_log(_line(1, "a")), []) == ((), ())


def test_empty_evidence_resolves_nothing() -> None:
    citations, unresolved = resolve_citations(DistilledLog(), [1, 2])
    assert citations == ()
    assert unresolved == (1, 2)


# -- defensive parsing ------------------------------------------------------


def test_coerce_reads_ints_and_numeric_strings() -> None:
    assert coerce_line_numbers([1, "2", 3]) == [1, 2, 3]


def test_coerce_discards_anything_that_is_not_a_line_number() -> None:
    assert coerce_line_numbers([1, None, "x", 2.5, True, {}, []]) == [1]


def test_coerce_rejects_non_positive_line_numbers() -> None:
    """Lines count from 1; "also cited L-5" would be noise, not a finding."""
    assert coerce_line_numbers([0, -5, "-3", 7]) == [7]


def test_coerce_of_a_non_list_is_empty() -> None:
    assert coerce_line_numbers("42") == []
    assert coerce_line_numbers(None) == []

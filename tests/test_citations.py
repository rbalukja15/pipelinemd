"""Resolving a diagnosis's cited line numbers back against the evidence."""

from __future__ import annotations

from pipelinemd.diagnose.citations import (
    citable_lines,
    coerce_line_numbers,
    resolve_citations,
)
from pipelinemd.models import DistilledLog, EvidenceBlock, EvidenceLine


def _log(*lines: EvidenceLine) -> DistilledLog:
    return DistilledLog(evidence=[EvidenceBlock(label="test", lines=lines)])


def _line(number: int, text: str, repeat: int = 1) -> EvidenceLine:
    return EvidenceLine(number=number, text=text, repeat=repeat)


# -- what may be cited ------------------------------------------------------


def test_every_shown_line_is_citable() -> None:
    log = _log(_line(10, "a"), _line(11, "b"))
    assert sorted(citable_lines(log)) == [10, 11]


def test_a_collapsed_run_makes_every_folded_line_citable() -> None:
    """`100 [x5]` stands for 100-104; each resolves to what the run said."""
    log = _log(_line(100, "npm WARN deprecated", repeat=5))
    citable = citable_lines(log)
    assert sorted(citable) == [100, 101, 102, 103, 104]
    assert citable[103].text == "npm WARN deprecated"


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


def test_citation_inside_a_collapsed_run_resolves() -> None:
    log = _log(_line(100, "same line", repeat=4))
    citations, unresolved = resolve_citations(log, [102])
    assert unresolved == ()
    assert citations[0].line_number == 102
    assert citations[0].text == "same line"


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


def test_coerce_of_a_non_list_is_empty() -> None:
    assert coerce_line_numbers("42") == []
    assert coerce_line_numbers(None) == []

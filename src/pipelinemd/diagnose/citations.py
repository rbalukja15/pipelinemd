"""Check that a diagnosis points at lines that actually exist.

A model asked to explain a failure will happily produce a confident paragraph
citing line 41203 whether or not line 41203 says anything of the sort. The
cheap defence is to resolve every cited number back against the evidence the
model was shown, and to treat a citation that does not resolve as what it is:
the model describing something it did not read.

Resolution is deliberately narrow. Only lines present in the *evidence* count,
not the whole cleaned trace - the excerpt is all the model saw, so a number
outside it could not have been read, only guessed.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Citation, DistilledLog, EvidenceLine


def citable_lines(distilled: DistilledLog) -> dict[int, EvidenceLine]:
    """Every line number a diagnosis may legitimately cite.

    Exactly the numbers printed in the excerpt, and nothing else. A collapsed
    run shown as ``100 [x10]`` contributes 100 alone: 101-109 were never put in
    front of the model, so citing one of them is a guess, not a reading.

    The stricter rule is also the safe one. A fuzzy collapse folds lines that
    merely *look* alike - ``Downloading package-0`` through ``package-9`` share
    a single entry - so mapping an offset onto the run's text would hand back
    "line 104 said ``package-0``" when line 104 said ``package-4``: a
    manufactured quote wearing a grounding badge, which is the one outcome this
    module exists to prevent.
    """
    return {line.number: line for block in distilled.evidence for line in block.lines}


def resolve_citations(
    distilled: DistilledLog, numbers: Iterable[int]
) -> tuple[tuple[Citation, ...], tuple[int, ...]]:
    """Split cited line numbers into resolved citations and invented ones.

    Order is preserved and duplicates are dropped, so the report reads in the
    order the model made its argument.
    """
    citable = citable_lines(distilled)
    resolved: list[Citation] = []
    unresolved: list[int] = []
    seen: set[int] = set()

    for number in numbers:
        if number in seen:
            continue
        seen.add(number)
        line = citable.get(number)
        if line is None:
            unresolved.append(number)
            continue
        resolved.append(
            Citation(
                line_number=number,
                text=line.text,
                section=line.section,
                repeat=max(1, line.repeat),
            )
        )
    return tuple(resolved), tuple(unresolved)


def coerce_line_numbers(raw: object) -> list[int]:
    """Read the model's ``evidence_lines`` defensively.

    Structured output constrains the type, but this layer is also reached by
    tests and by any future non-schema path, so anything unparseable is simply
    not a citation rather than an exception.

    Lines are numbered from 1, so anything at or below zero is malformed rather
    than invented - reporting it as "also cited L-5" would be noise dressed up
    as a finding.
    """
    if not isinstance(raw, list):
        return []
    numbers: list[int] = []
    for entry in raw:
        if isinstance(entry, bool):
            continue
        if isinstance(entry, int):
            candidate = entry
        elif isinstance(entry, str) and entry.strip().isdigit():
            candidate = int(entry.strip())
        else:
            continue
        if candidate >= 1:
            numbers.append(candidate)
    return numbers

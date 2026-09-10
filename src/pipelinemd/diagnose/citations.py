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

    A collapsed run displayed as one entry still stands for each line it
    folded, so ``100 [x5]`` makes 100-104 all citable and all resolve to the
    run's text - which is, by definition of the collapse, what each of them
    said.
    """
    citable: dict[int, EvidenceLine] = {}
    for block in distilled.evidence:
        for line in block.lines:
            for offset in range(max(1, line.repeat)):
                citable[line.number + offset] = line
    return citable


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
        resolved.append(Citation(line_number=number, text=line.text, section=line.section))
    return tuple(resolved), tuple(unresolved)


def coerce_line_numbers(raw: object) -> list[int]:
    """Read the model's ``evidence_lines`` defensively.

    Structured output constrains the type, but this layer is also reached by
    tests and by any future non-schema path, so anything unparseable is simply
    not a citation rather than an exception.
    """
    if not isinstance(raw, list):
        return []
    numbers: list[int] = []
    for entry in raw:
        if isinstance(entry, bool):
            continue
        if isinstance(entry, int):
            numbers.append(entry)
        elif isinstance(entry, str) and entry.strip().lstrip("-").isdigit():
            numbers.append(int(entry.strip()))
    return numbers

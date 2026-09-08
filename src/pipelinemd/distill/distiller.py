"""The public entry point of the deterministic half of pipelinemd."""

from __future__ import annotations

from ..models import DistilledLog, DistillStats
from .extract import (
    DEFAULT_MAX_LINES,
    DEFAULT_TAIL_LINES,
    DEFAULT_THRESHOLD,
    select_evidence,
)
from .trace import clean_trace


def distill(
    raw: str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    max_lines: int = DEFAULT_MAX_LINES,
    tail_lines: int = DEFAULT_TAIL_LINES,
) -> DistilledLog:
    """Reduce a raw gitlab-runner trace to the evidence that explains it.

    Pure and offline: the same trace always distils to the same result, which
    is what makes the output safe to diff in tests and cheap to cache.
    """
    cleaned = clean_trace(raw)
    evidence, _anchors = select_evidence(
        cleaned.lines,
        threshold=threshold,
        max_lines=max_lines,
        tail_lines=tail_lines,
    )

    evidence_lines = sum(len(block.lines) for block in evidence)
    evidence_chars = sum(len(line.text) for block in evidence for line in block.lines)

    return DistilledLog(
        lines=cleaned.lines,
        evidence=evidence,
        sections=cleaned.sections,
        commands=cleaned.commands,
        exit_code=cleaned.exit_code,
        failure_reason=cleaned.failure_reason,
        runner=cleaned.runner,
        image=cleaned.image,
        stats=DistillStats(
            raw_bytes=cleaned.raw_bytes,
            raw_lines=cleaned.raw_lines,
            clean_lines=len(cleaned.lines),
            evidence_lines=evidence_lines,
            evidence_chars=evidence_chars,
        ),
    )

"""Choose which evidence lines to *display* when the excerpt is still too long.

Distillation already decided what is worth keeping. This is a second, smaller
decision: given a terminal that can show 40 lines and an excerpt of 200, which
40? Taking the first 40 is the obvious answer and the wrong one - the head of
an excerpt is usually the context leading up to a failure, not the failure.

Three rules decide it: the runner's closing verdict is always shown; anchor
lines beat their neighbours; and within a tier the *earliest* line wins,
because the top of an error block names the conflict while the bottom repeats
it.
"""

from __future__ import annotations

import re

from ..models import DistilledLog, EvidenceLine

# Runner chatter that always trails a job. It is never the verdict, so it must
# not claim one of the guaranteed verdict slots.
_BOILERPLATE = re.compile(
    r"^(?:Cleaning up project directory|Uploading artifacts|Saving cache|"
    r"Not uploading cache|Restoring cache|Job succeeded|Downloading artifacts)",
)

NEAR = 1
NEARISH = 3
VERDICT_LINES = 2

TIER_VERDICT = 4
TIER_ANCHOR = 3
TIER_ADJACENT = 2
TIER_NEARBY = 1
TIER_CONTEXT = 0


def _tiers(lines: list[EvidenceLine]) -> list[int]:
    anchors = [index for index, line in enumerate(lines) if line.is_anchor]
    verdict: set[int] = set()
    for index in range(len(lines) - 1, -1, -1):
        text = lines[index].text.strip()
        if text and not _BOILERPLATE.match(text):
            verdict.add(index)
            if len(verdict) >= VERDICT_LINES:
                break

    tiers: list[int] = []
    for index, line in enumerate(lines):
        if index in verdict:
            tiers.append(TIER_VERDICT)
        elif not line.text.strip():
            # A blank line is never worth a slot that a content line could use.
            tiers.append(TIER_CONTEXT)
        elif line.is_anchor:
            tiers.append(TIER_ANCHOR)
        else:
            distance = min((abs(index - anchor) for anchor in anchors), default=10**6)
            if distance <= NEAR:
                tiers.append(TIER_ADJACENT)
            elif distance <= NEARISH:
                tiers.append(TIER_NEARBY)
            else:
                tiers.append(TIER_CONTEXT)
    return tiers


def select_display_lines(distilled: DistilledLog, limit: int) -> tuple[list[EvidenceLine], int]:
    """Return the lines to show and how many were dropped."""
    flat = [line for block in distilled.evidence for line in block.lines]
    if limit <= 0 or len(flat) <= limit:
        return flat, 0

    tiers = _tiers(flat)
    # reverse=True on (tier, -index) gives: highest tier first, and within a
    # tier the lowest index - i.e. the earliest line - first.
    ranked = sorted(range(len(flat)), key=lambda i: (tiers[i], -i), reverse=True)
    keep = sorted(ranked[:limit])
    return [flat[index] for index in keep], len(flat) - limit


def gap_before(previous: EvidenceLine | None, current: EvidenceLine) -> int:
    """Lines skipped between two displayed entries, accounting for collapsing."""
    if previous is None:
        return 0
    expected = previous.number + previous.repeat
    return max(0, current.number - expected)

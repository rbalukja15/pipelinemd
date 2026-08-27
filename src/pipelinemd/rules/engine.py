"""Match the rule catalog against a distilled trace.

Matching runs over the distilled evidence first. A rule that fires inside the
evidence is far more likely to describe the actual failure than one that fires
somewhere in the other 40,000 lines, so evidence hits are scored higher - and
the whole trace is only searched as a fallback.
"""

from __future__ import annotations

import re
from functools import lru_cache

from ..models import Confidence, DistilledLog, Rule, RuleHit
from .catalog import ALL_RULES

_CONFIDENCE_WEIGHT = {
    Confidence.HIGH: 100.0,
    Confidence.MEDIUM: 60.0,
    Confidence.LOW: 30.0,
}

EVIDENCE_BONUS = 30.0
EXIT_CODE_BONUS = 20.0
RECENCY_BONUS = 20.0
CLEANUP_PENALTY = 45.0
DEFAULT_MIN_SCORE = 1.0

# gitlab-runner sections that only run *after* the script has already decided
# the job's fate. A rule firing in one of these is describing fallout - the
# artifact upload that found nothing because the build never produced it - so
# it must not outrank a hit in the script itself.
CLEANUP_SECTIONS = frozenset(
    {
        "after_script",
        "archive_cache",
        "archive_cache_on_failure",
        "upload_artifacts_on_success",
        "upload_artifacts_on_failure",
        "cleanup_file_variables",
    }
)


@lru_cache(maxsize=2048)
def _compile(pattern: str) -> re.Pattern[str]:
    """Compile once per pattern string; the catalog is scanned repeatedly."""
    return re.compile(pattern, re.MULTILINE)


def _first_match(
    patterns: tuple[str, ...],
    lines: list[tuple[int, str]],
    excludes: tuple[str, ...],
) -> tuple[int, str] | None:
    """First line matching any pattern and no exclusion, in trace order."""
    compiled = [_compile(p) for p in patterns]
    compiled_excludes = [_compile(p) for p in excludes]
    for number, text in lines:
        if not any(pattern.search(text) for pattern in compiled):
            continue
        if any(pattern.search(text) for pattern in compiled_excludes):
            continue
        return number, text
    return None


def _requires_satisfied(rule: Rule, corpus: str) -> bool:
    return all(_compile(pattern).search(corpus) for pattern in rule.requires)


def match_rules(
    distilled: DistilledLog,
    *,
    rules: tuple[Rule, ...] = ALL_RULES,
    limit: int | None = None,
    min_score: float = DEFAULT_MIN_SCORE,
) -> list[RuleHit]:
    """Return the rules that fired, best first.

    A rule contributes at most one hit - the first line it matches. Score
    combines the rule's own confidence with where it matched (evidence beats
    the full trace, later beats earlier) and whether the job's exit code is one
    the rule expects.
    """
    evidence_lines: list[tuple[int, str]] = [
        (line.number, line.text) for block in distilled.evidence for line in block.lines
    ]
    all_lines: list[tuple[int, str]] = [(line.number, line.text) for line in distilled.lines]
    evidence_numbers = {number for number, _ in evidence_lines}
    section_of: dict[int, str | None] = {line.number: line.section for line in distilled.lines}

    evidence_corpus = "\n".join(text for _, text in evidence_lines)
    full_corpus = distilled.clean_text()
    total_lines = max(1, distilled.stats.clean_lines)

    hits: list[RuleHit] = []
    for rule in rules:
        found = _first_match(rule.patterns, evidence_lines, rule.excludes)
        in_evidence = found is not None
        corpus = evidence_corpus
        if found is None:
            found = _first_match(rule.patterns, all_lines, rule.excludes)
            corpus = full_corpus
        if found is None:
            continue
        if not _requires_satisfied(rule, corpus):
            continue

        number, text = found
        score = _CONFIDENCE_WEIGHT[rule.confidence]
        if in_evidence or number in evidence_numbers:
            score += EVIDENCE_BONUS
        if rule.exit_codes and distilled.exit_code in rule.exit_codes:
            score += EXIT_CODE_BONUS
        score += RECENCY_BONUS * (number / total_lines)
        if section_of.get(number) in CLEANUP_SECTIONS:
            score -= CLEANUP_PENALTY

        if score < min_score:
            continue
        hits.append(
            RuleHit(
                rule=rule,
                line_number=number,
                line_text=text,
                score=round(score, 2),
                in_evidence=in_evidence,
            )
        )

    hits.sort(key=lambda hit: (-hit.score, hit.rule.id))
    return hits[:limit] if limit is not None else hits

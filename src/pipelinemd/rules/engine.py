"""Match the rule catalog against a distilled trace.

Matching runs over the distilled evidence first. A rule that fires inside the
evidence is far more likely to describe the actual failure than one that fires
somewhere in the other 40,000 lines, so evidence hits are scored higher - and
the whole trace is only searched as a fallback.
"""

from __future__ import annotations

import re
from functools import lru_cache

from ..distill.trace import POST_JOB_SECTION
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
QUOTED_VERDICT_PENALTY = 25.0
NAMED_CAUSE_BONUS = 10.0
DEFAULT_MIN_SCORE = 1.0

# The runner's own verdict on the job. What follows the prefix is the runner
# restating the thing that went wrong:
#
#   ERROR: Job failed (system failure): no space left on device
#   ^--- runner.system-failure           ^--- runner.no-space
#
# runner.system-failure is a container, and its own first fix says so: "read the
# line right before this one". A rule that names the restated cause is usually
# the better answer, and it is scored as if it matched at the verdict, because
# that is where the runner concluded it. Without that, the container wins on
# recency alone: it always sits on the last line, while real runner output
# prints the cause several times earlier - a WARNING per pull attempt, an
# `ERROR: Preparation failed:` per retry - so the specific rule's *first* match
# is never the verdict at all.
#
# The exception is a rule whose advice is about the job's own setup of
# something the runner also provides. Restated in the runner's verdict, the
# thing in question is the runner's, and that advice points at the wrong
# machine:
#
#   ERROR: Job failed (system failure): Cannot connect to the Docker daemon
#                                        ^--- "add services: [docker:dind]"
#
# That is a property of the rule, not of its category: docker.manifest-unknown
# is also `docker`, and restated in the same verdict its advice - fix the tag in
# `image:` - is exactly right. Nor of the section: both fail in
# prepare_executor. So the exceptions are named, one by one.
VERDICT_LINE = re.compile(r"^ERROR: Job failed")

#: Rules whose advice targets the job's setup of something the runner provides.
#: Restated in the runner's verdict, they describe the runner's, so they are
#: demoted rather than promoted. An addition needs the same argument: name the
#: fix that is right in a job and wrong on the runner.
WRONG_MACHINE_WHEN_RESTATED: frozenset[str] = frozenset({"docker.daemon-unreachable"})

# gitlab-runner sections that only run *after* the script has already decided
# the job's fate. A rule firing in one of these is describing fallout - the
# artifact upload that found nothing because the build never produced it - so
# it must not outrank a hit in the script itself. GitHub Actions has no such
# markers; everything it runs after its verdict is read into one section of
# its own, which is fallout for the same reason.
CLEANUP_SECTIONS = frozenset(
    {
        "after_script",
        "archive_cache",
        "archive_cache_on_failure",
        "upload_artifacts_on_success",
        "upload_artifacts_on_failure",
        "cleanup_file_variables",
        POST_JOB_SECTION,
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


def _offset_in(rule: Rule, text: str) -> int | None:
    """Where in the line this rule first matches, or None if it does not.

    Every pattern is tried rather than stopping at the first hit, because a
    rule anchored at column 0 by one pattern is adjudicating even if another of
    its patterns also matches deeper in the line. Exclusions apply, as they do
    everywhere else a rule is matched.
    """
    if any(_compile(p).search(text) for p in rule.excludes):
        return None
    starts = [m.start() for m in (_compile(p).search(text) for p in rule.patterns) if m]
    return min(starts) if starts else None


def _restated_at(rule: Rule, verdicts: list[tuple[int, str]]) -> int | None:
    """The latest runner verdict that restates this rule's cause, if any.

    "Restates" means the rule matches *inside* the verdict, after the prefix. A
    rule matching at column 0 is the verdict itself - the container - and is
    not restating anything. Verdict lines are a handful per trace, so this
    stays off the hot path.
    """
    restating = [
        number
        for number, text in verdicts
        if (offset := _offset_in(rule, text)) is not None and offset > 0
    ]
    return max(restating) if restating else None


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
    verdicts = [(number, text) for number, text in all_lines if VERDICT_LINE.match(text)]

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
        # Where the runner restates this rule's cause in its verdict, score it
        # there - see the comment on VERDICT_LINE.
        position = number
        if (restated := _restated_at(rule, verdicts)) is not None:
            position = max(number, restated)
            if rule.id in WRONG_MACHINE_WHEN_RESTATED:
                score -= QUOTED_VERDICT_PENALTY
            else:
                score += NAMED_CAUSE_BONUS
        score += RECENCY_BONUS * (position / total_lines)
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

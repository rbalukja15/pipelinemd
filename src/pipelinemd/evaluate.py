"""Measure the deterministic half against the labelled corpus.

Four questions, each answered per failure class:

* **rule@1** - did the rule the corpus expects rank first?
* **class** - did the classifier put the case in the class the corpus labels?
* **evidence** - did the line a human would point at survive distillation?
* **exit code** - did the distiller read the runner's verdict correctly?

`class` is scored from the log alone. The corpus is single traces with no
retry history, so the one signal that can promote a failure to `flaky` is
absent here by construction - a flaky case whose log looks like an ordinary
test failure is expected to miss, and the report says so rather than hiding it.

Only the deterministic layers are scored. The LLM diagnosis is deliberately
out of scope: it needs a key, costs money per run, and is not reproducible, so
folding it in would turn `make eval` from a regression gate into a bill. What
is measured here runs offline and gives the same answer every time.

Alongside them, class accuracy per confidence level: the evidence that the
confidence an analysis shows (#18) means something. A level is worth showing
only if it is right more often than the level below it, and `low` - the level
that sends an analysis to human review - should be the one that is wrong.

The report separates authored cases from observed ones, because an accuracy
figure computed over traces written by the same hand that wrote the rules is a
regression signal rather than a measurement. Collapsing the two would quietly
turn the former into the latter.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .assessment import REVIEW_BELOW, assess, rank
from .corpus import CorpusCase, load_corpus
from .distill import distill
from .models import Confidence, DistilledLog, FailureClass, JobRef, Report
from .rules import match_rules
from .taxonomy import classify_hits


@dataclass(frozen=True, slots=True)
class CaseResult:
    """What the deterministic pipeline made of one labelled case."""

    case: CorpusCase
    top_rule: str | None
    rank: int | None
    evidence_hit: bool
    exit_code_read: int | None
    reduction: float
    fired_rules: tuple[str, ...]
    predicted_class: FailureClass = FailureClass.UNCLASSIFIED
    #: The confidence the analysis would show - rules only, as everywhere here.
    confidence: Confidence = Confidence.LOW

    @property
    def rule_correct(self) -> bool:
        """The expected rule ranked first. Gap cases are never correct."""
        return self.case.expects_rule and self.rank == 1

    @property
    def exit_code_correct(self) -> bool:
        return self.exit_code_read == self.case.exit_code

    @property
    def class_correct(self) -> bool:
        return self.predicted_class.value == self.case.failure_class

    @property
    def is_gap(self) -> bool:
        """No rule is expected to fire. Defined once, on the case itself."""
        return not self.case.expects_rule

    @property
    def false_positive(self) -> bool:
        """A gap case fired a rule anyway.

        The catalog growing a confident wrong answer where it previously had
        the good sense to stay silent is the most alarming thing this eval can
        find, and it moves none of the three headline numbers - gap cases are
        excluded from all of them. So it gets counted separately.
        """
        return self.is_gap and self.top_rule is not None


@dataclass(frozen=True, slots=True)
class ConfidenceScore:
    """How often the class was right among cases shown at one confidence level."""

    confidence: Confidence
    cases: int
    classes: int

    @property
    def needs_review(self) -> bool:
        return rank(self.confidence) < rank(REVIEW_BELOW)


@dataclass(frozen=True, slots=True)
class ClassScore:
    failure_class: str
    cases: int
    labelled: int
    rule_at_1: int
    classes: int
    evidence_hits: int
    exit_codes: int


def _rank_of(rule_id: str | None, fired: tuple[str, ...]) -> int | None:
    if rule_id is None:
        return None
    try:
        return fired.index(rule_id) + 1
    except ValueError:
        return None


def evaluate_case(case: CorpusCase) -> CaseResult:
    """Run the deterministic pipeline over one case. No network, no model."""
    distilled: DistilledLog = distill(case.read())
    hits = match_rules(distilled)
    fired = tuple(hit.rule.id for hit in hits)
    # Assessed as a report, so the eval scores the confidence a reader is shown
    # rather than a re-derivation of it.
    report = Report(job=JobRef(name=case.id, source="file"), distilled=distilled, hits=hits)
    return CaseResult(
        case=case,
        top_rule=fired[0] if fired else None,
        rank=_rank_of(case.expected_rule, fired),
        evidence_hit=case.evidence_marker in distilled.evidence_text(),
        exit_code_read=distilled.exit_code,
        reduction=distilled.stats.reduction,
        fired_rules=fired,
        predicted_class=classify_hits(hits).failure_class,
        confidence=assess(report).confidence,
    )


@dataclass(frozen=True, slots=True)
class EvalReport:
    results: tuple[CaseResult, ...]

    # -- populations -------------------------------------------------------

    @property
    def labelled(self) -> tuple[CaseResult, ...]:
        return tuple(r for r in self.results if not r.is_gap)

    @property
    def gaps(self) -> tuple[CaseResult, ...]:
        return tuple(r for r in self.results if r.is_gap)

    @property
    def misses(self) -> tuple[CaseResult, ...]:
        """Labelled cases where the expected rule did not rank first."""
        return tuple(r for r in self.labelled if not r.rule_correct)

    @property
    def class_misses(self) -> tuple[CaseResult, ...]:
        """Every case - gaps included - placed in a class other than its label."""
        return tuple(r for r in self.results if not r.class_correct)

    @property
    def gap_false_positives(self) -> tuple[CaseResult, ...]:
        """Gap cases that fired a rule. Scored by nothing else; see above."""
        return tuple(r for r in self.gaps if r.false_positive)

    def by_provenance(self) -> dict[str, int]:
        return dict(Counter(r.case.provenance for r in self.results))

    # -- scores ------------------------------------------------------------

    @property
    def rule_accuracy(self) -> float:
        labelled = self.labelled
        return sum(r.rule_correct for r in labelled) / len(labelled) if labelled else 0.0

    @property
    def class_accuracy(self) -> float:
        """Over every case: a gap still has a class label, even without a rule."""
        return (
            sum(r.class_correct for r in self.results) / len(self.results) if self.results else 0.0
        )

    @property
    def evidence_hit_rate(self) -> float:
        return (
            sum(r.evidence_hit for r in self.results) / len(self.results) if self.results else 0.0
        )

    @property
    def exit_code_accuracy(self) -> float:
        return (
            sum(r.exit_code_correct for r in self.results) / len(self.results)
            if self.results
            else 0.0
        )

    def confidence_scores(self) -> list[ConfidenceScore]:
        """Class accuracy per confidence level, highest first; every level listed."""
        return [
            ConfidenceScore(
                confidence=level,
                cases=sum(r.confidence is level for r in self.results),
                classes=sum(r.confidence is level and r.class_correct for r in self.results),
            )
            for level in sorted(Confidence, key=rank, reverse=True)
        ]

    def class_scores(self) -> list[ClassScore]:
        by_class: dict[str, list[CaseResult]] = {}
        for result in self.results:
            by_class.setdefault(result.case.failure_class, []).append(result)
        scores = [
            ClassScore(
                failure_class=name,
                cases=len(group),
                labelled=sum(not r.is_gap for r in group),
                rule_at_1=sum(r.rule_correct for r in group),
                classes=sum(r.class_correct for r in group),
                evidence_hits=sum(r.evidence_hit for r in group),
                exit_codes=sum(r.exit_code_correct for r in group),
            )
            for name, group in by_class.items()
        ]
        scores.sort(key=lambda score: score.failure_class)
        return scores


def run_eval(corpus_path: Path | None = None) -> EvalReport:
    return EvalReport(results=tuple(evaluate_case(c) for c in load_corpus(corpus_path)))


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _bar(hits: int, total: int) -> str:
    return f"{hits}/{total}" if total else "-"


def _class_miss_reason(result: CaseResult) -> str:
    """Why a case landed in the wrong class, in terms a reader can act on."""
    if not result.fired_rules:
        return "no rule fired"
    if result.predicted_class is FailureClass.UNCLASSIFIED:
        return f"{result.top_rule} is outside v1"
    if result.case.failure_class == FailureClass.FLAKY.value:
        # Deliberately neutral. For some of these, retry history would promote
        # the case to flaky; for others it would confirm the rule's class and
        # the label is the thing in question. The eval cannot tell which,
        # because the corpus carries no retry history at all.
        return (
            f"{result.top_rule} is not transient by nature, so only retry history could "
            "make it flaky, and the corpus carries none"
        )
    return f"{result.top_rule} maps to {result.predicted_class}"


def format_report(report: EvalReport) -> str:
    """A plain-text scorecard. No colour: this is read in CI as often as not."""
    provenance = ", ".join(
        f"{count} {name}" for name, count in sorted(report.by_provenance().items())
    )
    rule = f"{'-' * 16} {'-' * 5} {'-' * 8} {'-' * 7} {'-' * 9} {'-' * 10}"
    lines = [
        f"pipelinemd eval — {len(report.results)} cases ({provenance})",
        "",
        f"{'class':<16} {'cases':>5} {'rule@1':>8} {'class':>7} {'evidence':>9} {'exit code':>10}",
        rule,
    ]
    for score in report.class_scores():
        lines.append(
            f"{score.failure_class:<16} {score.cases:>5} "
            f"{_bar(score.rule_at_1, score.labelled):>8} "
            f"{_bar(score.classes, score.cases):>7} "
            f"{_bar(score.evidence_hits, score.cases):>9} "
            f"{_bar(score.exit_codes, score.cases):>10}"
        )
    labelled = len(report.labelled)
    total = len(report.results)
    lines += [
        rule,
        f"{'overall':<16} {total:>5} "
        f"{_bar(sum(r.rule_correct for r in report.results), labelled):>8} "
        f"{_bar(sum(r.class_correct for r in report.results), total):>7} "
        f"{_bar(sum(r.evidence_hit for r in report.results), total):>9} "
        f"{_bar(sum(r.exit_code_correct for r in report.results), total):>10}",
        "",
        f"rule@1 {report.rule_accuracy:.1%}  ·  "
        f"class {report.class_accuracy:.1%}  ·  "
        f"evidence {report.evidence_hit_rate:.1%}  ·  "
        f"exit code {report.exit_code_accuracy:.1%}",
    ]

    lines += ["", "Class accuracy by confidence"]
    for level in report.confidence_scores():
        rate = f"{level.classes / level.cases:.0%}" if level.cases else "-"
        note = "  needs human review" if level.needs_review else ""
        lines.append(
            f"  {level.confidence.value:<8} {_bar(level.classes, level.cases):>7} {rate:>5}{note}"
        )

    # Not part of the three rates - gap cases are excluded from all of them -
    # so it would otherwise only appear as a line buried in the gaps section.
    if report.gap_false_positives:
        lines.append(
            f"{len(report.gap_false_positives)} false positive(s) on known gaps "
            "— a rule now fires where the catalog used to stay silent."
        )

    # Widest id in whichever rows we are about to print, so a long id pushes
    # the column out instead of overflowing it and skewing its neighbours.
    listed = report.misses + report.gaps + report.class_misses
    width = max((len(r.case.id) for r in listed), default=0)

    if report.misses:
        lines += ["", f"Misses ({len(report.misses)})"]
        for miss in report.misses:
            got = miss.top_rule or "nothing fired"
            rank = f"rank {miss.rank}" if miss.rank else "never fired"
            lines.append(
                f"  {miss.case.id:<{width}} expected {miss.case.expected_rule} — got {got} ({rank})"
            )

    if report.gaps:
        lines += ["", f"Known gaps — no rule covers these ({len(report.gaps)})"]
        for gap in report.gaps:
            fired = gap.top_rule or "nothing fired"
            note = f"FALSE POSITIVE: {fired}" if gap.false_positive else "correctly silent"
            lines.append(f"  {gap.case.id:<{width}} {note}")

    if report.class_misses:
        lines += ["", f"Class misses ({len(report.class_misses)})"]
        for miss in report.class_misses:
            lines.append(
                f"  {miss.case.id:<{width}} labelled {miss.case.failure_class} — "
                f"got {miss.predicted_class} ({_class_miss_reason(miss)})"
            )

    if all(r.case.provenance == "authored" for r in report.results):
        lines += [
            "",
            "Every case is authored, not observed. These numbers are a regression",
            "signal, not a measurement of real-world accuracy — see corpus/README.md.",
        ]
    return "\n".join(lines) + "\n"


def eval_report_to_dict(report: EvalReport) -> dict[str, object]:
    """Machine-readable results, for tracking the numbers over time."""
    return {
        "schema_version": 1,
        "cases": len(report.results),
        "provenance": report.by_provenance(),
        "overall": {
            "rule_at_1": round(report.rule_accuracy, 4),
            "class_accuracy": round(report.class_accuracy, 4),
            "evidence_hit_rate": round(report.evidence_hit_rate, 4),
            "exit_code_accuracy": round(report.exit_code_accuracy, 4),
            "labelled": len(report.labelled),
            "gaps": len(report.gaps),
            "gap_false_positives": len(report.gap_false_positives),
        },
        "by_confidence": [
            {
                "confidence": level.confidence.value,
                "cases": level.cases,
                "classes": level.classes,
                "needs_review": level.needs_review,
            }
            for level in report.confidence_scores()
        ],
        "by_class": [
            {
                "failure_class": score.failure_class,
                "cases": score.cases,
                "labelled": score.labelled,
                "rule_at_1": score.rule_at_1,
                "classes": score.classes,
                "evidence_hits": score.evidence_hits,
                "exit_codes": score.exit_codes,
            }
            for score in report.class_scores()
        ],
        "misses": [
            {
                "id": miss.case.id,
                "failure_class": miss.case.failure_class,
                "expected_rule": miss.case.expected_rule,
                "top_rule": miss.top_rule,
                "rank": miss.rank,
            }
            for miss in report.misses
        ],
        "class_misses": [
            {
                "id": miss.case.id,
                "labelled": miss.case.failure_class,
                "predicted": miss.predicted_class.value,
                "top_rule": miss.top_rule,
                "reason": _class_miss_reason(miss),
            }
            for miss in report.class_misses
        ],
        "gaps": [
            {
                "id": gap.case.id,
                "failure_class": gap.case.failure_class,
                "top_rule": gap.top_rule,
                "false_positive": gap.false_positive,
            }
            for gap in report.gaps
        ],
    }

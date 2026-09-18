"""Measure the deterministic half against the labelled corpus.

Three questions, each answered per failure class:

* **rule@1** - did the rule the corpus expects rank first?
* **evidence** - did the line a human would point at survive distillation?
* **exit code** - did the distiller read the runner's verdict correctly?

Only the deterministic layers are scored. The LLM diagnosis is deliberately
out of scope: it needs a key, costs money per run, and is not reproducible, so
folding it in would turn `make eval` from a regression gate into a bill. What
is measured here runs offline and gives the same answer every time.

The report separates authored cases from observed ones, because an accuracy
figure computed over traces written by the same hand that wrote the rules is a
regression signal rather than a measurement. Collapsing the two would quietly
turn the former into the latter.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .corpus import CorpusCase, load_corpus
from .distill import distill
from .models import DistilledLog
from .rules import match_rules


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

    @property
    def rule_correct(self) -> bool:
        """The expected rule ranked first. Unlabelled cases are never correct."""
        return self.case.expected_rule is not None and self.rank == 1

    @property
    def exit_code_correct(self) -> bool:
        return self.exit_code_read == self.case.exit_code

    @property
    def is_gap(self) -> bool:
        return self.case.expected_rule is None


@dataclass(frozen=True, slots=True)
class ClassScore:
    failure_class: str
    cases: int
    labelled: int
    rule_at_1: int
    evidence_hits: int
    exit_codes: int

    def rate(self, hits: int, total: int) -> float:
        return hits / total if total else 0.0


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
    fired = tuple(hit.rule.id for hit in match_rules(distilled))
    return CaseResult(
        case=case,
        top_rule=fired[0] if fired else None,
        rank=_rank_of(case.expected_rule, fired),
        evidence_hit=case.evidence_marker in distilled.evidence_text(),
        exit_code_read=distilled.exit_code,
        reduction=distilled.stats.reduction,
        fired_rules=fired,
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

    def by_provenance(self) -> dict[str, int]:
        return dict(Counter(r.case.provenance for r in self.results))

    # -- scores ------------------------------------------------------------

    @property
    def rule_accuracy(self) -> float:
        labelled = self.labelled
        return sum(r.rule_correct for r in labelled) / len(labelled) if labelled else 0.0

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


def format_report(report: EvalReport) -> str:
    """A plain-text scorecard. No colour: this is read in CI as often as not."""
    provenance = ", ".join(
        f"{count} {name}" for name, count in sorted(report.by_provenance().items())
    )
    lines = [
        f"pipelinemd eval — {len(report.results)} cases ({provenance})",
        "",
        f"{'class':<16} {'cases':>5} {'rule@1':>8} {'evidence':>9} {'exit code':>10}",
        f"{'-' * 16} {'-' * 5} {'-' * 8} {'-' * 9} {'-' * 10}",
    ]
    for score in report.class_scores():
        lines.append(
            f"{score.failure_class:<16} {score.cases:>5} "
            f"{_bar(score.rule_at_1, score.labelled):>8} "
            f"{_bar(score.evidence_hits, score.cases):>9} "
            f"{_bar(score.exit_codes, score.cases):>10}"
        )
    labelled = len(report.labelled)
    total = len(report.results)
    lines += [
        f"{'-' * 16} {'-' * 5} {'-' * 8} {'-' * 9} {'-' * 10}",
        f"{'overall':<16} {total:>5} "
        f"{_bar(sum(r.rule_correct for r in report.results), labelled):>8} "
        f"{_bar(sum(r.evidence_hit for r in report.results), total):>9} "
        f"{_bar(sum(r.exit_code_correct for r in report.results), total):>10}",
        "",
        f"rule@1 {report.rule_accuracy:.1%}  ·  "
        f"evidence {report.evidence_hit_rate:.1%}  ·  "
        f"exit code {report.exit_code_accuracy:.1%}",
    ]

    if report.misses:
        lines += ["", f"Misses ({len(report.misses)})"]
        for miss in report.misses:
            got = miss.top_rule or "nothing fired"
            rank = f"rank {miss.rank}" if miss.rank else "never fired"
            lines.append(
                f"  {miss.case.id:<34} expected {miss.case.expected_rule} — got {got} ({rank})"
            )

    if report.gaps:
        lines += ["", f"Known gaps — no rule covers these ({len(report.gaps)})"]
        for gap in report.gaps:
            fired = gap.top_rule or "nothing fired"
            note = "correctly silent" if gap.top_rule is None else f"FALSE POSITIVE: {fired}"
            lines.append(f"  {gap.case.id:<34} {note}")

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
            "evidence_hit_rate": round(report.evidence_hit_rate, 4),
            "exit_code_accuracy": round(report.exit_code_accuracy, 4),
            "labelled": len(report.labelled),
            "gaps": len(report.gaps),
        },
        "by_class": [
            {
                "failure_class": score.failure_class,
                "cases": score.cases,
                "labelled": score.labelled,
                "rule_at_1": score.rule_at_1,
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
        "gaps": [
            {
                "id": gap.case.id,
                "failure_class": gap.case.failure_class,
                "top_rule": gap.top_rule,
                "false_positive": gap.top_rule is not None,
            }
            for gap in report.gaps
        ],
    }

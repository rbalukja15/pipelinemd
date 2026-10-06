"""Score the Claude diagnosis against the labelled corpus. Opt-in, and billed.

`make eval` leaves the model out on purpose (see `evaluate`): it has to stay
offline, free and reproducible to be a gate. This is the other half, run by
hand when the question is "does the diagnosis earn its cost, and where":

* **model class** - did the model's `failure_class` match the label?
* **grounded** - did every line it cited exist in the excerpt it was shown?
  A diagnosis citing nothing real is rejected by the diagnosis layer, and
  counts here as an answer that was not grounded.
* **agrees** - where a rule fired, did the model put the case in the same
  class as the top rule?

Each is reported per class beside the rules' own class score from the same
run, so the comparison is like for like. Nothing here feeds the gate.

Spend is capped. The run stops before a call that, at the most any call has
cost so far, would take the total over the cap, so it can overrun only by a
call dearer than every one before it. The model must have a price on file;
an unpriced model would make the cap meaningless, so it is refused up front.

The model cannot answer `flaky`: constrained decoding leaves it out, because
whether a failure is flaky is decided by retry history, not by reading one
log. The rules can still land there, for failures that are transient by
nature, so the flaky row is one the model cannot win, and it is reported as
it is rather than dropped.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .corpus import CorpusCase
from .cost import PRICES_AS_OF, Cost, call_cost, format_usd
from .distill import distill
from .errors import DiagnosisError
from .evaluate import CaseResult, evaluate_case
from .models import Diagnosis, DistilledLog, FailureClass, JobRef, RuleHit, Usage
from .rules import match_rules

#: What the eval needs from the diagnosis layer: one case in, one answer out.
#: The CLI binds the model, effort and key; tests pass a stub.
DiagnoseFn = Callable[[JobRef, DistilledLog, list[RuleHit]], Diagnosis]

#: Request failures in a row - no answer, nothing billed - before the run gives
#: up. A wrong key or an outage fails every case the same way, and there is no
#: point spending the next half hour finding that out 62 times.
MAX_CONSECUTIVE_FAILURES = 3


@dataclass(frozen=True, slots=True)
class LlmCaseResult:
    """The rules' result and the model's answer for one case."""

    rules: CaseResult
    diagnosis: Diagnosis | None = None
    #: Why there is no diagnosis: rejected, refused, or the request failed.
    error: str | None = None
    #: Every billed call, including one whose answer was rejected.
    calls: tuple[Usage, ...] = ()
    #: Not attempted, because the cost cap or repeated failures ended the run.
    skipped: bool = False

    @property
    def case(self) -> CorpusCase:
        return self.rules.case

    @property
    def answered(self) -> bool:
        """The model returned something, usable or not, and it was billed."""
        return bool(self.calls)

    @property
    def model_class(self) -> FailureClass | None:
        return self.diagnosis.failure_class if self.diagnosis is not None else None

    @property
    def model_correct(self) -> bool:
        model_class = self.model_class
        return model_class is not None and model_class.value == self.case.failure_class

    @property
    def grounded(self) -> bool:
        return self.diagnosis is not None and self.diagnosis.fully_grounded

    @property
    def comparable(self) -> bool:
        """A rule fired and the model answered, so the two can be compared."""
        return self.rules.top_rule is not None and self.diagnosis is not None

    @property
    def agrees_with_rule(self) -> bool:
        return self.comparable and self.model_class is self.rules.predicted_class


@dataclass(frozen=True, slots=True)
class LlmClassScore:
    failure_class: str
    cases: int
    answered: int
    rules_correct: int
    model_correct: int
    grounded: int
    comparable: int
    agrees: int


@dataclass(frozen=True, slots=True)
class LlmEvalReport:
    results: tuple[LlmCaseResult, ...]
    model: str
    effort: str
    max_usd: float
    date: str = field(default_factory=lambda: datetime.now(UTC).date().isoformat())
    #: Why the run ended early, if it did.
    stopped: str | None = None

    @property
    def answered(self) -> tuple[LlmCaseResult, ...]:
        """The cases every rate is computed over: the model gave an answer."""
        return tuple(r for r in self.results if r.answered)

    @property
    def not_run(self) -> tuple[LlmCaseResult, ...]:
        return tuple(r for r in self.results if not r.answered)

    @property
    def rejected(self) -> tuple[LlmCaseResult, ...]:
        return tuple(r for r in self.answered if r.diagnosis is None)

    @property
    def rescued(self) -> tuple[LlmCaseResult, ...]:
        """The rules had the class wrong and the model had it right."""
        return tuple(r for r in self.answered if r.model_correct and not r.rules.class_correct)

    @property
    def broken(self) -> tuple[LlmCaseResult, ...]:
        """The rules had the class right and the model had it wrong."""
        return tuple(r for r in self.answered if r.rules.class_correct and not r.model_correct)

    @property
    def disagreements(self) -> tuple[LlmCaseResult, ...]:
        return tuple(r for r in self.answered if r.comparable and not r.agrees_with_rule)

    @property
    def cost(self) -> Cost:
        return Cost(
            calls=tuple(call for r in self.results for call in r.calls),
            discarded=len(self.rejected),
        )

    def _rate(self, count: int, total: int) -> float:
        return count / total if total else 0.0

    @property
    def model_accuracy(self) -> float:
        answered = self.answered
        return self._rate(sum(r.model_correct for r in answered), len(answered))

    @property
    def rules_accuracy(self) -> float:
        """The rules' class accuracy over the same cases the model answered."""
        answered = self.answered
        return self._rate(sum(r.rules.class_correct for r in answered), len(answered))

    @property
    def grounded_rate(self) -> float:
        answered = self.answered
        return self._rate(sum(r.grounded for r in answered), len(answered))

    @property
    def agreement_rate(self) -> float:
        comparable = [r for r in self.answered if r.comparable]
        return self._rate(sum(r.agrees_with_rule for r in comparable), len(comparable))

    def class_scores(self) -> list[LlmClassScore]:
        by_class: dict[str, list[LlmCaseResult]] = {}
        for result in self.results:
            by_class.setdefault(result.case.failure_class, []).append(result)
        scores = []
        for name, group in sorted(by_class.items()):
            answered = [r for r in group if r.answered]
            scores.append(
                LlmClassScore(
                    failure_class=name,
                    cases=len(group),
                    answered=len(answered),
                    rules_correct=sum(r.rules.class_correct for r in answered),
                    model_correct=sum(r.model_correct for r in answered),
                    grounded=sum(r.grounded for r in answered),
                    comparable=sum(r.comparable for r in answered),
                    agrees=sum(r.agrees_with_rule for r in answered),
                )
            )
        return scores


def _spent(calls: Iterable[Usage]) -> float:
    # The CLI refuses an unpriced model before the run, so None cannot occur
    # for a real run; a stub's made-up model name counts as free.
    return sum((call_cost(call) or 0.0) for call in calls)


def interleave(cases: Iterable[CorpusCase]) -> list[CorpusCase]:
    """One case from each class in turn, in corpus order within a class.

    The corpus is grouped by class, so a run the cap ends early would
    otherwise have scored the first classes in full and the last not at all.
    Taking them in turn means a capped run still says something about every
    class.
    """
    by_class: dict[str, list[CorpusCase]] = {}
    for case in cases:
        by_class.setdefault(case.failure_class, []).append(case)
    queues = list(by_class.values())
    ordered: list[CorpusCase] = []
    for index in range(max((len(q) for q in queues), default=0)):
        ordered.extend(q[index] for q in queues if index < len(q))
    return ordered


def _usd(amount: float) -> str:
    return f"${amount:.2f}"


def run_llm_eval(
    cases: Iterable[CorpusCase],
    diagnose: DiagnoseFn,
    *,
    model: str,
    effort: str,
    max_usd: float,
    progress: Callable[[str], object] | None = None,
) -> LlmEvalReport:
    """Diagnose every case, a class at a time, stopping before the cap is crossed."""
    say: Callable[[str], object] = progress or (lambda _line: None)
    cases = interleave(cases)
    results: list[LlmCaseResult] = []
    spent = 0.0
    dearest = 0.0
    failures = 0
    stopped: str | None = None

    for index, case in enumerate(cases, start=1):
        rules = evaluate_case(case)
        if stopped is not None:
            results.append(LlmCaseResult(rules=rules, skipped=True))
            continue
        if spent + dearest > max_usd:
            stopped = (
                f"stopped before {case.id}: {format_usd(spent)} spent, and another call "
                f"could cost up to {format_usd(dearest)}, over the {_usd(max_usd)} cap"
            )
            say(stopped)
            results.append(LlmCaseResult(rules=rules, skipped=True))
            continue

        distilled = distill(case.read())
        hits = match_rules(distilled)
        try:
            diagnosis = diagnose(JobRef(name=case.id, source="file"), distilled, hits)
        except DiagnosisError as exc:
            calls = (exc.usage,) if exc.usage is not None else ()
            result = LlmCaseResult(rules=rules, error=str(exc), calls=calls)
        else:
            result = LlmCaseResult(rules=rules, diagnosis=diagnosis, calls=(diagnosis.usage,))
        results.append(result)

        cost = _spent(result.calls)
        spent += cost
        dearest = max(dearest, cost)
        failures = 0 if result.answered else failures + 1
        say(_progress_line(index, len(cases), result, spent))

        if failures >= MAX_CONSECUTIVE_FAILURES:
            stopped = f"stopped after {failures} requests in a row failed: {result.error}"
            say(stopped)

    return LlmEvalReport(
        results=tuple(results), model=model, effort=effort, max_usd=max_usd, stopped=stopped
    )


def _progress_line(index: int, total: int, result: LlmCaseResult, spent: float) -> str:
    if result.diagnosis is not None:
        model_class = result.model_class
        verdict = f"{model_class.value if model_class else '?'}"
        verdict += " ✓" if result.model_correct else f" ✗ (labelled {result.case.failure_class})"
    elif result.answered:
        verdict = "rejected"
    else:
        verdict = "request failed"
    return f"[{index}/{total}] {result.case.id}: {verdict} · {format_usd(spent)} so far"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _frac(hits: int, total: int) -> str:
    return f"{hits}/{total}" if total else "-"


def format_llm_report(report: LlmEvalReport) -> str:
    """A plain-text scorecard, in the same shape as `make eval`'s."""
    answered = report.answered
    rule = f"{'-' * 16} {'-' * 5} {'-' * 7} {'-' * 7} {'-' * 9} {'-' * 7}"
    lines = [
        f"pipelinemd eval --llm — {report.model} · effort {report.effort} · {report.date}",
        f"{len(answered)} of {len(report.results)} cases answered",
        "",
        f"{'class':<16} {'cases':>5} {'rules':>7} {'model':>7} {'grounded':>9} {'agrees':>7}",
        rule,
    ]
    for score in report.class_scores():
        lines.append(
            f"{score.failure_class:<16} {score.cases:>5} "
            f"{_frac(score.rules_correct, score.answered):>7} "
            f"{_frac(score.model_correct, score.answered):>7} "
            f"{_frac(score.grounded, score.answered):>9} "
            f"{_frac(score.agrees, score.comparable):>7}"
        )
    comparable = [r for r in answered if r.comparable]
    lines += [
        rule,
        f"{'overall':<16} {len(report.results):>5} "
        f"{_frac(sum(r.rules.class_correct for r in answered), len(answered)):>7} "
        f"{_frac(sum(r.model_correct for r in answered), len(answered)):>7} "
        f"{_frac(sum(r.grounded for r in answered), len(answered)):>9} "
        f"{_frac(sum(r.agrees_with_rule for r in comparable), len(comparable)):>7}",
        "",
        f"class: rules {report.rules_accuracy:.1%}  ·  model {report.model_accuracy:.1%}  ·  "
        f"grounded {report.grounded_rate:.1%}  ·  agrees with the top rule "
        f"{report.agreement_rate:.1%}",
        f"model right where the rules were wrong: {len(report.rescued)}  ·  "
        f"model wrong where the rules were right: {len(report.broken)}",
    ]

    cost = report.cost
    lines += [
        "",
        f"cost {format_usd(cost.usd)} for {len(cost.calls)} call(s) "
        f"(cap {_usd(report.max_usd)}) · {cost.input_tokens:,} in / "
        f"{cost.output_tokens:,} out tokens · prices as of {PRICES_AS_OF}",
    ]
    if report.stopped:
        lines.append(report.stopped)

    listed = report.disagreements + report.rescued + report.broken + report.rejected
    width = max((len(r.case.id) for r in listed), default=0)

    def row(result: LlmCaseResult) -> str:
        model_class = result.model_class
        return (
            f"  {result.case.id:<{width}} labelled {result.case.failure_class} — "
            f"rules {result.rules.predicted_class.value}, "
            f"model {model_class.value if model_class else '-'}"
        )

    for title, group in (
        ("Model right, rules wrong", report.rescued),
        ("Model wrong, rules right", report.broken),
        ("Model and top rule disagree", report.disagreements),
    ):
        if group:
            lines += ["", f"{title} ({len(group)})", *(row(r) for r in group)]

    if report.rejected:
        lines += ["", f"Rejected answers ({len(report.rejected)})"]
        lines += [f"  {r.case.id:<{width}} {r.error}" for r in report.rejected]

    failed = [r for r in report.not_run if not r.skipped]
    if failed:
        lines += ["", f"Requests that failed ({len(failed)})"]
        lines += [f"  {r.case.id} {r.error}" for r in failed]
    skipped = [r for r in report.not_run if r.skipped]
    if skipped:
        lines += ["", f"Not run: {len(skipped)} case(s), see above for why"]

    lines += [
        "",
        "The model cannot answer flaky, so the flaky row is the rules' alone to win.",
        "Every case is authored, not observed; see corpus/README.md.",
    ]
    return "\n".join(lines) + "\n"


def llm_report_to_dict(report: LlmEvalReport) -> dict[str, object]:
    """Machine-readable results, with the model and date they came from."""
    cost = report.cost
    return {
        "schema_version": 1,
        "model": report.model,
        "effort": report.effort,
        "date": report.date,
        "cases": len(report.results),
        "answered": len(report.answered),
        "stopped": report.stopped,
        "overall": {
            "rules_class_accuracy": round(report.rules_accuracy, 4),
            "model_class_accuracy": round(report.model_accuracy, 4),
            "grounded_rate": round(report.grounded_rate, 4),
            "agreement_rate": round(report.agreement_rate, 4),
            "rescued": len(report.rescued),
            "broken": len(report.broken),
            "rejected": len(report.rejected),
        },
        "cost": {
            "usd": cost.usd,
            "max_usd": report.max_usd,
            "calls": len(cost.calls),
            "input_tokens": cost.input_tokens,
            "output_tokens": cost.output_tokens,
            "prices_as_of": PRICES_AS_OF,
        },
        "by_class": [
            {
                "failure_class": s.failure_class,
                "cases": s.cases,
                "answered": s.answered,
                "rules_correct": s.rules_correct,
                "model_correct": s.model_correct,
                "grounded": s.grounded,
                "comparable": s.comparable,
                "agrees": s.agrees,
            }
            for s in report.class_scores()
        ],
        "cases_detail": [
            {
                "id": r.case.id,
                "labelled": r.case.failure_class,
                "rules": r.rules.predicted_class.value,
                "top_rule": r.rules.top_rule,
                "model": r.model_class.value if r.model_class else None,
                "grounded": r.grounded,
                "unresolved_citations": (
                    list(r.diagnosis.unresolved_citations) if r.diagnosis else []
                ),
                "usd": call_cost(r.calls[0]) if r.calls else 0.0,
                "error": r.error,
                "skipped": r.skipped,
            }
            for r in report.results
        ],
    }

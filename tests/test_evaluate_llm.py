"""`eval --llm`, driven through a stub client: no key, no network, no bill."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from typing import Any

import pytest

from pipelinemd.cli import EXIT_USAGE, main
from pipelinemd.corpus import CorpusCase, load_corpus
from pipelinemd.cost import call_cost
from pipelinemd.diagnose import citable_lines, diagnose
from pipelinemd.errors import DiagnosisError
from pipelinemd.evaluate_llm import (
    MAX_CONSECUTIVE_FAILURES,
    format_llm_report,
    llm_report_to_dict,
    run_llm_eval,
)
from pipelinemd.models import Diagnosis, DistilledLog, FailureClass, JobRef, RuleHit, Usage

MODEL = "claude-opus-5"
CALL = Usage(model=MODEL, input_tokens=1234, output_tokens=567)
CALL_USD = call_cost(CALL)
assert CALL_USD is not None


class _Usage:
    input_tokens = CALL.input_tokens
    output_tokens = CALL.output_tokens


class _Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    stop_reason = "end_turn"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.content = [_Block(json.dumps(payload))]
        self.usage = _Usage()


class _Client:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.messages = self
        self.payload = payload

    def create(self, **_kwargs: Any) -> _Response:
        return _Response(self.payload)


def _stub(
    answer: Callable[[str], str], *, invent: bool = False
) -> Callable[[JobRef, DistilledLog, list[RuleHit]], Diagnosis]:
    """A diagnose function that answers `answer(case id)` and cites a real line.

    It goes through the real `diagnose`, so citation checking and usage
    accounting are the production code's, not the stub's.
    """

    def run(job: JobRef, distilled: DistilledLog, hits: list[RuleHit]) -> Diagnosis:
        line = 10**9 if invent else min(citable_lines(distilled))
        payload = {
            "summary": "s",
            "root_cause": "r",
            "confidence": "high",
            "category": "script",
            "failure_class": answer(job.name),
            "evidence_lines": [line],
            "fixes": [],
        }
        return diagnose(job, distilled, hits, model=MODEL, client=_Client(payload))

    return run


@pytest.fixture(scope="module")
def corpus() -> list[CorpusCase]:
    return load_corpus()


@pytest.fixture(scope="module")
def labels(corpus: list[CorpusCase]) -> dict[str, str]:
    return {case.id: case.failure_class for case in corpus}


def _run(cases: list[CorpusCase], fn: Any, max_usd: float = 100.0) -> Any:
    return run_llm_eval(cases, fn, model=MODEL, effort="high", max_usd=max_usd)


def _oracle(labels: dict[str, str]) -> Callable[[str], str]:
    # Right every time it is allowed to be: the model cannot answer flaky.
    return lambda case_id: "unclassified" if labels[case_id] == "flaky" else labels[case_id]


def test_a_model_that_knows_every_label_scores_all_but_flaky(
    corpus: list[CorpusCase], labels: dict[str, str]
) -> None:
    report = _run(corpus, _stub(_oracle(labels)))

    flaky = sum(label == "flaky" for label in labels.values())
    assert len(report.answered) == len(corpus)
    assert sum(r.model_correct for r in report.answered) == len(corpus) - flaky
    assert report.grounded_rate == 1.0
    # The rules can call a transient network error flaky; the model cannot
    # answer flaky at all. Those are the only cases the oracle loses.
    assert report.broken
    assert {r.case.failure_class for r in report.broken} == {"flaky"}
    assert all(not r.rules.class_correct for r in report.rescued)
    assert report.cost.usd == pytest.approx(CALL_USD * len(corpus))


def test_rules_are_scored_over_the_cases_the_model_answered(corpus: list[CorpusCase]) -> None:
    report = _run(corpus[:5], _stub(lambda _id: "runner"), max_usd=CALL_USD * 2.5)

    answered = report.answered
    assert len(answered) == 2
    assert report.rules_accuracy == sum(r.rules.class_correct for r in answered) / 2


def test_cases_are_taken_a_class_at_a_time(corpus: list[CorpusCase]) -> None:
    report = _run(corpus, _stub(lambda _id: "runner"))

    classes = {case.failure_class for case in corpus}
    first_round = [r.case.failure_class for r in report.results[: len(classes)]]
    assert sorted(first_round) == sorted(classes)
    assert sorted(r.case.id for r in report.results) == sorted(c.id for c in corpus)


def test_the_cap_stops_the_run_before_it_would_be_crossed(corpus: list[CorpusCase]) -> None:
    # Two calls fit; a third, at the price of the dearest so far, would not.
    report = _run(corpus[:6], _stub(lambda _id: "runner"), max_usd=CALL_USD * 2.5)

    assert len(report.answered) == 2
    assert [r.skipped for r in report.results] == [False, False, True, True, True, True]
    assert report.stopped is not None and "cap" in report.stopped
    assert report.cost.usd is not None and report.cost.usd <= CALL_USD * 2.5


def test_a_diagnosis_citing_nothing_real_is_answered_billed_and_ungrounded(
    corpus: list[CorpusCase],
) -> None:
    report = _run(corpus[:3], _stub(lambda _id: "runner", invent=True))

    assert len(report.answered) == 3
    assert len(report.rejected) == 3
    assert report.grounded_rate == 0.0
    assert report.model_accuracy == 0.0
    assert report.cost.usd == pytest.approx(CALL_USD * 3)
    assert "Rejected answers (3)" in format_llm_report(report)


def test_repeated_request_failures_end_the_run(corpus: list[CorpusCase]) -> None:
    def failing(job: JobRef, distilled: DistilledLog, hits: list[RuleHit]) -> Diagnosis:
        raise DiagnosisError("Claude request failed: 401 invalid x-api-key")

    report = _run(corpus[:6], failing)

    tried = [r for r in report.results if not r.skipped]
    assert len(tried) == MAX_CONSECUTIVE_FAILURES
    assert not report.answered
    assert report.stopped is not None and "401" in report.stopped
    assert report.cost.usd == 0


def test_agreement_is_only_counted_where_a_rule_fired(corpus: list[CorpusCase]) -> None:
    report = _run(corpus, _stub(lambda _id: "runner"))

    comparable = [r for r in report.answered if r.comparable]
    assert all(r.rules.top_rule is not None for r in comparable)
    assert {r.agrees_with_rule for r in comparable} == {True, False}
    for result in comparable:
        assert result.agrees_with_rule == (result.rules.predicted_class is FailureClass.RUNNER)
    assert len(report.disagreements) == sum(not r.agrees_with_rule for r in comparable)


def test_the_scorecard_names_the_model_date_and_cost(
    corpus: list[CorpusCase], labels: dict[str, str]
) -> None:
    report = _run(corpus[:4], _stub(_oracle(labels)))
    text = format_llm_report(report)

    assert text.startswith(f"pipelinemd eval --llm — {MODEL} · effort high · {report.date}")
    assert "rules" in text and "model" in text and "grounded" in text and "agrees" in text
    assert "(cap $100.00)" in text

    data = llm_report_to_dict(report)
    assert data["model"] == MODEL
    assert data["date"] == report.date
    assert data["answered"] == 4
    assert json.dumps(data)  # serialisable as is


# -- CLI --------------------------------------------------------------------


def _cli(*argv: str) -> tuple[int, str]:
    err = io.StringIO()
    code = main(["eval", *argv], stdout=io.StringIO(), stderr=err)
    return code, err.getvalue()


def test_the_gate_flags_are_refused_with_llm() -> None:
    code, err = _cli("--llm", "--min-rule-accuracy", "0.9")
    assert code == EXIT_USAGE
    assert "do not apply with --llm" in err


def test_an_unpriced_model_is_refused_before_any_call() -> None:
    code, err = _cli("--llm", "--model", "claude-unknown-9")
    assert code == EXIT_USAGE
    assert "cost cap cannot be enforced" in err


@pytest.mark.parametrize("cap", ["0", "-1"])
def test_the_cap_must_be_positive(cap: str) -> None:
    code, err = _cli("--llm", "--max-cost", cap)
    assert code == EXIT_USAGE
    assert "--max-cost" in err


def test_case_without_llm_is_refused() -> None:
    code, err = _cli("--case", "runner-oom-killed")
    assert code == EXIT_USAGE
    assert "--case only applies with --llm" in err


def test_a_full_cli_run_writes_both_formats_from_one_run(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake(job: JobRef, distilled: DistilledLog, hits: list[RuleHit], **kwargs: Any) -> Any:
        calls.append(job.name)
        assert kwargs["model"] == MODEL and kwargs["effort"] == "low"
        return _stub(lambda _id: "runner")(job, distilled, hits)

    monkeypatch.setattr("pipelinemd.cli.llm_available", lambda: True)
    monkeypatch.setattr("pipelinemd.cli.run_diagnosis", fake)
    text, data = tmp_path / "llm.txt", tmp_path / "llm.json"
    code, err = _cli(
        "--llm",
        "--model",
        MODEL,
        "--effort",
        "low",
        "--case",
        "runner-oom-killed",
        "--case",
        "yaml-unknown-stage",
        "-o",
        str(text),
        "-o",
        str(data),
    )

    assert code == 0, err
    assert sorted(calls) == ["runner-oom-killed", "yaml-unknown-stage"]  # once each
    assert "2 of 2 cases answered" in text.read_text(encoding="utf-8")
    assert json.loads(data.read_text(encoding="utf-8"))["answered"] == 2
    assert "[2/2]" in err


def test_an_unknown_case_is_refused_before_any_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pipelinemd.cli.llm_available", lambda: True)
    code, err = _cli("--llm", "--case", "no-such-case")
    assert code == EXIT_USAGE
    assert "no corpus case named no-such-case" in err


def test_eval_without_llm_writes_both_formats_too(tmp_path: Any) -> None:
    text, data = tmp_path / "eval.txt", tmp_path / "eval.json"
    code, err = _cli("-o", str(text), "-o", str(data))

    assert code == 0, err
    assert text.read_text(encoding="utf-8").startswith("pipelinemd eval")
    assert json.loads(data.read_text(encoding="utf-8"))["schema_version"] == 1

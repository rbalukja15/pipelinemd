"""Renderers, and the second truncation decision they make."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace

import pytest

from pipelinemd.distill import distill
from pipelinemd.models import (
    Category,
    Citation,
    Confidence,
    Diagnosis,
    FailureClass,
    Fix,
    JobRef,
    Report,
    RetryHistory,
)
from pipelinemd.render import (
    make_style,
    render_json,
    render_markdown,
    render_terminal,
    select_display_lines,
)
from pipelinemd.render.style import Style
from pipelinemd.rules import match_rules

DIAGNOSIS = Diagnosis(
    summary="npm ci failed on a peer dependency conflict",
    root_cause="The lockfile pins react@17 while package.json asks for ^18.",
    confidence=Confidence.HIGH,
    category=Category.DEPENDENCY,
    fixes=(Fix(title="Regenerate the lockfile", detail="Run npm install.", patch="npm install"),),
    citations=(Citation(line_number=142, text="npm ERR! code ERESOLVE", section="step_script"),),
    model="claude-opus-5",
    input_tokens=1200,
    output_tokens=400,
)

UNGROUNDED = Diagnosis(
    summary="Something failed",
    root_cause="Line 41203 shows it.",
    confidence=Confidence.LOW,
    category=Category.SCRIPT,
    citations=(Citation(line_number=142, text="npm ERR! code ERESOLVE"),),
    unresolved_citations=(41203,),
    model="claude-opus-5",
)


@pytest.fixture
def report(trace: Callable[[str], str]) -> Report:
    distilled = distill(trace("npm_eresolve"))
    return Report(
        job=JobRef(
            name="build",
            id=98765,
            stage="test",
            project="acme/web",
            ref="main",
            url="https://gitlab.com/acme/web/-/jobs/98765",
            duration_s=47.0,
        ),
        distilled=distilled,
        hits=match_rules(distilled),
    )


# -- style ------------------------------------------------------------------


def test_style_disabled_emits_no_escapes() -> None:
    style = Style(enabled=False)
    assert style.red("x") == "x"
    assert "\x1b" not in style.heading("Evidence")


def test_style_enabled_wraps_and_resets() -> None:
    assert Style(enabled=True).red("x") == "\x1b[31mx\x1b[0m"


def test_no_color_env_disables_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert not make_style("auto").enabled
    assert make_style("always").enabled, "explicit --color=always still wins"


# -- evidence selection -----------------------------------------------------


def test_display_selection_is_a_noop_under_the_limit(report: Report) -> None:
    lines, dropped = select_display_lines(report.distilled, 10_000)
    assert dropped == 0
    assert len(lines) == report.distilled.stats.evidence_lines


def test_display_selection_prefers_the_failure_over_the_preamble(report: Report) -> None:
    """Taking the first N lines would show the docker image pull, not the error."""
    lines, dropped = select_display_lines(report.distilled, 10)
    assert dropped > 0
    text = "\n".join(line.text for line in lines)
    assert "npm ERR! code ERESOLVE" in text
    assert "ERROR: Job failed" in text, "the verdict is always kept"
    assert "Pulling docker image" not in text


def test_display_selection_shows_the_head_of_an_error_block(report: Report) -> None:
    """The top of an npm ERR! block names the conflict; the bottom repeats it."""
    lines, _ = select_display_lines(report.distilled, 10)
    npm_lines = [line for line in lines if line.text.startswith("npm ERR!")]
    assert npm_lines
    assert npm_lines[0].text == "npm ERR! code ERESOLVE"


def test_blank_lines_do_not_take_slots_from_content(report: Report) -> None:
    lines, _ = select_display_lines(report.distilled, 8)
    blanks = [line for line in lines if not line.text.strip()]
    assert len(blanks) <= 1


# -- terminal ---------------------------------------------------------------


def test_terminal_report_without_a_diagnosis_falls_back_to_rules(report: Report) -> None:
    out = render_terminal(report, Style(enabled=False))
    assert "npm.eresolve" in out
    assert "Regenerate the lockfile" not in out
    assert "Rule matches" in out
    assert "Evidence" in out


def test_terminal_report_with_a_diagnosis(report: Report) -> None:
    out = render_terminal(
        Report(job=report.job, distilled=report.distilled, hits=report.hits, diagnosis=DIAGNOSIS),
        Style(enabled=False),
    )
    assert "Diagnosis" in out
    assert DIAGNOSIS.summary in out
    assert "Regenerate the lockfile" in out
    assert "npm install" in out


def test_terminal_report_is_plain_when_colour_is_off(report: Report) -> None:
    assert "\x1b" not in render_terminal(report, Style(enabled=False))


def test_terminal_handles_no_rule_matches() -> None:
    distilled = distill("$ make\nall good\n")
    out = render_terminal(Report(job=JobRef(name="x"), distilled=distilled), Style(enabled=False))
    assert "none" in out


# -- markdown ---------------------------------------------------------------


def test_markdown_is_paste_ready(report: Report) -> None:
    out = render_markdown(report)
    assert out.startswith("### pipelinemd")
    assert "<details>" in out and "</details>" in out
    assert "```log" in out
    assert "[View job](https://gitlab.com/acme/web/-/jobs/98765)" in out
    assert "\x1b" not in out


def test_markdown_credits_the_model_when_one_ran(report: Report) -> None:
    out = render_markdown(
        Report(job=report.job, distilled=report.distilled, hits=report.hits, diagnosis=DIAGNOSIS)
    )
    assert "claude-opus-5" in out
    assert DIAGNOSIS.root_cause in out


# -- json -------------------------------------------------------------------


def test_json_is_valid_and_versioned(report: Report) -> None:
    payload = json.loads(render_json(report))
    assert payload["schema_version"] == 1
    assert payload["job"]["id"] == 98765
    assert payload["trace"]["exit_code"] == 1
    assert payload["trace"]["stats"]["raw_lines"] > 100
    assert payload["rule_hits"][0]["id"] == "npm.eresolve"
    assert payload["diagnosis"] is None


def test_json_includes_the_diagnosis_when_present(report: Report) -> None:
    payload = json.loads(
        render_json(
            Report(
                job=report.job, distilled=report.distilled, hits=report.hits, diagnosis=DIAGNOSIS
            )
        )
    )
    assert payload["diagnosis"]["confidence"] == "high"
    assert payload["diagnosis"]["usage"]["input_tokens"] == 1200
    assert payload["diagnosis"]["fixes"][0]["patch"] == "npm install"


# -- citations (#16) --------------------------------------------------------


def _with(report: Report, diagnosis: Diagnosis) -> Report:
    return Report(
        job=report.job,
        distilled=report.distilled,
        hits=report.hits,
        diagnosis=diagnosis,
    )


def test_terminal_shows_cited_evidence(report: Report) -> None:
    out = render_terminal(_with(report, DIAGNOSIS), Style(enabled=False))
    assert "Cited evidence" in out
    assert "L142" in out


def test_terminal_marks_cited_lines_in_the_evidence_block(report: Report) -> None:
    """A reader should be able to find the cited line without counting rows."""
    out = render_terminal(_with(report, DIAGNOSIS), Style(enabled=False), evidence_limit=200)
    cited_row = next(
        line for line in out.splitlines() if line.lstrip("›").strip().startswith("142 ")
    )
    assert cited_row.startswith("›")


def test_terminal_warns_about_invented_citations(report: Report) -> None:
    out = render_terminal(_with(report, UNGROUNDED), Style(enabled=False))
    assert "L41203" in out
    assert "not in the evidence" in out


def test_terminal_shows_the_collapse_count_on_a_cited_run(report: Report) -> None:
    """One quote standing for ten lines must say so."""
    folded = Diagnosis(
        summary="s",
        root_cause="r",
        confidence=Confidence.LOW,
        category=Category.SCRIPT,
        citations=(Citation(line_number=142, text="npm WARN deprecated", repeat=9),),
    )
    out = render_terminal(_with(report, folded), Style(enabled=False))
    assert "[x9]" in out


def test_terminal_clips_a_long_citation_visibly(report: Report) -> None:
    long = Diagnosis(
        summary="s",
        root_cause="r",
        confidence=Confidence.LOW,
        category=Category.SCRIPT,
        citations=(Citation(line_number=142, text="x" * 500),),
    )
    out = render_terminal(_with(report, long), Style(enabled=False))
    cited = next(line for line in out.splitlines() if line.strip().startswith("L142"))
    assert "…" in cited, "clipping must be visible, not silent"
    assert len(cited) < 200


def test_terminal_survives_an_absurdly_narrow_width(report: Report) -> None:
    """`width` is a public parameter; under 12 the old slice went negative."""
    narrow = Diagnosis(
        summary="s",
        root_cause="r",
        confidence=Confidence.LOW,
        category=Category.SCRIPT,
        citations=(Citation(line_number=142, text="npm ERR! code ERESOLVE"),),
    )
    out = render_terminal(_with(report, narrow), Style(enabled=False), width=4)
    cited = next(line for line in out.splitlines() if line.strip().startswith("L142"))
    assert cited.strip().startswith("L142")
    assert "npm" in cited, "a tiny width must still show the head of the line"


def test_markdown_shows_the_collapse_count(report: Report) -> None:
    folded = Diagnosis(
        summary="s",
        root_cause="r",
        confidence=Confidence.LOW,
        category=Category.SCRIPT,
        citations=(Citation(line_number=142, text="npm WARN deprecated", repeat=9),),
    )
    assert "[x9]" in render_markdown(_with(report, folded))


def test_markdown_shows_cited_evidence(report: Report) -> None:
    out = render_markdown(_with(report, DIAGNOSIS))
    assert "**Cited evidence**" in out
    assert "npm ERR! code ERESOLVE" in out


def test_markdown_warns_about_invented_citations(report: Report) -> None:
    out = render_markdown(_with(report, UNGROUNDED))
    assert "L41203" in out
    assert "suspicion" in out


def test_json_carries_citations_and_grounding(report: Report) -> None:
    payload = json.loads(render_json(_with(report, DIAGNOSIS)))["diagnosis"]
    assert payload["citations"] == [
        {
            "line_number": 142,
            "text": "npm ERR! code ERESOLVE",
            "section": "step_script",
            "repeat": 1,
        }
    ]
    assert payload["unresolved_citations"] == []
    assert payload["fully_grounded"] is True


def test_json_marks_an_ungrounded_diagnosis(report: Report) -> None:
    payload = json.loads(render_json(_with(report, UNGROUNDED)))["diagnosis"]
    assert payload["unresolved_citations"] == [41203]
    assert payload["fully_grounded"] is False


def test_json_evidence_keeps_line_numbers(report: Report) -> None:
    payload = json.loads(render_json(report))
    numbers = [line["number"] for block in payload["evidence"] for line in block["lines"]]
    assert numbers == sorted(numbers)
    assert all(isinstance(number, int) for number in numbers)


# -- classification (#17) ---------------------------------------------------


def test_terminal_header_states_the_class_and_where_the_fix_lives(report: Report) -> None:
    out = render_terminal(report, Style(enabled=False))
    header = out.split("Evidence")[0]
    assert "test → code_patch" in header
    assert "from npm.eresolve" in header


def test_terminal_says_when_the_model_reads_it_differently(report: Report) -> None:
    """Shown, not applied: the model's class never overrides the rules."""
    other = _with(report, replace(DIAGNOSIS, failure_class=FailureClass.CI_VARS))
    out = render_terminal(other, Style(enabled=False))
    assert "test → code_patch" in out, "the classification is still the rules'"
    assert "model reads it as ci_vars" in out


def test_terminal_is_quiet_when_the_model_agrees(report: Report) -> None:
    agrees = _with(report, replace(DIAGNOSIS, failure_class=FailureClass.TEST))
    assert "model reads it as" not in render_terminal(agrees, Style(enabled=False))


def test_an_unrecognised_log_is_unclassified_with_no_fix_type() -> None:
    bare = Report(job=JobRef(name="x"), distilled=distill("ERROR: Job failed: exit code 1\n"))
    out = render_terminal(bare, Style(enabled=False))
    assert "unclassified  ·  low  ·  no rule fired" in out
    assert "→" not in out.split("\n")[2]


def test_markdown_states_the_class(report: Report) -> None:
    md = render_markdown(report)
    assert "**class** `test` → `code_patch`" in md


def test_json_carries_the_classification_and_each_hits_class(report: Report) -> None:
    payload = json.loads(render_json(report))
    assert payload["schema_version"] == 1, "additive: no reader breaks"
    cls = payload["classification"]
    assert cls["failure_class"] == "test"
    assert cls["fix_type"] == "code_patch"
    assert cls["source"] == "rules"
    assert cls["retry"] is None
    assert all("failure_class" in entry for entry in payload["rule_hits"])


def test_json_carries_retry_history_when_it_was_fetched(report: Report) -> None:
    flaky = Report(
        job=report.job,
        distilled=report.distilled,
        hits=report.hits,
        retry=RetryHistory(("failed", "success")),
    )
    cls = json.loads(render_json(flaky))["classification"]
    assert cls["failure_class"] == "flaky"
    assert cls["fix_type"] == "flaky_retry"
    assert cls["retry"] == {
        "attempts": 2,
        "statuses": ["failed", "success"],
        "verdict": "passed_on_another_attempt",
    }


def test_json_carries_the_models_class_on_the_diagnosis(report: Report) -> None:
    payload = json.loads(render_json(_with(report, DIAGNOSIS)))
    assert payload["diagnosis"]["failure_class"] == "unclassified"


def test_a_model_that_declines_to_classify_is_not_a_disagreement(report: Report) -> None:
    """`unclassified` from the model beside a confident rule is silence, not dissent."""
    declined = _with(report, replace(DIAGNOSIS, failure_class=FailureClass.UNCLASSIFIED))
    assert "model reads it as" not in render_terminal(declined, Style(enabled=False))
    assert "model reads it as" not in render_markdown(declined)


def test_markdown_shows_a_real_disagreement_too(report: Report) -> None:
    other = _with(report, replace(DIAGNOSIS, failure_class=FailureClass.RUNNER))
    assert "model reads it as runner" in render_markdown(other)


# -- confidence and cost (#18) ----------------------------------------------


def test_terminal_shows_the_confidence_and_that_rules_cost_nothing(report: Report) -> None:
    out = render_terminal(report, Style(enabled=False))
    assert "  confidence high\n" in out
    assert "cost $0 — rules only, no model call" in out


def test_terminal_shows_the_estimated_cost_of_a_diagnosis(report: Report) -> None:
    """1,200 in and 400 out on Opus 5: $0.006 + $0.010."""
    out = render_terminal(_with(report, DIAGNOSIS), Style(enabled=False))
    assert "est. cost $0.0160 · claude-opus-5 · 1,200 in / 400 out tokens" in out


def test_terminal_sends_a_weak_analysis_to_review_and_says_why(report: Report) -> None:
    out = render_terminal(_with(report, UNGROUNDED), Style(enabled=False), width=400)
    line = next(line for line in out.splitlines() if "needs human review" in line)
    assert "low confidence" in line
    assert "the model rated its diagnosis low" in line
    assert "L41203" in line


def test_terminal_review_line_wraps_at_the_report_width(report: Report) -> None:
    out = render_terminal(_with(report, UNGROUNDED), Style(enabled=False), width=60)
    start = out.index("needs human review")
    block = out[start : out.index("est. cost", start)]
    assert all(len(line) <= 60 for line in block.splitlines())


def test_markdown_shows_the_confidence_and_cost(report: Report) -> None:
    out = render_markdown(_with(report, DIAGNOSIS))
    assert "**confidence** high" in out
    assert "<sub>est. cost $0.0160 · claude-opus-5 · 1,200 in / 400 out tokens</sub>" in out
    assert "Needs human review" not in out


def test_markdown_flags_review_where_a_reviewer_will_see_it(report: Report) -> None:
    out = render_markdown(_with(report, UNGROUNDED))
    assert "> ⚠️ **Needs human review** — low confidence:" in out
    assert out.index("Needs human review") < out.index(UNGROUNDED.summary), "before the claim"


def test_json_carries_the_assessment(report: Report) -> None:
    payload = json.loads(render_json(_with(report, UNGROUNDED)))
    assert payload["assessment"] == {
        "confidence": "low",
        "needs_review": True,
        "review_below": "medium",
        "reasons": [
            "the model rated its diagnosis low",
            "the diagnosis cited L41203, which is not in the evidence",
        ],
    }


def test_json_carries_the_cost(report: Report) -> None:
    payload = json.loads(render_json(_with(report, DIAGNOSIS)))
    cost = payload["cost"]
    assert cost["estimated_usd"] == pytest.approx(0.016)
    assert cost["prices_as_of"]
    assert cost["unpriced_models"] == []
    assert cost["discarded_calls"] == 0
    assert (cost["total_input_tokens"], cost["total_output_tokens"]) == (1200, 400)
    assert cost["calls"] == [
        {
            "model": "claude-opus-5",
            "input_tokens": 1200,
            "output_tokens": 400,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "estimated_usd": pytest.approx(0.016),
        }
    ]


def test_json_rules_only_cost_is_zero_not_null(report: Report) -> None:
    """Null means "could not be priced"; a run that called no model cost nothing."""
    cost = json.loads(render_json(report))["cost"]
    assert cost["estimated_usd"] == 0.0
    assert cost["calls"] == []


def test_json_an_unpriced_model_is_null_not_a_guess(report: Report) -> None:
    payload = json.loads(render_json(_with(report, replace(DIAGNOSIS, model="claude-opus-9"))))
    assert payload["cost"]["estimated_usd"] is None
    assert payload["cost"]["unpriced_models"] == ["claude-opus-9"]
    assert payload["cost"]["calls"][0]["estimated_usd"] is None


def test_json_diagnosis_usage_gains_the_cache_counts(report: Report) -> None:
    usage = json.loads(render_json(_with(report, DIAGNOSIS)))["diagnosis"]["usage"]
    assert usage == {
        "input_tokens": 1200,
        "output_tokens": 400,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }

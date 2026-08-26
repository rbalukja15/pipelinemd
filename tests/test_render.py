"""Renderers, and the second truncation decision they make."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from pipelinemd.distill import distill
from pipelinemd.models import Category, Confidence, Diagnosis, Fix, JobRef, Report
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
    model="claude-opus-5",
    input_tokens=1200,
    output_tokens=400,
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


def test_json_evidence_keeps_line_numbers(report: Report) -> None:
    payload = json.loads(render_json(report))
    numbers = [line["number"] for block in payload["evidence"] for line in block["lines"]]
    assert numbers == sorted(numbers)
    assert all(isinstance(number, int) for number in numbers)

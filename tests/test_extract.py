"""Line scoring and evidence-window selection."""

from __future__ import annotations

import pytest

from pipelinemd.distill.extract import (
    DEFAULT_THRESHOLD,
    find_anchors,
    score_line,
    select_evidence,
)
from pipelinemd.models import TraceLine


def _lines(texts: list[str]) -> list[TraceLine]:
    return [
        TraceLine(number=index, raw_number=index, text=text)
        for index, text in enumerate(texts, start=1)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "ERROR: Job failed: exit code 1",
        "npm ERR! code ERESOLVE",
        "fatal: Authentication failed for https://gitlab.com/x.git",
        "Traceback (most recent call last):",
        "no space left on device",
        "/bin/sh: eval: line 1: terraform: not found",
        "--- FAIL: TestThing (0.01s)",
    ],
)
def test_real_failures_clear_the_threshold(text: str) -> None:
    assert score_line(text)[0] >= DEFAULT_THRESHOLD


@pytest.mark.parametrize(
    "text",
    [
        "Tests: 0 failed, 512 passed, 512 total",
        "found 0 vulnerabilities",
        "0 errors, 2 warnings",
        "downloading 100%",
        "",
        "   ",
        "added 1842 packages in 41s",
    ],
)
def test_success_lines_stay_below_the_threshold(text: str) -> None:
    assert score_line(text)[0] < DEFAULT_THRESHOLD


def test_job_failed_outranks_everything() -> None:
    verdict, _ = score_line("ERROR: Job failed: exit code 1")
    ordinary, _ = score_line("something failed")
    assert verdict > ordinary * 5


def test_signals_are_reported_by_name() -> None:
    _score, signals = score_line("npm ERR! code ERESOLVE")
    assert "npm-err" in signals


def test_find_anchors_returns_trace_order() -> None:
    lines = _lines(["quiet", "npm ERR! boom", "quiet", "ERROR: Job failed: exit code 1"])
    anchors = find_anchors(lines)
    assert [anchor.line_number for anchor in anchors] == [2, 4]


def test_tail_is_always_kept_even_when_nothing_anchors() -> None:
    lines = _lines([f"routine line {i}" for i in range(200)])
    blocks, anchors = select_evidence(lines, tail_lines=10)
    assert anchors == []
    assert blocks, "the runner's verdict lives at the end; never drop the tail"
    last = blocks[-1].lines[-1]
    assert last.number + last.repeat - 1 == 200


def test_budget_is_respected() -> None:
    texts = [f"npm ERR! failure {i}" for i in range(500)]
    blocks, _ = select_evidence(_lines(texts), max_lines=50)
    kept = sum(len(block.lines) for block in blocks)
    assert kept <= 60, "one window may overshoot; the budget must still bound the rest"


def test_repeated_lines_collapse_with_a_count() -> None:
    texts = ["start"] + ["npm WARN deprecated foo@1.0.0"] * 20 + ["npm ERR! boom"]
    blocks, _ = select_evidence(_lines(texts), tail_lines=30)
    collapsed = [line for block in blocks for line in block.lines if line.collapsed]
    assert collapsed, "20 identical lines should fold into one entry"
    assert collapsed[0].repeat == 20


def test_near_identical_noise_collapses() -> None:
    texts = [f"npm http fetch GET 200 registry/package-{i} 143ms" for i in range(30)]
    blocks, _ = select_evidence(_lines(texts), tail_lines=30)
    repeats = [line.repeat for block in blocks for line in block.lines]
    assert max(repeats) > 1, "digits-only differences are noise when there are many"


def test_enumerated_failures_are_not_collapsed_away() -> None:
    """Three failing tests differ only in their digits. Each still matters."""
    texts = ["=== short test summary info ==="] + [
        f"FAILED tests/test_billing.py::test_case_{i} - AssertionError" for i in range(1, 4)
    ]
    blocks, _ = select_evidence(_lines(texts), tail_lines=30)
    failures = [line for block in blocks for line in block.lines if line.text.startswith("FAILED")]
    assert len(failures) == 3
    assert all(line.repeat == 1 for line in failures)


def test_exact_repeats_still_collapse() -> None:
    texts = ["npm WARN deprecated inflight@1.0.6"] * 5
    blocks, _ = select_evidence(_lines(texts), tail_lines=30)
    lines = [line for block in blocks for line in block.lines]
    assert len(lines) == 1
    assert lines[0].repeat == 5


def test_anchors_are_flagged_in_the_evidence() -> None:
    lines = _lines(["quiet"] * 10 + ["npm ERR! code ERESOLVE"] + ["quiet"] * 3)
    blocks, _ = select_evidence(lines)
    flagged = [line for block in blocks for line in block.lines if line.is_anchor]
    assert [line.text for line in flagged] == ["npm ERR! code ERESOLVE"]


def test_empty_input_is_handled() -> None:
    assert select_evidence([]) == ([], [])

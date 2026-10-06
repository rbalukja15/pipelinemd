"""Trace cleaning: sections, timestamps, metadata extraction."""

from __future__ import annotations

from collections.abc import Callable

from pipelinemd.distill import distill
from pipelinemd.distill.trace import MAX_LINE_CHARS, POST_JOB_SECTION, clean_trace
from pipelinemd.rules.engine import match_rules

from .fixtures.secrets import GITLAB_PAT_PLAIN

K = "\r\x1b[0K"


def test_section_markers_become_structure_not_text() -> None:
    raw = (
        f"section_start:1700000000:step_script{K}\x1b[36;1mExecuting stage\x1b[0;m\n"
        "$ make build\n"
        f"section_end:1700000090:step_script{K}\n"
    )
    cleaned = clean_trace(raw)

    assert [line.text for line in cleaned.lines[:2]] == ["Executing stage", "$ make build"]
    assert not any("section_start" in line.text for line in cleaned.lines)

    (section,) = [s for s in cleaned.sections if s.name == "step_script"]
    assert section.duration_s == 90.0
    assert not section.failed_open


def test_lines_are_attributed_to_their_section() -> None:
    raw = (
        f"section_start:100:get_sources{K}Fetching\n"
        f"section_end:110:get_sources{K}"
        f"section_start:110:step_script{K}Running\n"
        "$ pytest\n"
    )
    cleaned = clean_trace(raw)
    sections = [line.section for line in cleaned.lines]
    assert sections[0] == "get_sources"
    assert sections[-1] == "step_script"


def test_several_markers_on_one_physical_line() -> None:
    raw = f"section_end:110:a{K}section_start:110:b{K}$ echo hi\n"
    cleaned = clean_trace(raw)
    assert cleaned.lines[0].text == "$ echo hi"
    assert cleaned.lines[0].section == "b"


def test_section_never_closed_is_reported_as_open() -> None:
    raw = f"section_start:100:step_script{K}Running\n$ boom\n"
    cleaned = clean_trace(raw)
    (section,) = cleaned.sections
    assert section.failed_open
    assert section.end_line is None


def test_collapsed_flag_is_tolerated() -> None:
    raw = f"section_start:100:prepare_executor[collapsed=true]{K}Preparing\n"
    cleaned = clean_trace(raw)
    assert cleaned.lines[0].text == "Preparing"
    assert cleaned.sections[0].name == "prepare_executor"


def test_runner_timestamps_are_stripped() -> None:
    raw = "2026-08-26T09:12:44.101010Z 00O $ npm ci\n2026-08-26T09:12:45.000000Z 00O done\n"
    cleaned = clean_trace(raw)
    assert [line.text for line in cleaned.lines[:2]] == ["$ npm ci", "done"]


def test_every_runner_stream_descriptor_is_stripped() -> None:
    raw = (
        "2026-08-26T09:12:44.101010Z 00O $ npm ci\n"
        "2026-08-26T09:12:45.000000Z 01E npm warn deprecated\n"
        "2026-08-26T09:12:45.500000Z 00O+continued output\n"
        "2021-01-01T04:00:00.020010Z ffE+Done.\r\n"
        "2021-01-01T04:00:00.020010Z ffE+\n"
        "2026-08-26T09:12:46.000000Z 00O   indented output\n"
    )
    cleaned = clean_trace(raw)
    assert [line.text for line in cleaned.lines[:6]] == [
        "$ npm ci",
        "npm warn deprecated",
        "continued output",
        "Done.",
        "",
        "  indented output",
    ]


def test_section_marker_on_a_continuation_line_is_parsed() -> None:
    # The runner writes a section marker with no newline of its own, so the
    # text after it arrives on a continuation line ("00O+").
    raw = (
        "2026-08-26T09:12:44.000000Z 01O section_start:100:foo\r\x1b[0K\n"
        "2026-08-26T09:12:45.000000Z 01O+inside\n"
        "2026-08-26T09:12:46.000000Z 01O+section_end:123:foo\r\x1b[0K\n"
        "2026-08-26T09:12:46.000000Z 01O+after\n"
    )
    cleaned = clean_trace(raw)
    assert [line.text for line in cleaned.lines[:4]] == ["", "inside", "", "after"]
    assert [(s.name, s.start_line, s.end_line) for s in cleaned.sections] == [("foo", 1, 3)]
    assert cleaned.lines[1].section == "foo"
    assert cleaned.lines[3].section is None


def test_short_first_word_after_a_runner_descriptor_survives() -> None:
    raw = "2026-08-26T09:12:44.101010Z 00O Run tests\n"
    cleaned = clean_trace(raw)
    assert cleaned.lines[0].text == "Run tests"


def test_timestamp_without_descriptor_keeps_every_word() -> None:
    """GitHub Actions stamps lines with the time alone (#49)."""
    words = [
        "Post job cleanup.",
        "Run actions/checkout@v4",
        "with node-version: 20",
        "LTS release",
        "2024 was the cutoff",
        "  node-version: 20",
        " * [new ref]         main -> main",
    ]
    raw = "".join(f"2026-10-06T09:51:58.7516961Z {text}\n" for text in words)
    cleaned = clean_trace(raw)
    assert [line.text for line in cleaned.lines[: len(words)]] == words


def test_indentation_after_a_bare_timestamp_is_kept_so_column_rules_do_not_fire() -> None:
    # Vitest indents its FAIL lines; losing that space would let the Go rule
    # anchored at column 0 claim a TypeScript failure.
    raw = "2026-10-06T09:51:58.7516961Z  FAIL  src/cart.test.ts > applies the discount\n"
    cleaned = clean_trace(raw)
    assert cleaned.lines[0].text == " FAIL  src/cart.test.ts > applies the discount"
    assert "test.go-failed" not in {hit.rule.id for hit in match_rules(distill(raw))}


def test_byte_order_mark_does_not_keep_the_first_timestamp() -> None:
    """GitHub's job logs begin with a UTF-8 byte order mark."""
    raw = (
        "\ufeff2026-10-06T08:43:30.0997190Z Current runner version: '2.337.0'\n"
        "2026-10-06T08:43:49.3916783Z Post job cleanup.\n"
    )
    cleaned = clean_trace(raw)
    assert [line.text for line in cleaned.lines[:2]] == [
        "Current runner version: '2.337.0'",
        "Post job cleanup.",
    ]


def test_timestamp_only_lines_keep_their_text_through_distill() -> None:
    raw = "2026-10-06T09:51:58.7516961Z Post job cleanup.\n"
    result = distill(raw)
    assert result.clean_text().splitlines()[0] == "Post job cleanup."
    assert result.evidence_text().endswith("Post job cleanup.")


def test_metadata_is_extracted() -> None:
    raw = (
        "Running with gitlab-runner 16.9.1 (dcfb4b66)\n"
        "  on blue-3.shared ntHFEtyX\n"
        "Using Docker executor with image node:20-alpine ...\n"
        "$ npm ci\n"
        "$ npm test\n"
        "ERROR: Job failed: exit code 137\n"
    )
    cleaned = clean_trace(raw)
    assert cleaned.runner == "16.9.1 (dcfb4b66) on blue-3.shared ntHFEtyX"
    assert cleaned.image == "node:20-alpine"
    assert cleaned.exit_code == 137
    assert cleaned.failure_reason == "ERROR: Job failed: exit code 137"
    assert cleaned.commands == ["npm ci", "npm test"]


def test_progress_bars_collapse_to_their_final_frame() -> None:
    raw = "\r".join(f"downloading {p}%" for p in range(0, 101, 10)) + "\n"
    cleaned = clean_trace(raw)
    assert cleaned.lines[0].text == "downloading 100%"


def test_overlong_lines_are_truncated_visibly() -> None:
    raw = "x" * 5000 + "\n"
    cleaned = clean_trace(raw)
    text = cleaned.lines[0].text
    assert len(text) < 5000
    assert text.startswith("x" * MAX_LINE_CHARS)
    assert "truncated" in text


def test_raw_line_numbers_survive_cleaning() -> None:
    raw = f"a\nsection_start:1:x{K}b\nc\n"
    cleaned = clean_trace(raw)
    assert [line.raw_number for line in cleaned.lines[:3]] == [1, 2, 3]
    assert [line.number for line in cleaned.lines[:3]] == [1, 2, 3]


def test_secrets_are_gone_by_the_time_lines_exist() -> None:
    raw = f"export TOKEN={GITLAB_PAT_PLAIN}\n"
    cleaned = clean_trace(raw)
    assert GITLAB_PAT_PLAIN not in cleaned.lines[0].text


GITHUB_VERDICT = "##[error]Process completed with exit code 1."


def test_github_post_job_steps_are_read_as_one_section(trace: Callable[[str], str]) -> None:
    """Everything GitHub runs after its verdict is fallout, like after_script."""
    cleaned = clean_trace(trace("github/pytest_service_container"))
    verdict = next(line for line in cleaned.lines if line.text == GITHUB_VERDICT)
    assert verdict.section is None
    after = [line for line in cleaned.lines if line.number > verdict.number]
    assert after
    assert all(line.section == POST_JOB_SECTION for line in after)
    post_job = next(section for section in cleaned.sections if section.name == POST_JOB_SECTION)
    assert post_job.start_line == verdict.number + 1
    assert not post_job.failed_open, "the job did not die in its cleanup"


def test_github_verdict_is_the_failure_reason_and_the_exit_code() -> None:
    raw = (
        "2026-10-05T09:10:22.0000000Z FAILED tests/test_cart.py::test_total - assert 1 == 2\n"
        f"2026-10-05T09:10:22.1000000Z {GITHUB_VERDICT}\n"
        "2026-10-05T09:10:22.2000000Z Print service container logs: shop_postgres16alpine\n"
        "2026-10-05T09:10:22.3000000Z  LOG:  background worker (PID 62) exited with exit code 3\n"
    )
    cleaned = clean_trace(raw)
    assert cleaned.failure_reason == GITHUB_VERDICT
    assert cleaned.exit_code == 1, "a service container's own exit code is not the job's"


def test_a_github_job_that_failed_in_an_action_starts_post_job_at_its_cleanup() -> None:
    """An action's failure prints no "Process completed" line."""
    raw = (
        "2026-10-05T09:10:22.0000000Z ##[error]Unable to find Node version '25' for linux x64\n"
        "2026-10-05T09:10:22.1000000Z Post job cleanup.\n"
        "2026-10-05T09:10:22.2000000Z [command]/usr/bin/git version\n"
    )
    cleaned = clean_trace(raw)
    assert [line.section for line in cleaned.lines[:3]] == [
        None,
        POST_JOB_SECTION,
        POST_JOB_SECTION,
    ]


def test_a_github_verdict_on_the_last_line_opens_no_section() -> None:
    cleaned = clean_trace(f"2026-10-05T09:10:22.1000000Z {GITHUB_VERDICT}\n")
    assert cleaned.sections == []
    assert cleaned.failure_reason == GITHUB_VERDICT


def test_gitlab_traces_have_no_post_job_section(any_trace: tuple[str, str]) -> None:
    _name, raw = any_trace
    cleaned = clean_trace(raw)
    assert all(line.section != POST_JOB_SECTION for line in cleaned.lines)
    assert all(section.name != POST_JOB_SECTION for section in cleaned.sections)

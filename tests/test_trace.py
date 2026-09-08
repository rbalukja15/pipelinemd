"""Trace cleaning: sections, timestamps, metadata extraction."""

from __future__ import annotations

from pipelinemd.distill.trace import MAX_LINE_CHARS, clean_trace

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

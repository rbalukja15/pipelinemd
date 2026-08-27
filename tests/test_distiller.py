"""End-to-end distillation against the recorded traces."""

from __future__ import annotations

from collections.abc import Callable

from pipelinemd.distill import distill

from .fixtures.secrets import ALL_SECRETS


def test_distillation_is_deterministic(any_trace: tuple[str, str]) -> None:
    """Same trace in, same evidence out - the whole caching story rests on this."""
    _name, raw = any_trace
    first, second = distill(raw), distill(raw)
    assert first.evidence_text() == second.evidence_text()
    assert first.stats == second.stats


def test_every_trace_yields_evidence(any_trace: tuple[str, str]) -> None:
    name, raw = any_trace
    result = distill(raw)
    assert result.evidence, f"{name} produced no evidence"
    assert result.stats.evidence_lines <= result.stats.clean_lines


def test_every_trace_reports_its_exit_code(any_trace: tuple[str, str]) -> None:
    name, raw = any_trace
    result = distill(raw)
    assert result.exit_code is not None, f"{name} has no exit code"
    assert result.failure_reason and result.failure_reason.startswith("ERROR: Job failed")


def test_no_escape_codes_survive(any_trace: tuple[str, str]) -> None:
    _name, raw = any_trace
    assert "\x1b" not in distill(raw).clean_text()


def test_no_section_markers_survive(any_trace: tuple[str, str]) -> None:
    _name, raw = any_trace
    text = distill(raw).clean_text()
    assert "section_start:" not in text
    assert "section_end:" not in text


def test_noisy_trace_is_reduced_hard(trace: Callable[[str], str]) -> None:
    result = distill(trace("noisy_lint_failure"))
    assert result.stats.raw_lines > 3000
    assert result.stats.reduction > 0.98, "3,000 lines of registry chatter should vanish"
    assert "✖ 2 problems (2 errors, 0 warnings)" in result.evidence_text()


def test_evidence_keeps_the_verdict(any_trace: tuple[str, str]) -> None:
    _name, raw = any_trace
    assert "ERROR: Job failed" in distill(raw).evidence_text()


def test_sections_and_commands_are_recovered(trace: Callable[[str], str]) -> None:
    result = distill(trace("npm_eresolve"))
    names = {section.name for section in result.sections}
    assert {"prepare_executor", "get_sources", "step_script"} <= names
    assert result.commands[-1] == "npm ci --prefer-offline"
    assert result.image == "node:20-alpine"
    assert result.runner is not None and result.runner.startswith("16.9.1")


def test_secrets_never_reach_the_output(secrets_trace: str) -> None:
    result = distill(secrets_trace)
    everything = result.clean_text() + result.evidence_text()
    for secret in ALL_SECRETS:
        assert secret not in everything, f"{secret[:12]}… leaked into the report"


def test_secrets_trace_still_distils_normally(secrets_trace: str) -> None:
    """Redaction must not cost us the diagnosis."""
    result = distill(secrets_trace)
    assert result.exit_code == 2
    assert "Connection refused" in result.evidence_text()


def test_empty_trace_does_not_crash() -> None:
    result = distill("")
    assert result.stats.evidence_lines >= 0
    assert result.exit_code is None


def test_budget_is_honoured_end_to_end(trace: Callable[[str], str]) -> None:
    result = distill(trace("npm_eresolve"), max_lines=12)
    assert result.stats.evidence_lines <= 12

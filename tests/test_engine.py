"""Rule matching, including which hit wins when several fire."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from pipelinemd.distill import distill
from pipelinemd.rules import match_rules

# The whole point of the tool, expressed as a table.
EXPECTED_TOP_RULE = {
    "npm_eresolve": "npm.eresolve",
    "npm_lockfile_out_of_sync": "npm.lockfile-out-of-sync",
    "docker_daemon_unreachable": "docker.daemon-unreachable",
    "pytest_failures": "test.pytest-failed",
    "node_heap_oom": "node.heap-oom",
    "no_space_left": "runner.no-space",
    "command_not_found": "shell.command-not-found",
    "git_submodule_auth": "git.submodule-failed",
    "noisy_lint_failure": "lint.eslint",
}


@pytest.mark.parametrize(("name", "rule_id"), sorted(EXPECTED_TOP_RULE.items()))
def test_top_rule_names_the_real_failure(
    trace: Callable[[str], str], name: str, rule_id: str
) -> None:
    hits = match_rules(distill(trace(name)))
    assert hits, f"{name}: no rule fired"
    assert hits[0].rule.id == rule_id, (
        f"{name}: expected {rule_id} first, got {[hit.rule.id for hit in hits[:3]]}"
    )


def test_cleanup_noise_never_outranks_the_real_cause(
    trace: Callable[[str], str],
) -> None:
    """The artifact upload fails *because* the build did. It is fallout."""
    hits = match_rules(distill(trace("npm_eresolve")))
    ranked = [hit.rule.id for hit in hits]
    assert "ci.artifact-missing" in ranked, "the fallout is still worth reporting"
    assert ranked.index("npm.eresolve") < ranked.index("ci.artifact-missing")


def test_evidence_hits_outrank_whole_trace_hits() -> None:
    """A rule that only fires far outside the failure region ranks lower.

    "WARNING: Cache file does not exist" scores below the anchor threshold, so it never
    earns an evidence window - it is found only on the fallback scan of the
    whole trace, and is scored accordingly.
    """
    raw = (
        "WARNING: Cache file does not exist\n" + "routine\n" * 300 + "npm ERR! code ERESOLVE\n"
        "ERROR: Job failed: exit code 1\n"
    )
    hits = match_rules(distill(raw))
    by_id = {hit.rule.id: hit for hit in hits}
    assert by_id["npm.eresolve"].in_evidence
    assert not by_id["ci.cache-failed"].in_evidence
    assert by_id["npm.eresolve"].score > by_id["ci.cache-failed"].score


def test_exit_code_boosts_a_matching_rule() -> None:
    with_code = distill("/bin/sh: foo: not found\nERROR: Job failed: exit code 127\n")
    without = distill("/bin/sh: foo: not found\nERROR: Job failed: exit code 1\n")
    boosted = next(h for h in match_rules(with_code) if h.rule.id == "shell.command-not-found")
    plain = next(h for h in match_rules(without) if h.rule.id == "shell.command-not-found")
    assert boosted.score > plain.score


def test_excludes_prevent_a_false_positive() -> None:
    """A passing Jest run must not match the Jest failure rule."""
    passing = distill(
        "Tests:       0 failed, 512 passed, 512 total\nERROR: Job failed: exit code 1\n"
    )
    assert "test.jest-failed" not in {hit.rule.id for hit in match_rules(passing)}


def test_a_clean_log_matches_nothing() -> None:
    hits = match_rules(distill("$ make\nBuilding...\nDone in 4s\n"))
    assert hits == []


def test_limit_is_respected(trace: Callable[[str], str]) -> None:
    assert len(match_rules(distill(trace("npm_eresolve")), limit=1)) == 1


def test_hits_are_sorted_by_score(trace: Callable[[str], str]) -> None:
    hits = match_rules(distill(trace("git_submodule_auth")))
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)

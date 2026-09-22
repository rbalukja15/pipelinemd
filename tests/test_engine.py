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


# ---------------------------------------------------------------------------
# Cause vs. fallout, second instalment
#
# Each of these is a miss the eval harness found on its first run over the
# labelled corpus, reproduced here as the smallest trace that shows it. They
# are regression tests in the strict sense: every one fails on the catalog as
# it stood before this change.
# ---------------------------------------------------------------------------


def _job(*body: str, verdict: str = "ERROR: Job failed: exit code 1") -> str:
    """The smallest thing the distiller will accept as a failed job."""
    return "\n".join(
        (
            "Running with gitlab-runner 16.11.0 (abc)",
            'section_start:1700000020:step_script\x1b[0KExecuting "step_script" stage',
            *body,
            "section_end:1700000090:step_script\x1b[0K",
            verdict,
            "",
        )
    )


def _ranked(raw: str) -> list[str]:
    return [hit.rule.id for hit in match_rules(distill(raw))]


def test_the_pytest_rule_does_not_claim_jest_output() -> None:
    """`\\d+ failed,` is generic; `Tests:  1 failed, 511 passed` is Jest's.

    A rule named for one framework outranking the rule named for the other, on
    that framework's own output, is a confident wrong answer - the worst kind.
    """
    ranked = _ranked(
        _job(
            "$ npx jest --ci",
            "  \u25cf CheckoutSummary \u203a renders the tax line",
            "Test Suites: 1 failed, 83 passed, 84 total",
            "Tests:       1 failed, 511 passed, 512 total",
        )
    )
    assert ranked[0] == "test.jest-failed"
    assert "test.pytest-failed" not in ranked


def test_real_pytest_output_still_wins() -> None:
    """The exclusion must not cost the rule its own framework."""
    ranked = _ranked(
        _job(
            "$ pytest -q",
            "=================================== FAILURES ===================================",
            "FAILED tests/test_money.py::test_rounding - assert 0.1 + 0.2 == 0.3",
            "=========================== 1 failed, 2 passed in 0.12s ========================",
        )
    )
    assert ranked[0] == "test.pytest-failed"


def test_a_timeout_is_not_a_refusal() -> None:
    """Silence and a closed port need different advice, so they are different rules."""
    ranked = _ranked(
        _job(
            "$ curl --fail --max-time 30 https://api.example.com/health",
            "curl: (28) Operation timed out after 30001 milliseconds with 0 bytes received",
            verdict="ERROR: Job failed: exit code 28",
        )
    )
    assert ranked[0] == "net.connection-timeout"
    assert "net.connection-refused" not in ranked


def test_a_refusal_is_still_a_refusal() -> None:
    ranked = _ranked(
        _job("$ psql -h postgres", "psql: error: connection to server failed: Connection refused")
    )
    assert ranked[0] == "net.connection-refused"
    assert "net.connection-timeout" not in ranked


def test_the_runners_verdict_outranks_what_it_quotes() -> None:
    """`ERROR: Job failed (system failure): Cannot connect to the Docker daemon`.

    Both rules are right about that line. The runner's conclusion wins: the
    docker daemon is the *runner's*, the job never started, and "fix your
    docker setup" is advice about the wrong machine.
    """
    raw = (
        "Running with gitlab-runner 16.11.0 (abc)\n"
        'section_start:1700000001:prepare_executor\x1b[0KPreparing the "docker" executor\n'
        "ERROR: Job failed (system failure): Cannot connect to the Docker daemon at "
        "unix:///var/run/docker.sock. Is the docker daemon running?\n"
        "ERROR: Job failed: exit code 1\n"
    )
    ranked = _ranked(raw)
    assert ranked[0] == "runner.system-failure"
    assert ranked[1] == "docker.daemon-unreachable", (
        "the quoted cause is demoted, not hidden - it is the useful detail"
    )


def test_a_docker_daemon_failure_in_the_script_is_still_the_job_s() -> None:
    """The demotion is scoped to the verdict line, not to the message."""
    ranked = _ranked(
        _job(
            "$ docker build -t app .",
            "Cannot connect to the Docker daemon at tcp://docker:2375. "
            "Is the docker daemon running?",
        )
    )
    assert ranked[0] == "docker.daemon-unreachable"


def test_a_cache_backend_credential_is_not_the_jobs_aws_credential() -> None:
    """AccessDenied inside a runner cache transfer belongs to `[runners.cache]`.

    aws.no-credentials would send someone to check AWS_ACCESS_KEY_ID, which the
    runner does not use for this - the wrong machine again.
    """
    ranked = _ranked(
        _job(
            "WARNING: Retrieving cache from S3 failed: AccessDenied: Access Denied",
            "Failed to extract cache",
        )
    )
    assert ranked[0] == "ci.cache-failed"
    assert "aws.no-credentials" not in ranked


def test_a_genuine_aws_access_denial_still_fires() -> None:
    """The exclusion is scoped to the runner's cache lines, not to AccessDenied."""
    ranked = _ranked(
        _job(
            "$ aws s3 cp dist/ s3://acme-releases/ --recursive",
            "upload failed: dist/app.js to s3://acme-releases/app.js "
            "An error occurred (AccessDenied) when calling the PutObject operation",
        )
    )
    assert ranked[0] == "aws.no-credentials"

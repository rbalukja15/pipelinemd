"""Rule matching, including which hit wins when several fire."""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest

from pipelinemd.distill import distill
from pipelinemd.rules import get_rule, match_rules
from pipelinemd.rules.engine import WRONG_MACHINE_WHEN_RESTATED

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


# GitHub Actions logs, with their post-job steps and service containers.
EXPECTED_TOP_RULE_GITHUB = {
    "github/pytest_service_container": "test.pytest-failed",
    "github/npm_eresolve_docker_build": "npm.eresolve",
}


@pytest.mark.parametrize(("name", "rule_id"), sorted(EXPECTED_TOP_RULE_GITHUB.items()))
def test_top_rule_names_the_real_failure_in_a_github_log(
    trace: Callable[[str], str], name: str, rule_id: str
) -> None:
    hits = match_rules(distill(trace(name)))
    assert hits, f"{name}: no rule fired"
    assert hits[0].rule.id == rule_id, (
        f"{name}: expected {rule_id} first, got {[hit.rule.id for hit in hits[:3]]}"
    )


def test_a_service_container_s_log_is_fallout(trace: Callable[[str], str]) -> None:
    """Postgres prints "sh: locale: not found" while GitHub tears it down."""
    hits = match_rules(distill(trace("github/pytest_service_container")))
    ranked = [hit.rule.id for hit in hits]
    assert ranked.index("test.pytest-failed") < ranked.index("shell.command-not-found")


@pytest.mark.parametrize(
    ("line", "rule_id"),
    [
        ("npm error code ERESOLVE", "npm.eresolve"),
        ("npm error code E404", "npm.registry-404"),
        ("npm error 404 Not Found - GET https://registry.npmjs.org/@shop%2fui", "npm.registry-404"),
        ("npm error code E401", "npm.registry-auth"),
        ("npm error code E403", "npm.registry-auth"),
        (
            "npm error 401 Unauthorized - GET https://registry.npmjs.org/@shop%2fui",
            "npm.registry-auth",
        ),
        ("npm error code EBADENGINE", "npm.bad-engine"),
    ],
)
def test_npm_rules_read_npm_10_wording(line: str, rule_id: str) -> None:
    """npm 10 prints "npm error" where earlier versions printed "npm ERR!"."""
    hits = match_rules(distill(f"{line}\nERROR: Job failed: exit code 1\n"))
    assert rule_id in {hit.rule.id for hit in hits}


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
            'section_start:1700000020:step_script\r\x1b[0KExecuting "step_script" stage',
            *body,
            "section_end:1700000090:step_script\r\x1b[0K",
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
        'section_start:1700000001:prepare_executor\r\x1b[0KPreparing the "docker" executor\n'
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


# ---------------------------------------------------------------------------
# Review of #42: the first cut of the quoted-verdict rule keyed on offset
# alone, which demoted the actionable rule whenever the quoted text named a
# fault on the machine that genuinely needed fixing.
# ---------------------------------------------------------------------------


def _system_failure(reason: str) -> str:
    return (
        "Running with gitlab-runner 16.11.0 (abc)\n"
        'section_start:1700000001:prepare_executor\r\x1b[0KPreparing the "docker" executor\n'
        f"ERROR: Job failed (system failure): {reason}\n"
        "ERROR: Job failed: exit code 1\n"
    )


def test_a_named_cause_outranks_the_container_that_quotes_it() -> None:
    """`runner.system-failure` is a container; its own first fix says so.

    "Read the line right before this one - it names the underlying failure."
    When a rule *is* that underlying failure, and its advice points at the
    runner's own host, it is strictly the better answer - and it must win on
    score, not on an alphabetical tie-break it happens to be on the right side
    of today.
    """
    hits = match_rules(distill(_system_failure("no space left on device")))
    ranked = [hit.rule.id for hit in hits]
    assert ranked[0] == "runner.no-space"
    assert ranked[1] == "runner.system-failure"
    assert hits[0].score > hits[1].score, "must not depend on the id tie-break"


def test_a_quoted_cause_aimed_at_the_wrong_machine_is_still_demoted() -> None:
    """The other side of the same rule, so the two cannot drift apart."""
    ranked = [
        hit.rule.id
        for hit in match_rules(
            distill(
                _system_failure(
                    "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
                    "Is the docker daemon running?"
                )
            )
        )
    ]
    assert ranked[0] == "runner.system-failure"
    assert ranked[1] == "docker.daemon-unreachable"


def test_the_curl_refusal_pattern_matches_what_curl_actually_prints() -> None:
    """curl capitalises `Failed`; the pattern did not, and never matched it.

    The refusal case passed anyway, off `Connection refused` at the tail of the
    same line - so the pattern looked load-bearing while doing nothing.
    """
    pattern = next(p for p in get_rule("net.connection-refused").patterns if "connect to" in p)
    line = "curl: (7) Failed to connect to localhost port 5432 after 2 ms: Connection refused"
    assert re.compile(pattern, re.MULTILINE).search(line)


@pytest.mark.parametrize(
    "line",
    [
        # No `curl: (28)` tell, so only the case-insensitive phrase can catch it.
        "wget: unable to connect to api.example.com:443: Connection Timed Out",
        "java.net.SocketTimeoutException: Read Timed Out",
    ],
)
def test_a_capitalised_timeout_is_still_a_timeout(line: str) -> None:
    """JVM and Windows tooling title-case it; the guard was case-sensitive."""
    ranked = [hit.rule.id for hit in match_rules(distill(_job(line)))]
    assert "net.connection-timeout" in ranked
    assert "net.connection-refused" not in ranked


# ---------------------------------------------------------------------------
# Second review of #42: category was the wrong proxy for "whose machine", and
# the mechanism only engaged when the cause appeared on the verdict line alone.
# Real runner output prints it several times first - a WARNING per pull
# attempt, an `ERROR: Preparation failed:` per retry - so a rule's first match
# was never the verdict, and the container won on recency.
# ---------------------------------------------------------------------------

_IMAGE = "registry.gitlab.com/acme/ci:1.2"
_MANIFEST = (
    f'failed to pull image "{_IMAGE}" with specified policies [always]: '
    f"Error response from daemon: manifest for {_IMAGE} not found: manifest unknown"
)
_DAEMON = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock."


def _prepare_failure(error: str, *, warning: str | None = None, attempts: int = 3) -> str:
    """What gitlab-runner prints when a job fails before any script runs."""
    lines = [
        "Running with gitlab-runner 15.11.0 (436955cb)",
        'section_start:1700000001:prepare_executor\r\x1b[0KPreparing the "docker" executor',
    ]
    for _ in range(attempts):
        lines.append(f" Using Docker executor with image {_IMAGE} ...")
        if warning:
            lines.append(f'WARNING: Failed to pull image with policy "always": {warning}')
        lines += [f"ERROR: Preparation failed: {error}", "Will be retried in 3s ..."]
    lines.append(f"ERROR: Job failed (system failure): {error}")
    return "\n".join(lines) + "\n"


def test_a_job_side_cause_restated_by_the_runner_is_the_answer() -> None:
    """The tag in `image:` does not exist; fixing the tag is exactly right.

    docker.manifest-unknown is `docker`, like docker.daemon-unreachable, so the
    category-based rule demoted it. Its advice targets the right machine.
    """
    hits = match_rules(distill(_system_failure(_MANIFEST)))
    assert hits[0].rule.id == "docker.manifest-unknown"
    assert hits[1].rule.id == "runner.system-failure"
    assert hits[0].score > hits[1].score


def test_the_cause_wins_even_when_the_runner_printed_it_earlier_first() -> None:
    """The shape real runners produce: warnings and retries, then the verdict.

    The specific rule's first match is a WARNING line, long before the verdict,
    so recency alone handed it to the container on the last line.
    """
    trace = _prepare_failure(_MANIFEST, warning="manifest unknown")
    ranked = [hit.rule.id for hit in match_rules(distill(trace))]
    assert ranked[0] == "docker.manifest-unknown"
    assert ranked.index("runner.system-failure") < ranked.index("docker.pull-denied"), (
        "pull-denied claims every prepare-time pull failure; it must not outrank the reason"
    )


def test_a_wrong_machine_cause_is_demoted_in_the_realistic_shape_too() -> None:
    """With retries, the daemon rule used to lose only on recency - by luck."""
    hits = match_rules(distill(_prepare_failure(_DAEMON)))
    ranked = [hit.rule.id for hit in hits]
    assert ranked[:2] == ["runner.system-failure", "docker.daemon-unreachable"]
    container, daemon = hits[0].score, hits[1].score
    assert container - daemon >= 20, "the margin must come from the rule, not from recency"


def test_a_less_confident_cause_does_not_outrank_the_container() -> None:
    """The bonus settles a tie between equally confident rules, and no more.

    net.connection-timeout is MEDIUM and the container is HIGH, a 40-point gap
    that a 10-point bonus does not close. Whether a restated MEDIUM cause
    *should* outrank a HIGH container is open - no corpus case covers it - so
    this pins today's behaviour rather than endorsing it.
    """
    ranked = [h.rule.id for h in match_rules(distill(_system_failure("dial tcp: i/o timeout")))]
    assert ranked[:2] == ["runner.system-failure", "net.connection-timeout"]


def test_a_rule_does_not_collect_a_restatement_it_disclaims() -> None:
    """Exclusions apply on the verdict line, as everywhere else.

    The refusal rule fired honestly on curl's earlier line. The verdict that
    follows says "timed out", which that rule explicitly excludes - so it must
    not be scored as the cause the runner restated. Without the check it ties
    the timeout rule and wins on the id tie-break, naming a refusal on a line
    that reports a timeout.
    """
    trace = _job(
        "$ curl https://registry.example.com/v2/",
        "curl: (7) Failed to connect to registry.example.com port 443: Connection refused",
        verdict=(
            "ERROR: Job failed (system failure): Failed to connect to "
            "registry.example.com port 443: Connection timed out"
        ),
    )
    hits = {hit.rule.id: hit.score for hit in match_rules(distill(trace))}
    assert hits["net.connection-timeout"] > hits["net.connection-refused"]


def test_the_wrong_machine_list_is_short_and_deliberate() -> None:
    """Each entry needs a fix that is right in a job and wrong on the runner.

    `services: [docker:dind]` is the one known so far. If this grows, the new
    entry should be argued the same way, not inferred from its category.
    """
    assert {"docker.daemon-unreachable"} == WRONG_MACHINE_WHEN_RESTATED
    assert all(get_rule(rule_id) is not None for rule_id in WRONG_MACHINE_WHEN_RESTATED)

"""GitHub Actions: URL parsing, the HTTP client, the API mapping and the CLI path.

The payload shapes below follow recorded API responses (`/actions/runs/{id}/
jobs`, `/actions/jobs/{id}`), trimmed to the fields pipelinemd reads.
"""

from __future__ import annotations

import email.message
import io
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from pipelinemd import cli
from pipelinemd.cli import EXIT_GITLAB, EXIT_NOTHING, EXIT_OK, EXIT_USAGE, main
from pipelinemd.config import CIContext, detect_ci, resolve_github_credentials
from pipelinemd.distill import distill
from pipelinemd.errors import (
    GitHubAuthError,
    GitHubError,
    GitHubNotFoundError,
    RateLimitError,
    UsageError,
)
from pipelinemd.github import (
    GitHubClient,
    GitHubHttp,
    github_target_from_parts,
    looks_like_github,
    parse_github_target,
    retry_history,
)
from pipelinemd.github.url import api_url_for
from pipelinemd.models import RetryVerdict
from pipelinemd.rules import match_rules

TRACES = Path(__file__).parent / "fixtures" / "traces"

# -- URLs ---------------------------------------------------------------------


def test_a_run_url() -> None:
    target = parse_github_target("https://github.com/acme/shop/actions/runs/41432307443")
    assert (target.kind, target.id, target.repository) == ("run", 41432307443, "acme/shop")
    assert target.attempt is None
    assert target.api_url == "https://api.github.com"


def test_a_run_attempt_url() -> None:
    target = parse_github_target("github.com/acme/shop/actions/runs/7/attempts/2")
    assert (target.kind, target.id, target.attempt) == ("run", 7, 2)


def test_a_job_url_names_the_job() -> None:
    target = parse_github_target(
        "https://github.com/acme/shop/actions/runs/7/job/125233618223?pr=12"
    )
    assert (target.kind, target.id) == ("job", 125233618223)


def test_an_enterprise_server_url() -> None:
    target = parse_github_target("https://ghe.acme.io/acme/shop/actions/runs/7")
    assert target.api_url == "https://ghe.acme.io/api/v3"
    under_path = parse_github_target(
        "https://acme.io/github/acme/shop/actions/runs/7", "https://acme.io/github"
    )
    assert under_path.repository == "acme/shop"
    assert under_path.api_url == "https://acme.io/github/api/v3"


def test_github_and_gitlab_urls_are_told_apart() -> None:
    assert looks_like_github("https://github.com/acme/shop/actions/runs/7")
    assert not looks_like_github("https://gitlab.com/acme/shop/-/jobs/7")
    assert not looks_like_github("https://gitlab.com/acme/actions/runs/-/pipelines/7")


def test_a_github_url_that_is_not_a_run() -> None:
    with pytest.raises(UsageError, match="not a GitHub Actions run or job URL"):
        parse_github_target("https://github.com/acme/shop/pull/3")


def test_flags_need_exactly_one_of_run_or_job() -> None:
    with pytest.raises(UsageError, match="exactly one"):
        github_target_from_parts("https://github.com", "acme/shop")
    with pytest.raises(UsageError, match="owner/name"):
        github_target_from_parts("https://github.com", "shop", run=1)


def test_api_url_for() -> None:
    assert api_url_for("https://github.com") == "https://api.github.com"
    assert api_url_for("https://ghe.acme.io/") == "https://ghe.acme.io/api/v3"


# -- HTTP ---------------------------------------------------------------------


class _Response(io.BytesIO):
    """What urllib's handlers return: a body, a status and headers."""

    def __init__(self, url: str, status: int, body: bytes, headers: dict[str, str]) -> None:
        super().__init__(body)
        self.url = url
        self.status = self.code = status
        self.msg = "Found" if status == 302 else "OK"
        message = email.message.Message()
        for key, value in headers.items():
            message[key] = value
        self.headers = message

    def info(self) -> email.message.Message:
        return self.headers

    def geturl(self) -> str:
        return self.url

    def getcode(self) -> int:
        return self.status


class _Server(urllib.request.BaseHandler):
    """A fake https handler placed in the real opener chain, so redirects are urllib's.

    ``routes`` maps a URL prefix to (status, body, headers). Every request is
    recorded with the headers urllib actually sent.
    """

    handler_order = 100  # before the real HTTPSHandler

    def __init__(self, routes: dict[str, list[tuple[int, str, dict[str, str]]]]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, dict[str, str]]] = []

    def https_open(self, request: urllib.request.Request) -> _Response:
        url = request.full_url
        headers = {k.lower(): v for k, v in request.header_items()}
        self.requests.append((url, headers))
        for prefix, answers in self.routes.items():
            if url.startswith(prefix):
                status, body, extra = answers.pop(0) if len(answers) > 1 else answers[0]
                return _Response(url, status, body.encode(), extra)
        return _Response(url, 404, b'{"message": "Not Found"}', {})


def _http(server: _Server, **kwargs: Any) -> GitHubHttp:
    http = GitHubHttp("https://api.github.com", "s3cret", backoff=0, **kwargs)
    http._opener = urllib.request.build_opener(server)
    return http


def test_the_token_does_not_follow_the_log_redirect() -> None:
    """GitHub redirects a job log to a signed URL on another host."""
    signed = "https://results.blob.example/logs/1.txt?sig=abc"
    server = _Server(
        {
            "https://api.github.com/repos/acme/shop/actions/jobs/1/logs": [
                (302, "", {"Location": signed})
            ],
            "https://results.blob.example/": [(200, "the log\n", {})],
        }
    )
    text = _http(server).get_text("repos/acme/shop/actions/jobs/1/logs")
    assert text == "the log\n"
    (_first_url, first), (second_url, second) = server.requests
    assert first["authorization"] == "Bearer s3cret"
    assert second_url == signed
    assert "authorization" not in second
    assert second["user-agent"].startswith("pipelinemd/")


def test_pages_follow_the_link_header() -> None:
    base = "https://api.github.com/repos/acme/shop/actions/runs/7/jobs"
    server = _Server(
        {
            f"{base}?per_page=100&page=2": [(200, json.dumps({"jobs": [{"id": 2}]}), {})],
            base: [
                (
                    200,
                    json.dumps({"total_count": 2, "jobs": [{"id": 1}]}),
                    {"Link": f'<{base}?per_page=100&page=2>; rel="next", <{base}>; rel="last"'},
                )
            ],
        }
    )
    jobs = list(_http(server).paginate("repos/acme/shop/actions/runs/7/jobs", "jobs"))
    assert [job["id"] for job in jobs] == [1, 2]


def test_an_exhausted_rate_limit_names_the_reset_time() -> None:
    reset = int(time.time()) + 3600
    server = _Server(
        {
            "https://api.github.com/": [
                (
                    403,
                    '{"message": "API rate limit exceeded"}',
                    {
                        "x-ratelimit-remaining": "0",
                        "x-ratelimit-reset": str(reset),
                    },
                )
            ]
        }
    )
    with pytest.raises(RateLimitError, match="rate limit is used up until"):
        _http(server).get_json("repos/acme/shop")
    assert len(server.requests) == 1, "a far-off reset is not waited for"


def test_a_short_retry_after_is_honoured() -> None:
    server = _Server(
        {
            "https://api.github.com/": [
                (429, "{}", {"retry-after": "0"}),
                (200, '{"id": 1}', {}),
            ]
        }
    )
    assert _http(server).get_json("repos/acme/shop") == {"id": 1}
    assert len(server.requests) == 2


def test_a_rate_limit_wait_is_announced(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    server = _Server(
        {
            "https://api.github.com/": [
                (429, "{}", {"retry-after": "12"}),
                (200, '{"id": 1}', {}),
            ]
        }
    )
    notes: list[str] = []
    assert _http(server, notify=notes.append).get_json("repos/acme/shop") == {"id": 1}
    assert slept == [12.0]
    assert notes == ["waiting 12s for GitHub's rate limit to reset"]


def test_a_next_page_on_another_host_is_not_followed() -> None:
    """The token stays on its own instance even if a Link header points elsewhere."""
    base = "https://api.github.com/repos/acme/shop/actions/runs/7/jobs"
    server = _Server(
        {
            base: [
                (
                    200,
                    json.dumps({"jobs": [{"id": 1}]}),
                    {"Link": '<https://evil.example/steal?page=2>; rel="next"'},
                )
            ],
        }
    )
    pages = _http(server).paginate("repos/acme/shop/actions/runs/7/jobs", "jobs")
    with pytest.raises(GitHubError, match="another host"):
        list(pages)
    assert [url for url, _ in server.requests] == [f"{base}?per_page=100"]


def test_a_plain_403_is_an_auth_error_and_404_not_found() -> None:
    server = _Server({"https://api.github.com/repos/acme/x": [(403, "{}", {})]})
    with pytest.raises(GitHubAuthError, match="actions: read"):
        _http(server).get_json("repos/acme/x")
    with pytest.raises(GitHubNotFoundError):
        _http(_Server({})).get_json("repos/acme/y")


# -- API mapping ----------------------------------------------------------------


def _job(
    job_id: int,
    name: str,
    conclusion: str | None,
    *,
    attempt: int = 1,
    failed_step: str = "Run pytest",
) -> dict[str, Any]:
    return {
        "id": job_id,
        "run_id": 41432307443,
        "run_attempt": attempt,
        "name": name,
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
        "head_branch": "main",
        "head_sha": "83250ecee28808bc8e4b18c1d05fef1bfe974dff",
        "html_url": f"https://github.com/acme/shop/actions/runs/41432307443/job/{job_id}",
        "started_at": "2026-11-02T10:52:56Z",
        "completed_at": "2026-11-02T10:58:01Z",
        "steps": [
            {"name": "Set up job", "conclusion": "success", "number": 1},
            {
                "name": failed_step,
                "conclusion": "failure" if conclusion == "failure" else "success",
                "number": 9,
            },
            {"name": "Complete job", "conclusion": "success", "number": 19},
        ],
    }


class _Jobs:
    def __init__(self, jobs: list[dict[str, Any]]) -> None:
        self.jobs = jobs

    def paginate(self, path: str, key: str, params: Any = None, **_: Any) -> Any:
        return iter(self.jobs)


def test_failed_jobs_leave_cancelled_ones_for_last_resort() -> None:
    client = GitHubClient("https://api.github.com", "t")
    client.http = _Jobs(
        [_job(3, "lint", "cancelled"), _job(2, "test", "failure"), _job(1, "e2e", "timed_out")]
    )
    assert [job["name"] for job in client.failed_jobs("acme/shop", 7)] == ["e2e", "test"]
    client.http = _Jobs([_job(3, "lint", "cancelled"), _job(4, "ok", "success")])
    assert [job["name"] for job in client.failed_jobs("acme/shop", 7)] == ["lint"]


def test_job_ref_names_the_failed_step() -> None:
    ref = GitHubClient("https://api.github.com").job_ref(
        _job(125233618223, "Backend (Django + pytest)", "failure"), "acme/shop"
    )
    assert ref.stage == "Run pytest"
    assert ref.source == "github"
    assert ref.pipeline_id == 41432307443
    assert ref.duration_s == 305.0
    assert ref.label == "Backend (Django + pytest) (Run pytest) #125233618223"


def test_retry_history_maps_conclusions_onto_attempts() -> None:
    first = _job(1, "test", "failure", attempt=1)
    history = retry_history(
        first, [_job(2, "test", "success", attempt=2), first, _job(9, "x", "failure")]
    )
    assert history is not None
    assert history.statuses == ("failed", "success")
    assert history.verdict is RetryVerdict.PASSED_ON_ANOTHER_ATTEMPT
    timed_out = retry_history(first, [first, _job(2, "test", "timed_out", attempt=2)])
    assert timed_out is not None and timed_out.verdict is RetryVerdict.FAILED_EVERY_ATTEMPT


# -- config -------------------------------------------------------------------


def test_detect_ci_reads_the_actions_context() -> None:
    context = detect_ci(
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "acme/shop",
            "GITHUB_RUN_ID": "7",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_API_URL": "https://api.github.com",
            "GITHUB_REF_NAME": "main",
        }
    )
    assert context == CIContext(
        server_url="https://github.com",
        project_path="acme/shop",
        pipeline_id=7,
        job_id=None,
        ref="main",
        provider="github",
        api_url="https://api.github.com",
        run_attempt=2,
    )


def test_the_gitlab_token_variable_is_never_sent_to_github() -> None:
    assert not resolve_github_credentials(env={"PIPELINEMD_TOKEN": "glpat"}).present
    found = resolve_github_credentials(env={"GITHUB_TOKEN": "a", "GH_TOKEN": "b"})
    assert (found.token, found.source) == ("b", "GH_TOKEN")


# -- CLI ------------------------------------------------------------------------


class _FakeGitHub:
    """Stands in for GitHubClient without any network."""

    def __init__(
        self,
        *args: Any,
        jobs: list[dict[str, Any]] | None = None,
        attempts: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        self.jobs = jobs if jobs is not None else [_job(11, "test", "failure")]
        self.attempts = attempts if attempts is not None else self.jobs
        self.calls: list[tuple[str, Any]] = []
        self.real = GitHubClient("https://api.github.com")

    def failed_jobs(self, repository: str, run_id: int, *, attempt: int | None = None) -> Any:
        self.calls.append(("failed_jobs", (run_id, attempt)))
        return [job for job in self.jobs if job["conclusion"] in ("failure", "timed_out")]

    def list_run_jobs(self, repository: str, run_id: int, **kwargs: Any) -> Any:
        self.calls.append(("list_run_jobs", kwargs))
        return self.attempts

    def get_job(self, repository: str, job_id: int) -> dict[str, Any]:
        return next(job for job in [*self.jobs, *self.attempts] if job["id"] == job_id)

    def get_trace(self, repository: str, job_id: int) -> str:
        return (TRACES / "github" / "pytest_service_container.log").read_text()

    def get_run(self, repository: str, run_id: int) -> dict[str, Any]:
        return {"id": run_id, "path": ".github/workflows/ci.yml"}

    def get_workflow_file(self, repository: str, path: str, ref: str) -> str | None:
        return "jobs: {}\n"

    def job_ref(self, job: dict[str, Any], repository: str) -> Any:
        return self.real.job_ref(job, repository)


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), stdout=out, stderr=err, stdin=io.StringIO(""))
    return code, out.getvalue(), err.getvalue()


class _Patched:
    """The fake clients the CLI made, and the state the next one starts with."""

    def __init__(self) -> None:
        self.made: list[_FakeGitHub] = []
        self.state: dict[str, Any] = {}


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> _Patched:
    """Patch in a fake client, with a GitHub token in the environment."""
    patched = _Patched()

    def factory(api_url: str, token: str | None, **kwargs: Any) -> _FakeGitHub:
        client = _FakeGitHub(**patched.state)
        client.calls.append(("init", (api_url, token)))
        patched.made.append(client)
        return client

    monkeypatch.setattr(cli, "GitHubClient", factory)
    monkeypatch.setattr(cli, "detect_ci", lambda: None)
    for name in ("GITHUB_TOKEN", "PIPELINEMD_GITHUB_URL", "GITHUB_SERVER_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    return patched


def test_diagnose_a_run_url(github: _Patched) -> None:
    code, out, _err = run(
        "diagnose", "https://github.com/acme/shop/actions/runs/7", "--no-llm", "-f", "json"
    )
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["job"]["source"] == "github"
    assert payload["job"]["stage"] == "Run pytest"
    assert payload["classification"]["failure_class"] == "test"
    assert payload["cost"]["estimated_usd"] == 0
    (client,) = github.made
    assert client.calls[0] == ("init", ("https://api.github.com", "gh-token"))
    assert ("failed_jobs", (7, None)) in client.calls
    assert ("list_run_jobs", {"all_attempts": True}) in client.calls


def test_a_job_from_a_run_whose_rerun_passed_is_flaky(
    github: _Patched, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _job(11, "test", "failure", attempt=1)
    second = _job(12, "test", "success", attempt=2)
    github.state.update(jobs=[first], attempts=[first, second])
    code, out, _err = run(
        "diagnose", "https://github.com/acme/shop/actions/runs/7/job/11", "--no-llm", "-f", "json"
    )
    assert code == EXIT_OK
    classification = json.loads(out)["classification"]
    assert classification["failure_class"] == "flaky"
    assert classification["retry"]["statuses"] == ["failed", "success"]


def test_inside_actions_the_run_and_attempt_come_from_the_context(
    github: _Patched, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = CIContext(
        server_url="https://github.com",
        project_path="acme/shop",
        pipeline_id=7,
        job_id=None,
        ref="main",
        provider="github",
        api_url="https://api.github.com",
        run_attempt=3,
    )
    monkeypatch.setattr(cli, "detect_ci", lambda: context)
    code, _out, _err = run("diagnose", "--no-llm")
    assert code == EXIT_OK
    assert ("failed_jobs", (7, 3)) in github.made[0].calls


def test_a_run_with_nothing_failed(github: _Patched, monkeypatch: pytest.MonkeyPatch) -> None:
    github.state.update(jobs=[_job(1, "ok", "success")])
    code, _out, err = run("diagnose", "--repo", "acme/shop", "--run", "7", "--no-llm")
    assert code == EXIT_NOTHING
    assert "No failed jobs in run 7 of acme/shop" in err


def test_a_token_is_required_up_front(github: _Patched, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN")
    code, _out, err = run("diagnose", "https://github.com/acme/shop/actions/runs/7", "--no-llm")
    assert code == EXIT_USAGE
    assert "only serves job logs with a token" in err
    assert not github.made


def test_an_environment_token_is_not_sent_to_another_github(
    github: _Patched,
) -> None:
    code, _out, err = run("diagnose", "https://ghe.attacker.example/a/b/actions/runs/7", "--no-llm")
    assert code == EXIT_USAGE
    assert "not sending $GH_TOKEN to https://ghe.attacker.example" in err
    assert not github.made


def test_open_mr_is_gitlab_only(github: _Patched, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "llm_available", lambda: True)
    monkeypatch.setenv("PIPELINEMD_WRITE_TOKEN", "w")
    code, _out, err = run("diagnose", "https://github.com/acme/shop/actions/runs/7", "--open-mr")
    assert code == EXIT_USAGE
    assert "does not apply to GitHub" in err


def test_an_api_error_exits_like_gitlabs(github: _Patched, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise GitHubNotFoundError("Not found (404)", status=404)

    monkeypatch.setattr(cli, "GitHubClient", boom)
    code, _out, err = run("diagnose", "https://github.com/acme/shop/actions/runs/7", "--no-llm")
    assert code == EXIT_GITLAB
    assert "Not found" in err


def test_with_ci_config_hands_the_workflow_file_to_the_model(
    github: _Patched, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def maybe(report: Any, args: Any, ci_config: Any, stderr: Any, **kwargs: Any) -> Any:
        seen.update(ci_config=ci_config, **kwargs)
        return report

    monkeypatch.setattr(cli, "_maybe_diagnose", maybe)
    run("diagnose", "https://github.com/acme/shop/actions/runs/7", "--with-ci-config")
    assert seen == {"ci_config": "jobs: {}\n", "ci_config_path": ".github/workflows/ci.yml"}


# -- logs and rules -----------------------------------------------------------------


def _github_log(*step_output: str, header: str = "Run npm publish") -> str:
    lines = [
        "﻿2026-11-02T10:52:56.0000000Z Current runner version: '2.336.0'",
        "2026-11-02T10:52:56.1000000Z ##[group]Runner Image",
        "2026-11-02T10:52:56.1000000Z Image: ubuntu-24.04",
        "2026-11-02T10:52:56.1000000Z Version: 20261026.1.1",
        "2026-11-02T10:52:56.1000000Z ##[endgroup]",
        f"2026-11-02T10:52:57.0000000Z ##[group]{header}",
        "2026-11-02T10:52:57.0000000Z ##[endgroup]",
        *(f"2026-11-02T10:52:58.0000000Z {line}" for line in step_output),
        "2026-11-02T10:53:07.0000000Z Post job cleanup.",
    ]
    return "\n".join(lines) + "\n"


def _top(raw: str) -> str | None:
    hits = match_rules(distill(raw))
    return hits[0].rule.id if hits else None


def test_a_github_log_is_recognised_and_structured() -> None:
    distilled = distill(
        _github_log("npm error code E401", "##[error]Process completed with exit code 1.")
    )
    assert distilled.source == "github"
    assert distilled.runner == "GitHub Actions runner 2.336.0"
    assert distilled.image == "ubuntu-24.04 (20261026.1.1)"
    assert distilled.commands == ["npm publish"]
    assert distilled.failure_reason == "##[error]Process completed with exit code 1."
    failing = distilled.failing_section
    assert failing is not None and failing.name == "Run npm publish"
    assert failing.duration_s == 10.0


def test_an_action_failing_without_an_exit_code_line_still_has_a_verdict() -> None:
    distilled = distill(
        _github_log(
            "##[error]Input required and not supplied: token", header="Run actions/upload@v4"
        )
    )
    assert distilled.failure_reason == "##[error]Input required and not supplied: token"
    assert _top(_github_log("##[error]Input required and not supplied: token")) == (
        "actions.input-missing"
    )


def test_annotation_prefixes_do_not_hide_anchored_patterns() -> None:
    hits = match_rules(distill(_github_log("##[error]The operation was canceled.")))
    assert hits[0].rule.id == "actions.canceled"
    assert hits[0].line_text == "##[error]The operation was canceled.", "reported as printed"


def test_a_timeout_outranks_the_cancel_it_also_prints() -> None:
    raw = _github_log(
        "##[error]The job running on runner GitHub Actions 7 has exceeded the maximum "
        "execution time of 10 minutes.",
        "##[error]The operation was canceled.",
    )
    assert _top(raw) == "actions.timeout"


def test_a_bare_403_on_github_is_not_gitlabs_token() -> None:
    raw = _github_log("curl: (22) The requested URL returned error: 403 Forbidden")
    assert "gitlab.token-denied" not in {hit.rule.id for hit in match_rules(distill(raw))}
    # The same text in a log of unknown origin still gets every rule.
    assert "gitlab.token-denied" in {hit.rule.id for hit in match_rules(distill("403 Forbidden\n"))}


def test_github_only_rules_stay_quiet_on_gitlab() -> None:
    raw = (
        "Running with gitlab-runner 17.0.0\n"
        "$ ./notify.sh\nThe operation was canceled.\nERROR: Job failed: exit code 1\n"
    )
    assert "actions.canceled" not in {hit.rule.id for hit in match_rules(distill(raw))}


def test_the_token_permission_rule() -> None:
    raw = _github_log(
        "RequestError [HttpError]: Resource not accessible by integration",
        "##[error]Process completed with exit code 1.",
    )
    assert _top(raw) == "github.token-permissions"

"""Credential and instance resolution, including the in-CI case."""

from __future__ import annotations

from pipelinemd.config import (
    DEFAULT_GITLAB_URL,
    detect_ci,
    resolve_anthropic_key,
    resolve_credentials,
    resolve_gitlab_url,
)


def test_explicit_token_wins() -> None:
    creds = resolve_credentials("abc", env={"GITLAB_TOKEN": "zzz"})
    assert (creds.token, creds.header, creds.source) == ("abc", "PRIVATE-TOKEN", "--token")


def test_job_token_uses_its_own_header() -> None:
    """A CI job token is not a private token; sending it as one gets a 401."""
    creds = resolve_credentials(env={"CI_JOB_TOKEN": "job"})
    assert (creds.token, creds.header) == ("job", "JOB-TOKEN")


def test_personal_token_beats_job_token() -> None:
    creds = resolve_credentials(env={"GITLAB_TOKEN": "pat", "CI_JOB_TOKEN": "job"})
    assert creds.token == "pat"


def test_no_token_is_not_an_error() -> None:
    creds = resolve_credentials(env={})
    assert not creds.present
    assert creds.source == "none"


def test_gitlab_url_resolution_order() -> None:
    assert resolve_gitlab_url("https://x.io/", env={}) == "https://x.io"
    assert resolve_gitlab_url(env={"CI_SERVER_URL": "https://y.io/"}) == "https://y.io"
    assert (
        resolve_gitlab_url(
            env={"PIPELINEMD_GITLAB_URL": "https://z.io", "CI_SERVER_URL": "https://y.io"}
        )
        == "https://z.io"
    )
    assert resolve_gitlab_url(env={}) == DEFAULT_GITLAB_URL


def test_detect_ci_reads_the_surrounding_pipeline() -> None:
    context = detect_ci(
        env={
            "GITLAB_CI": "true",
            "CI_PROJECT_PATH": "acme/web",
            "CI_SERVER_URL": "https://gitlab.com",
            "CI_PIPELINE_ID": "12345",
            "CI_JOB_ID": "98765",
            "CI_COMMIT_REF_NAME": "main",
        }
    )
    assert context is not None
    assert (context.project_path, context.pipeline_id, context.job_id) == ("acme/web", 12345, 98765)


def test_detect_ci_is_none_outside_ci() -> None:
    assert detect_ci(env={}) is None
    assert detect_ci(env={"CI_PROJECT_PATH": "a/b"}) is None, "GITLAB_CI must be set"


def test_detect_ci_tolerates_non_numeric_ids() -> None:
    context = detect_ci(
        env={"GITLAB_CI": "true", "CI_PROJECT_PATH": "a/b", "CI_PIPELINE_ID": "oops"}
    )
    assert context is not None and context.pipeline_id is None


def test_anthropic_key_resolution() -> None:
    assert resolve_anthropic_key("k", env={}) == "k"
    assert resolve_anthropic_key(env={"ANTHROPIC_API_KEY": "e"}) == "e"
    assert resolve_anthropic_key(env={}) is None

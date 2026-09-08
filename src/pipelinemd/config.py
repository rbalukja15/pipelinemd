"""Where credentials and instance URLs come from.

Resolution is explicit-flag first, then environment, then the GitLab CI
variables that exist when pipelinemd runs *inside* a pipeline. That last case
is the interesting one: a job can diagnose its own pipeline with no
configuration at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Checked in order. The header differs: a CI job token is not a private token,
# and sending it as one gets a 401.
TOKEN_SOURCES: tuple[tuple[str, str], ...] = (
    ("PIPELINEMD_TOKEN", "PRIVATE-TOKEN"),
    ("GITLAB_TOKEN", "PRIVATE-TOKEN"),
    ("GITLAB_PRIVATE_TOKEN", "PRIVATE-TOKEN"),
    ("CI_JOB_TOKEN", "JOB-TOKEN"),
)

DEFAULT_GITLAB_URL = "https://gitlab.com"


@dataclass(frozen=True, slots=True)
class Credentials:
    token: str | None
    header: str
    source: str

    @property
    def present(self) -> bool:
        return bool(self.token)


def resolve_credentials(
    explicit: str | None = None, env: dict[str, str] | None = None
) -> Credentials:
    """Find a GitLab token, saying where it came from."""
    if explicit:
        return Credentials(token=explicit, header="PRIVATE-TOKEN", source="--token")
    environ = env if env is not None else os.environ
    for name, header in TOKEN_SOURCES:
        value = environ.get(name)
        if value:
            return Credentials(token=value, header=header, source=name)
    return Credentials(token=None, header="PRIVATE-TOKEN", source="none")


def resolve_gitlab_url(explicit: str | None = None, env: dict[str, str] | None = None) -> str:
    if explicit:
        return explicit.rstrip("/")
    environ = env if env is not None else os.environ
    for name in ("PIPELINEMD_GITLAB_URL", "CI_SERVER_URL"):
        if value := environ.get(name):
            return value.rstrip("/")
    return DEFAULT_GITLAB_URL


@dataclass(frozen=True, slots=True)
class CIContext:
    """What GitLab tells a job about itself."""

    server_url: str
    project_path: str
    pipeline_id: int | None
    job_id: int | None
    ref: str | None


def detect_ci(env: dict[str, str] | None = None) -> CIContext | None:
    """Return the surrounding pipeline's identity when running inside GitLab CI."""
    environ = env if env is not None else os.environ
    project = environ.get("CI_PROJECT_PATH")
    if not project or environ.get("GITLAB_CI") != "true":
        return None

    def _int(name: str) -> int | None:
        raw = environ.get(name)
        if raw is None or not raw.isdigit():
            return None
        return int(raw)

    return CIContext(
        server_url=(environ.get("CI_SERVER_URL") or DEFAULT_GITLAB_URL).rstrip("/"),
        project_path=project,
        pipeline_id=_int("CI_PIPELINE_ID"),
        job_id=_int("CI_JOB_ID"),
        ref=environ.get("CI_COMMIT_REF_NAME"),
    )


def resolve_anthropic_key(
    explicit: str | None = None, env: dict[str, str] | None = None
) -> str | None:
    if explicit:
        return explicit
    environ = env if env is not None else os.environ
    return environ.get("ANTHROPIC_API_KEY") or None

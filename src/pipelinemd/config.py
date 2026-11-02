"""Where credentials and instance URLs come from.

Resolution is explicit-flag first, then environment, then the CI variables
that exist when pipelinemd runs *inside* a pipeline: GitLab CI's, or GitHub
Actions'. That last case is the interesting one: a job can diagnose its own
pipeline with no configuration at all.

The access model these follow is docs/adr/0002-gitlab-access.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

# Checked in order. The header differs: a CI job token is not a private token,
# and sending it as one gets a 401.
TOKEN_SOURCES: tuple[tuple[str, str], ...] = (
    ("PIPELINEMD_TOKEN", "PRIVATE-TOKEN"),
    ("GITLAB_TOKEN", "PRIVATE-TOKEN"),
    ("GITLAB_PRIVATE_TOKEN", "PRIVATE-TOKEN"),
    ("CI_JOB_TOKEN", "JOB-TOKEN"),
)

DEFAULT_GITLAB_URL = "https://gitlab.com"
DEFAULT_GITHUB_URL = "https://github.com"

#: GitHub's own token variables. PIPELINEMD_TOKEN is left out on purpose: it
#: is the GitLab token's name too, and must not be sent to github.com.
GITHUB_TOKEN_SOURCES: tuple[str, ...] = ("GH_TOKEN", "GITHUB_TOKEN")


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


def same_instance(first: str, second: str) -> bool:
    """Whether two URLs name the same GitLab server: scheme, host and port.

    The path is ignored, so an instance served from a subdirectory matches
    URLs under it.
    """
    a = urlsplit(first if "://" in first else f"https://{first}")
    b = urlsplit(second if "://" in second else f"https://{second}")
    defaults = {"http": 80, "https": 443}
    try:
        port_a = a.port or defaults.get(a.scheme)
        port_b = b.port or defaults.get(b.scheme)
    except ValueError:
        return False
    return a.scheme == b.scheme and (a.hostname or "") == (b.hostname or "") and port_a == port_b


def scope_credentials(credentials: Credentials, target_url: str, instance_url: str) -> Credentials:
    """Withhold a token from the environment when the target is another server.

    A token in the environment was issued by one instance: the one
    ``resolve_gitlab_url`` names. Sending it to whatever host a pasted URL
    points at would hand it to that host. A token passed with ``--token`` is
    the user naming both the token and the target in one command, so it goes
    where they asked.
    """
    if not credentials.present or credentials.source == "--token":
        return credentials
    if same_instance(target_url, instance_url):
        return credentials
    return Credentials(token=None, header="PRIVATE-TOKEN", source="none")


def resolve_github_credentials(
    explicit: str | None = None, env: dict[str, str] | None = None
) -> Credentials:
    """Find a GitHub token, saying where it came from."""
    if explicit:
        return Credentials(token=explicit, header="Authorization", source="--token")
    environ = env if env is not None else os.environ
    for name in GITHUB_TOKEN_SOURCES:
        if value := environ.get(name):
            return Credentials(token=value, header="Authorization", source=name)
    return Credentials(token=None, header="Authorization", source="none")


def resolve_github_url(explicit: str | None = None, env: dict[str, str] | None = None) -> str:
    """The GitHub instance a token from the environment belongs to (its web URL)."""
    if explicit:
        return explicit.rstrip("/")
    environ = env if env is not None else os.environ
    for name in ("PIPELINEMD_GITHUB_URL", "GITHUB_SERVER_URL"):
        if value := environ.get(name):
            return value.rstrip("/")
    return DEFAULT_GITHUB_URL


@dataclass(frozen=True, slots=True)
class CIContext:
    """What the CI tells a job about itself. GitHub's run is `pipeline_id`."""

    server_url: str
    project_path: str
    pipeline_id: int | None
    job_id: int | None
    ref: str | None
    provider: str = "gitlab"
    #: GitHub only: GITHUB_API_URL, and the attempt this run is on.
    api_url: str | None = None
    run_attempt: int | None = None


def _int(environ: dict[str, str] | os._Environ[str], name: str) -> int | None:
    raw = environ.get(name)
    if raw is None or not raw.isdigit():
        return None
    return int(raw)


def detect_ci(env: dict[str, str] | None = None) -> CIContext | None:
    """The surrounding pipeline's identity inside GitLab CI or GitHub Actions."""
    environ = env if env is not None else os.environ
    if environ.get("GITHUB_ACTIONS") == "true" and (repository := environ.get("GITHUB_REPOSITORY")):
        return CIContext(
            server_url=(environ.get("GITHUB_SERVER_URL") or DEFAULT_GITHUB_URL).rstrip("/"),
            project_path=repository,
            pipeline_id=_int(environ, "GITHUB_RUN_ID"),
            job_id=None,
            ref=environ.get("GITHUB_REF_NAME"),
            provider="github",
            api_url=(environ.get("GITHUB_API_URL") or "").rstrip("/") or None,
            run_attempt=_int(environ, "GITHUB_RUN_ATTEMPT"),
        )
    project = environ.get("CI_PROJECT_PATH")
    if not project or environ.get("GITLAB_CI") != "true":
        return None

    return CIContext(
        server_url=(environ.get("CI_SERVER_URL") or DEFAULT_GITLAB_URL).rstrip("/"),
        project_path=project,
        pipeline_id=_int(environ, "CI_PIPELINE_ID"),
        job_id=_int(environ, "CI_JOB_ID"),
        ref=environ.get("CI_COMMIT_REF_NAME"),
    )


def resolve_anthropic_key(
    explicit: str | None = None, env: dict[str, str] | None = None
) -> str | None:
    if explicit:
        return explicit
    environ = env if env is not None else os.environ
    return environ.get("ANTHROPIC_API_KEY") or None

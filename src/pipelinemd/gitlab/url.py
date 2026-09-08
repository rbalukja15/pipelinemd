"""Parse the GitLab URLs a user is most likely to paste.

Everything in a GitLab project URL after ``/-/`` is the route; everything
between the host and ``/-/`` is the project path, however many subgroups deep
it happens to be. That single fact does most of the work here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..errors import UsageError

_ROUTE = re.compile(
    r"^(?P<kind>pipelines|jobs|builds)/(?P<id>\d+)"
    r"(?:/(?:builds|jobs)/(?P<nested_id>\d+))?"
)


@dataclass(frozen=True, slots=True)
class Target:
    """A pipeline or job identified well enough to fetch."""

    base_url: str
    project: str
    kind: str  # "pipeline" | "job"
    id: int

    @property
    def is_job(self) -> bool:
        return self.kind == "job"

    def web_url(self) -> str:
        route = "jobs" if self.is_job else "pipelines"
        return f"{self.base_url}/{self.project}/-/{route}/{self.id}"


def parse_target(url: str) -> Target:
    """Turn a pasted GitLab URL into a fetchable target.

    Understands pipeline URLs, job URLs, the legacy
    ``/-/pipelines/<id>/builds/<job>`` form, and self-hosted instances served
    from a subdirectory.
    """
    text = url.strip()
    if not text:
        raise UsageError("No target given.")
    if "://" not in text:
        text = f"https://{text}"

    parts = urlsplit(text)
    if not parts.netloc:
        raise UsageError(f"Not a URL: {url!r}")
    if "/-/" not in parts.path:
        raise UsageError(
            f"{url!r} is not a GitLab pipeline or job URL "
            "(expected something like https://gitlab.com/group/project/-/jobs/123)."
        )

    project_part, _, route_part = parts.path.partition("/-/")
    project = project_part.strip("/")
    if not project:
        raise UsageError(f"Could not find a project path in {url!r}.")

    match = _ROUTE.match(route_part.strip("/"))
    if not match:
        raise UsageError(
            f"Could not find a pipeline or job id in {url!r}. "
            "Expected .../-/pipelines/<id> or .../-/jobs/<id>."
        )

    # /-/pipelines/<pipeline>/builds/<job> points at the job, not the pipeline.
    if nested := match.group("nested_id"):
        kind, identifier = "job", int(nested)
    else:
        raw_kind = match.group("kind")
        kind = "pipeline" if raw_kind == "pipelines" else "job"
        identifier = int(match.group("id"))

    base_url = f"{parts.scheme}://{parts.netloc}"
    return Target(base_url=base_url, project=project, kind=kind, id=identifier)


def target_from_parts(
    base_url: str, project: str, *, pipeline: int | None = None, job: int | None = None
) -> Target:
    """Build a target from explicit flags rather than a URL."""
    if (pipeline is None) == (job is None):
        raise UsageError("Give exactly one of --pipeline or --job.")
    kind = "pipeline" if pipeline is not None else "job"
    identifier = pipeline if pipeline is not None else job
    assert identifier is not None
    return Target(
        base_url=base_url.rstrip("/"),
        project=project.strip("/"),
        kind=kind,
        id=identifier,
    )


def rebase(target: Target, base_url: str) -> Target:
    """Re-split a target against an explicitly given instance URL.

    A GitLab served from a subdirectory (``https://host/gitlab``) is
    indistinguishable from a top-level group of the same name when all you
    have is the URL. Passing the real instance URL resolves it: any matching
    prefix is moved out of the project path and into the base.
    """
    cleaned = base_url.rstrip("/")
    parts = urlsplit(cleaned if "://" in cleaned else f"https://{cleaned}")
    prefix = parts.path.strip("/")
    project = target.project
    if prefix and project.startswith(f"{prefix}/"):
        project = project[len(prefix) + 1 :]
    return Target(
        base_url=f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}",
        project=project,
        kind=target.kind,
        id=target.id,
    )

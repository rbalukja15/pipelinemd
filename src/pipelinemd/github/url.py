"""Parse the GitHub Actions URLs a user is most likely to paste.

A run lives at ``/<owner>/<repo>/actions/runs/<id>``, optionally pinned to one
attempt with ``/attempts/<n>``, and a job at ``…/runs/<id>/job/<job_id>``. The
host is github.com or a GitHub Enterprise Server, whose API sits under
``/api/v3`` on the same host.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..errors import UsageError

GITHUB_COM = "https://github.com"
GITHUB_COM_API = "https://api.github.com"

_ROUTE = re.compile(
    r"^/(?P<owner>[^/]+)/(?P<repo>[^/]+)/actions/runs/(?P<run>\d+)"
    r"(?:/attempts/(?P<attempt>\d+))?(?:/job/(?P<job>\d+))?/?$"
)


@dataclass(frozen=True, slots=True)
class GitHubTarget:
    """A workflow run or job identified well enough to fetch."""

    web_url: str
    owner: str
    repo: str
    kind: str  # "run" | "job"
    id: int
    #: The run attempt a run URL was pinned to; None means the latest.
    attempt: int | None = None
    #: Explicit API root, e.g. GITHUB_API_URL inside Actions. Else derived.
    api: str | None = None

    @property
    def is_job(self) -> bool:
        return self.kind == "job"

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def api_url(self) -> str:
        return self.api or api_url_for(self.web_url)


def api_url_for(web_url: str) -> str:
    """github.com's API has its own host; a GitHub Enterprise Server's is /api/v3."""
    base = web_url.rstrip("/")
    parts = urlsplit(base)
    if (parts.hostname or "").lower() in ("github.com", "www.github.com"):
        return GITHUB_COM_API
    return f"{base}/api/v3"


def looks_like_github(url: str) -> bool:
    """Whether a pasted URL is a GitHub Actions run or job rather than GitLab."""
    text = url.strip()
    parts = urlsplit(text if "://" in text else f"https://{text}")
    return "/-/" not in parts.path and bool(_ROUTE.match(parts.path))


def parse_github_target(url: str, github_url: str | None = None) -> GitHubTarget:
    """Turn a pasted run or job URL into a fetchable target.

    ``github_url`` is the instance's web URL from ``--github-url``, for a
    GitHub Enterprise Server served under a path; it replaces the host part.
    """
    text = url.strip()
    if not text:
        raise UsageError("No target given.")
    if "://" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    path = parts.path
    web_url = f"{parts.scheme}://{parts.netloc}"
    if github_url:
        base = urlsplit(github_url.rstrip("/"))
        prefix = base.path.rstrip("/")
        if prefix and path.startswith(f"{prefix}/"):
            path = path[len(prefix) :]
        web_url = f"{base.scheme}://{base.netloc}{prefix}"
    match = _ROUTE.match(path)
    if not match:
        raise UsageError(
            f"{url!r} is not a GitHub Actions run or job URL (expected something like "
            "https://github.com/owner/repo/actions/runs/123 or …/runs/123/job/456)."
        )
    attempt = int(match.group("attempt")) if match.group("attempt") else None
    if job := match.group("job"):
        # A job id names one attempt's job already.
        return GitHubTarget(
            web_url=web_url,
            owner=match.group("owner"),
            repo=match.group("repo"),
            kind="job",
            id=int(job),
        )
    return GitHubTarget(
        web_url=web_url,
        owner=match.group("owner"),
        repo=match.group("repo"),
        kind="run",
        id=int(match.group("run")),
        attempt=attempt,
    )


def github_target_from_parts(
    web_url: str,
    repository: str,
    *,
    run: int | None = None,
    job: int | None = None,
    attempt: int | None = None,
    api: str | None = None,
) -> GitHubTarget:
    """Build a target from --repo with --run or --job, or from the Actions context."""
    if (run is None) == (job is None):
        raise UsageError("Give exactly one of --run or --job with --repo.")
    owner, _, repo = repository.strip("/").partition("/")
    if not owner or not repo or "/" in repo:
        raise UsageError(f"--repo takes owner/name, not {repository!r}.")
    kind = "run" if run is not None else "job"
    identifier = run if run is not None else job
    assert identifier is not None
    return GitHubTarget(
        web_url=web_url.rstrip("/"),
        owner=owner,
        repo=repo,
        kind=kind,
        id=identifier,
        attempt=attempt if kind == "run" else None,
        api=api,
    )

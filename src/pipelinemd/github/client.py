"""GitHub Actions REST calls pipelinemd needs, and nothing else.

The method names follow `gitlab.client.GitLabClient` (`failed_jobs`,
`get_job`, `get_trace`, `job_ref`) so the CLI's two paths read alike.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..errors import GitHubError
from ..gitlab.http import DEFAULT_RETRIES, DEFAULT_TIMEOUT
from ..models import JobRef, RetryHistory
from .http import RAW, GitHubHttp

#: Conclusions that mean "this job produced a failure worth explaining".
FAILED_CONCLUSIONS = frozenset({"failure", "timed_out"})
#: A cancelled job is usually fallout: a matrix sibling failed with fail-fast,
#: or a newer run cancelled this one. Offered only when nothing else failed.
CANCELLED = "cancelled"

#: GitHub's conclusions in the words `RetryHistory` reads, which are GitLab's.
#: GitLab records a timeout as `failed`, so `timed_out` maps there too.
_RETRY_STATUS = {
    "success": "success",
    "failure": "failed",
    "timed_out": "failed",
    "cancelled": "canceled",
    "skipped": "skipped",
}


def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        began = datetime.fromisoformat(start.replace("Z", "+00:00"))
        ended = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (ended - began).total_seconds())


def failed_step(job: dict[str, Any]) -> str | None:
    """The name of the step that failed, as the job's page shows it."""
    for step in job.get("steps") or []:
        if step.get("conclusion") in FAILED_CONCLUSIONS:
            return str(step.get("name") or "") or None
    return None


class GitHubClient:
    """Thin, typed wrapper over the handful of Actions endpoints we use."""

    def __init__(
        self,
        api_url: str,
        token: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        notify: Callable[[str], None] | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.http = GitHubHttp(self.api_url, token, timeout=timeout, retries=retries, notify=notify)

    @staticmethod
    def _repo(repository: str) -> str:
        owner, _, name = repository.strip("/").partition("/")
        return f"repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}"

    # -- reads -------------------------------------------------------------

    def get_run(self, repository: str, run_id: int) -> dict[str, Any]:
        return dict(self.http.get_json(f"{self._repo(repository)}/actions/runs/{run_id}"))

    def get_job(self, repository: str, job_id: int) -> dict[str, Any]:
        return dict(self.http.get_json(f"{self._repo(repository)}/actions/jobs/{job_id}"))

    def list_run_jobs(
        self,
        repository: str,
        run_id: int,
        *,
        attempt: int | None = None,
        all_attempts: bool = False,
    ) -> list[dict[str, Any]]:
        """A run's jobs: its latest attempt, one given attempt, or every attempt."""
        base = f"{self._repo(repository)}/actions/runs/{run_id}"
        if attempt is not None:
            return list(self.http.paginate(f"{base}/attempts/{attempt}/jobs", "jobs"))
        return list(
            self.http.paginate(
                f"{base}/jobs", "jobs", {"filter": "all" if all_attempts else "latest"}
            )
        )

    def failed_jobs(
        self, repository: str, run_id: int, *, attempt: int | None = None
    ) -> list[dict[str, Any]]:
        """Failed and timed-out jobs in run order; cancelled ones only if none failed."""
        jobs = self.list_run_jobs(repository, run_id, attempt=attempt)
        failed = [job for job in jobs if job.get("conclusion") in FAILED_CONCLUSIONS]
        if not failed:
            failed = [job for job in jobs if job.get("conclusion") == CANCELLED]
        failed.sort(key=lambda job: job.get("id") or 0)
        return failed

    def get_trace(self, repository: str, job_id: int) -> str:
        """The job's log. GitHub redirects to a signed URL; the token stays behind."""
        return self.http.get_text(f"{self._repo(repository)}/actions/jobs/{job_id}/logs")

    def get_workflow_file(self, repository: str, path: str, ref: str) -> str | None:
        """The workflow file a run came from, at its commit. Best effort."""
        clean = path.partition("@")[0]
        if not clean or clean.startswith("dynamic/"):
            return None
        quoted = urllib.parse.quote(clean)
        try:
            return self.http.get_text(
                f"{self._repo(repository)}/contents/{quoted}", {"ref": ref}, accept=RAW
            )
        except GitHubError:
            return None

    # -- mapping -----------------------------------------------------------

    def job_ref(self, job: dict[str, Any], repository: str) -> JobRef:
        """Map a GitHub job payload onto our own JobRef. The failed step is the stage."""
        return JobRef(
            name=str(job.get("name") or "unknown"),
            id=int(job["id"]) if job.get("id") is not None else None,
            stage=failed_step(job),
            status=job.get("conclusion") or job.get("status"),
            url=job.get("html_url"),
            project=repository,
            pipeline_id=job.get("run_id"),
            ref=job.get("head_branch"),
            sha=job.get("head_sha"),
            duration_s=_seconds(job.get("started_at"), job.get("completed_at")),
            source="github",
        )


def retry_history(job: dict[str, Any], every_attempt: list[dict[str, Any]]) -> RetryHistory | None:
    """This job's attempts across the run's re-runs, oldest first.

    Every attempt of a run builds the same commit, so a job that failed once
    and passed on a re-run is flaky by the same evidence GitLab's retries give.
    """
    # Attempts are paired by job name because a job's id changes on every
    # re-run and GitHub links attempts by nothing else. Matrix jobs are safe:
    # their names carry the matrix values. Two jobs that share a name (calls
    # of one reusable workflow, say) would have their histories merged.
    attempts = sorted(
        (other for other in every_attempt if other.get("name") == job.get("name")),
        key=lambda other: (other.get("run_attempt") or 0, other.get("id") or 0),
    )
    if not attempts:
        return None
    return RetryHistory(
        statuses=tuple(
            _RETRY_STATUS.get(str(other.get("conclusion")), str(other.get("status") or ""))
            for other in attempts
        )
    )

"""GitLab REST calls pipelinemd needs, and nothing else."""

from __future__ import annotations

import urllib.parse
from typing import Any

from ..models import JobRef
from .http import DEFAULT_RETRIES, DEFAULT_TIMEOUT, HttpClient

# Statuses that mean "this job produced a failure worth explaining".
FAILED_STATUSES = frozenset({"failed"})


class GitLabClient:
    """Thin, typed wrapper over the handful of endpoints we use."""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        token_header: str = "PRIVATE-TOKEN",
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = HttpClient(
            base_url=self.base_url,
            token=token,
            token_header=token_header,
            timeout=timeout,
            retries=retries,
        )

    @staticmethod
    def _encode(project: str) -> str:
        """GitLab wants the project path URL-encoded, slashes included."""
        return urllib.parse.quote(project.strip("/"), safe="")

    # -- reads -------------------------------------------------------------

    def get_pipeline(self, project: str, pipeline_id: int) -> dict[str, Any]:
        result = self.http.get_json(f"projects/{self._encode(project)}/pipelines/{pipeline_id}")
        return dict(result)

    def get_job(self, project: str, job_id: int) -> dict[str, Any]:
        result = self.http.get_json(f"projects/{self._encode(project)}/jobs/{job_id}")
        return dict(result)

    def list_pipeline_jobs(
        self, project: str, pipeline_id: int, *, include_retried: bool = False
    ) -> list[dict[str, Any]]:
        return list(
            self.http.paginate(
                f"projects/{self._encode(project)}/pipelines/{pipeline_id}/jobs",
                {"include_retried": "true" if include_retried else None},
            )
        )

    def failed_jobs(self, project: str, pipeline_id: int) -> list[dict[str, Any]]:
        """Failed jobs in pipeline order, jobs allowed to fail listed last.

        A job with ``allow_failure`` did not break the pipeline, so it is
        rarely the one a user is asking about - but it is still worth
        offering when nothing else failed.
        """
        jobs = self.list_pipeline_jobs(project, pipeline_id)
        failed = [job for job in jobs if job.get("status") in FAILED_STATUSES]
        failed.sort(key=lambda job: (bool(job.get("allow_failure")), job.get("id") or 0))
        return failed

    def get_trace(self, project: str, job_id: int) -> str:
        """The job's raw log, exactly as the runner uploaded it."""
        return self.http.get_text(f"projects/{self._encode(project)}/jobs/{job_id}/trace")

    def get_ci_config(self, project: str, ref: str, path: str = ".gitlab-ci.yml") -> str | None:
        """The pipeline definition at a ref, if it is readable. Best effort."""
        from ..errors import GitLabError

        encoded_path = urllib.parse.quote(path, safe="")
        try:
            return self.http.get_text(
                f"projects/{self._encode(project)}/repository/files/{encoded_path}/raw",
                {"ref": ref},
            )
        except GitLabError:
            return None

    # -- mapping -----------------------------------------------------------

    def job_ref(self, job: dict[str, Any], project: str) -> JobRef:
        """Map a GitLab job payload onto our own JobRef."""
        pipeline = job.get("pipeline") or {}
        commit = job.get("commit") or {}
        return JobRef(
            name=str(job.get("name") or "unknown"),
            id=int(job["id"]) if job.get("id") is not None else None,
            stage=job.get("stage"),
            status=job.get("status"),
            url=job.get("web_url"),
            project=project,
            pipeline_id=pipeline.get("id"),
            ref=job.get("ref") or pipeline.get("ref"),
            sha=(job.get("sha") or commit.get("id") or pipeline.get("sha")),
            duration_s=job.get("duration"),
            allow_failure=bool(job.get("allow_failure")),
            source="gitlab",
        )

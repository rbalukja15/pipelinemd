"""The only code in pipelinemd that writes to GitLab.

ADR-0002 keeps writes apart from reads: their own client, their own token with
the `api` scope, and nothing here is reachable unless `diagnose --open-mr`
asked for it. The read client (`gitlab.http`) only ever sends GET.

Writes are not retried. Creating a commit or a merge request twice is worse
than failing once and saying so, and GitLab gives no idempotency key to make a
retry safe.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from ..errors import GitLabError, NotFoundError
from .http import DEFAULT_TIMEOUT, HttpClient


class WriteClient:
    """The handful of calls the merge-request path makes, reads included.

    The reads it needs (the project, its labels, a branch, a file) go through
    this client too, so the write token is the only credential the path uses
    and it is never mixed with the read token.
    """

    def __init__(self, base_url: str, token: str, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = HttpClient(base_url=self.base_url, token=token, timeout=timeout, retries=0)

    @staticmethod
    def _encode(value: str) -> str:
        return urllib.parse.quote(value.strip("/"), safe="")

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        http = self.http
        url = http._url(path)
        headers = dict(http._headers(), **{"Content-Type": "application/json"})
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        status, body, _headers = http._send(request)
        if status >= 400:
            http._raise_for_status(status, body, url)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise GitLabError(f"Expected JSON from {url}, got: {body[:200]!r}") from exc

    # -- reads -------------------------------------------------------------

    def project(self, project: str) -> dict[str, Any]:
        return dict(self.http.get_json(f"projects/{self._encode(project)}"))

    def has_label(self, project: str, name: str) -> bool:
        """An exact, case-sensitive match: `search` alone also finds substrings."""
        labels = self.http.get_json(
            f"projects/{self._encode(project)}/labels",
            {"search": name, "include_ancestor_groups": "true", "per_page": 100},
        )
        return any(isinstance(label, dict) and label.get("name") == name for label in labels)

    def branch_exists(self, project: str, branch: str) -> bool:
        try:
            self.http.get_json(
                f"projects/{self._encode(project)}/repository/branches/{self._encode(branch)}"
            )
        except NotFoundError:
            return False
        return True

    def file(self, project: str, path: str, ref: str) -> str:
        return self.http.get_text(
            f"projects/{self._encode(project)}/repository/files/{self._encode(path)}/raw",
            {"ref": ref},
        )

    # -- validation ----------------------------------------------------------

    def lint(self, project: str, content: str, ref: str) -> dict[str, Any]:
        """GitLab's own verdict on a pipeline definition, in the project's context.

        In the project's context, so `include: local` and project variables
        resolve as they would in a real pipeline on that branch.
        """
        result = self._post(
            f"projects/{self._encode(project)}/ci/lint",
            {"content": content, "ref": ref, "dry_run": False},
        )
        return dict(result)

    # -- writes ------------------------------------------------------------

    def create_commit(
        self,
        project: str,
        *,
        branch: str,
        start_sha: str,
        path: str,
        content: str,
        message: str,
    ) -> dict[str, Any]:
        """One commit on a new branch, started from the failing commit."""
        result = self._post(
            f"projects/{self._encode(project)}/repository/commits",
            {
                "branch": branch,
                "start_sha": start_sha,
                "commit_message": message,
                "actions": [{"action": "update", "file_path": path, "content": content}],
            },
        )
        return dict(result)

    def create_merge_request(
        self,
        project: str,
        *,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
    ) -> dict[str, Any]:
        result = self._post(
            f"projects/{self._encode(project)}/merge_requests",
            {
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
                "description": description,
                "remove_source_branch": True,
            },
        )
        return dict(result)

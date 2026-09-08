"""URL parsing, the HTTP layer's retry/pagination behaviour, and the client."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pipelinemd.errors import AuthError, GitLabError, NotFoundError, UsageError
from pipelinemd.gitlab import GitLabClient, parse_target, rebase, target_from_parts
from pipelinemd.gitlab.http import HttpClient

# -- URL parsing ------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "kind", "identifier", "project"),
    [
        ("https://gitlab.com/acme/web/-/pipelines/12345", "pipeline", 12345, "acme/web"),
        ("https://gitlab.com/acme/web/-/jobs/98765", "job", 98765, "acme/web"),
        ("https://gitlab.com/a/b/c/d/-/jobs/1", "job", 1, "a/b/c/d"),
        ("gitlab.com/acme/web/-/jobs/9", "job", 9, "acme/web"),
        ("https://gitlab.com/acme/web/-/jobs/9/", "job", 9, "acme/web"),
        ("https://gitlab.com/acme/web/-/pipelines/1/builds/777", "job", 777, "acme/web"),
    ],
)
def test_parse_target(url: str, kind: str, identifier: int, project: str) -> None:
    target = parse_target(url)
    assert (target.kind, target.id, target.project) == (kind, identifier, project)


@pytest.mark.parametrize(
    "url", ["", "   ", "https://gitlab.com/acme/web", "nonsense", "https://gitlab.com/-/jobs/1"]
)
def test_bad_urls_are_rejected(url: str) -> None:
    with pytest.raises(UsageError):
        parse_target(url)


def test_web_url_round_trips() -> None:
    url = "https://gitlab.com/acme/web/-/jobs/98765"
    assert parse_target(url).web_url() == url


def test_rebase_moves_a_subdirectory_prefix_out_of_the_project() -> None:
    """A GitLab at https://host/gitlab looks like a group called 'gitlab'."""
    target = parse_target("https://git.example.com/gitlab/infra/deploy/-/jobs/42")
    assert target.project == "gitlab/infra/deploy"

    fixed = rebase(target, "https://git.example.com/gitlab")
    assert fixed.project == "infra/deploy"
    assert fixed.base_url == "https://git.example.com/gitlab"


def test_rebase_without_a_prefix_is_harmless() -> None:
    target = parse_target("https://gitlab.com/acme/web/-/jobs/1")
    assert rebase(target, "https://gitlab.com").project == "acme/web"


def test_target_from_parts_requires_exactly_one_id() -> None:
    assert target_from_parts("https://gitlab.com", "a/b", pipeline=1).kind == "pipeline"
    assert target_from_parts("https://gitlab.com", "a/b", job=2).kind == "job"
    with pytest.raises(UsageError):
        target_from_parts("https://gitlab.com", "a/b")
    with pytest.raises(UsageError):
        target_from_parts("https://gitlab.com", "a/b", pipeline=1, job=2)


# -- HTTP -------------------------------------------------------------------


class _Response:
    def __init__(self, body: str, status: int = 200, headers: dict[str, str] | None = None):
        self._body = body.encode()
        self.status = status
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _Opener:
    """Replays a scripted list of responses or exceptions."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[str] = []

    def open(self, request: Any, timeout: float | None = None) -> Any:
        self.calls.append(request.full_url)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(script: list[Any], monkeypatch: pytest.MonkeyPatch) -> HttpClient:
    import time

    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    client = HttpClient(base_url="https://gitlab.example.com", token="t", backoff=0.0)
    client._opener = _Opener(script)  # type: ignore[assignment]
    return client


def test_get_json_sends_the_token_header(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client([_Response('{"id": 1}')], monkeypatch)
    assert client.get_json("projects/1") == {"id": 1}


def test_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    flaky = urllib.error.HTTPError("u", 503, "busy", {}, None)  # type: ignore[arg-type]
    client = _client([flaky, _Response('{"ok": true}')], monkeypatch)
    assert client.get_json("projects/1") == {"ok": True}
    assert len(client._opener.calls) == 2  # type: ignore[attr-defined]


def test_gives_up_after_the_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    errors = [urllib.error.HTTPError("u", 500, "boom", {}, None) for _ in range(5)]  # type: ignore[arg-type]
    client = _client(errors, monkeypatch)
    with pytest.raises(GitLabError):
        client.get_json("projects/1")


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, AuthError), (403, AuthError), (404, NotFoundError), (400, GitLabError)],
)
def test_client_errors_map_to_typed_exceptions(
    status: int, expected: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.error

    error = urllib.error.HTTPError("u", status, "no", {}, None)  # type: ignore[arg-type]
    client = _client([error], monkeypatch)
    with pytest.raises(expected):
        client.get_json("projects/1")


def test_client_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    error = urllib.error.HTTPError("u", 404, "no", {}, None)  # type: ignore[arg-type]
    client = _client([error], monkeypatch)
    with pytest.raises(NotFoundError):
        client.get_json("projects/1")
    assert len(client._opener.calls) == 1  # type: ignore[attr-defined]


def test_pagination_follows_x_next_page(monkeypatch: pytest.MonkeyPatch) -> None:
    page1 = _Response(json.dumps([{"id": 1}, {"id": 2}]), headers={"X-Next-Page": "2"})
    page2 = _Response(json.dumps([{"id": 3}]), headers={"X-Next-Page": ""})
    client = _client([page1, page2], monkeypatch)
    assert [item["id"] for item in client.paginate("projects/1/jobs")] == [1, 2, 3]


def test_non_json_body_is_reported_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client([_Response("<html>nope</html>")], monkeypatch)
    with pytest.raises(GitLabError, match="Expected JSON"):
        client.get_json("projects/1")


# -- Client -----------------------------------------------------------------


class _StubHttp:
    def __init__(self, json_result: Any = None, text_result: str = "", items: Any = None):
        self.json_result = json_result
        self.text_result = text_result
        self.items = items or []
        self.paths: list[str] = []

    def get_json(self, path: str, params: Any = None) -> Any:
        self.paths.append(path)
        return self.json_result

    def get_text(self, path: str, params: Any = None) -> str:
        self.paths.append(path)
        return self.text_result

    def paginate(self, path: str, params: Any = None, **kwargs: Any) -> Any:
        self.paths.append(path)
        return iter(self.items)


def test_project_path_is_url_encoded() -> None:
    assert GitLabClient._encode("group/sub/project") == "group%2Fsub%2Fproject"


def test_get_trace_hits_the_right_endpoint() -> None:
    client = GitLabClient("https://gitlab.com")
    client.http = _StubHttp(text_result="log body")  # type: ignore[assignment]
    assert client.get_trace("acme/web", 99) == "log body"
    assert client.http.paths == ["projects/acme%2Fweb/jobs/99/trace"]  # type: ignore[attr-defined]


def test_failed_jobs_filters_and_orders() -> None:
    jobs = [
        {"id": 3, "status": "success", "name": "lint"},
        {"id": 4, "status": "failed", "name": "flaky", "allow_failure": True},
        {"id": 5, "status": "failed", "name": "build"},
        {"id": 6, "status": "running", "name": "deploy"},
    ]
    client = GitLabClient("https://gitlab.com")
    client.http = _StubHttp(items=jobs)  # type: ignore[assignment]
    result = client.failed_jobs("acme/web", 1)
    assert [job["name"] for job in result] == ["build", "flaky"], (
        "allow_failure jobs did not break the pipeline, so they rank last"
    )


def test_job_ref_maps_the_payload() -> None:
    payload = {
        "id": 98765,
        "name": "build",
        "stage": "test",
        "status": "failed",
        "web_url": "https://gitlab.com/acme/web/-/jobs/98765",
        "ref": "main",
        "duration": 47.5,
        "allow_failure": False,
        "pipeline": {"id": 12345},
        "commit": {"id": "3f2a1b9c"},
    }
    ref = GitLabClient("https://gitlab.com").job_ref(payload, "acme/web")
    assert ref.label == "build (test) #98765"
    assert (ref.pipeline_id, ref.sha, ref.duration_s) == (12345, "3f2a1b9c", 47.5)


def test_get_ci_config_swallows_a_missing_file() -> None:
    class _Failing(_StubHttp):
        def get_text(self, path: str, params: Any = None) -> str:
            raise NotFoundError("nope")

    client = GitLabClient("https://gitlab.com")
    client.http = _Failing()  # type: ignore[assignment]
    assert client.get_ci_config("acme/web", "main") is None

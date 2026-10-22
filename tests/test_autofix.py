"""`diagnose --open-mr`: every gate, through fakes. Nothing reaches GitLab or Claude."""

from __future__ import annotations

import io
import json
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from pipelinemd import cli
from pipelinemd.autofix import (
    AUTOFIX_LABEL,
    WRITE_TOKEN_ENV,
    AutofixOutcome,
    branch_name,
    changed_lines,
    open_fix_mr,
)
from pipelinemd.cost import cost_of
from pipelinemd.diagnose import diagnose
from pipelinemd.diagnose.patch import PatchProposal, propose_patch
from pipelinemd.distill import distill
from pipelinemd.errors import DiagnosisError, GitLabError, NotFoundError
from pipelinemd.gitlab.write import WriteClient
from pipelinemd.models import Diagnosis, DistilledLog, JobRef, Report, Usage
from pipelinemd.rules import match_rules

ROOT = Path(__file__).resolve().parents[1]
YAML_TRACE = ROOT / "corpus" / "traces" / "yaml-unknown-stage.log"
NPM_TRACE = ROOT / "tests" / "fixtures" / "traces" / "npm_eresolve.log"
MODEL = "claude-opus-5"
SHA = "a" * 40

CONFIG = """\
stages:
  - build
  - test

build:
  stage: build
  script: make

lint:
  stage: lint
  script: make lint
"""
FIXED = CONFIG.replace("  - test\n", "  - test\n  - lint\n")


# -- fakes -----------------------------------------------------------------


class _Usage:
    input_tokens = 1000
    output_tokens = 500


class _Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    def __init__(self, payload: dict[str, Any], stop_reason: str = "end_turn") -> None:
        self.content = [_Block(json.dumps(payload))]
        self.usage = _Usage()
        self.stop_reason = stop_reason


class _Anthropic:
    def __init__(self, payload: dict[str, Any], stop_reason: str = "end_turn") -> None:
        self.messages = self
        self.response = _Response(payload, stop_reason)
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> _Response:
        self.kwargs = kwargs
        return self.response


class _Writer:
    """GitLab as the merge-request path sees it, recording every call."""

    def __init__(
        self,
        *,
        label: bool = True,
        branches: tuple[str, ...] = ("main",),
        ci_config_path: str | None = None,
        lint: dict[str, Any] | None = None,
        fail_on: str | None = None,
    ) -> None:
        self.label = label
        self.branches = set(branches)
        self.ci_config_path = ci_config_path
        self.lint_result = lint if lint is not None else {"valid": True, "errors": []}
        self.fail_on = fail_on
        self.calls: list[str] = []
        self.commit: dict[str, Any] = {}
        self.merge_request: dict[str, Any] = {}

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_on == name:
            raise GitLabError(f"GitLab returned 500 for {name}.")

    def project(self, project: str) -> dict[str, Any]:
        self._call("project")
        return {"ci_config_path": self.ci_config_path}

    def has_label(self, project: str, name: str) -> bool:
        self._call("has_label")
        return self.label and name == AUTOFIX_LABEL

    def branch_exists(self, project: str, branch: str) -> bool:
        self._call("branch_exists")
        return branch in self.branches

    def file(self, project: str, path: str, ref: str) -> str:
        self._call("file")
        assert ref == SHA  # the file as it was when the job failed
        return CONFIG

    def lint(self, project: str, content: str, ref: str) -> dict[str, Any]:
        self._call("lint")
        return self.lint_result

    def create_commit(self, project: str, **kwargs: Any) -> dict[str, Any]:
        self._call("create_commit")
        self.commit = kwargs
        return {"id": "b" * 40}

    def create_merge_request(self, project: str, **kwargs: Any) -> dict[str, Any]:
        self._call("create_merge_request")
        self.merge_request = kwargs
        return {"web_url": "https://gitlab.com/acme/web/-/merge_requests/7"}


def _job() -> JobRef:
    return JobRef(
        name="lint",
        id=4242,
        stage="lint",
        project="acme/web",
        ref="main",
        sha=SHA,
        url="https://gitlab.com/acme/web/-/jobs/4242",
        source="gitlab",
    )


def _report(
    trace: Path = YAML_TRACE, *, failure_class: str = "yaml", cite: int | None = None
) -> Report:
    distilled = distill(trace.read_text())
    hits = match_rules(distilled)
    payload = {
        "summary": "The lint job names a stage the pipeline never declares",
        "root_cause": "Line 23 says stage lint is not defined.",
        "confidence": "high",
        "category": "config",
        "failure_class": failure_class,
        "evidence_lines": [cite or (hits[0].line_number if hits else 1)],
        "fixes": [{"title": "Declare the stage", "detail": "Add lint to stages.", "patch": ""}],
    }
    diagnosis = diagnose(_job(), distilled, hits, model=MODEL, client=_Anthropic(payload))
    return Report(job=_job(), distilled=distilled, hits=hits, diagnosis=diagnosis)


def _propose(content: str = FIXED, calls: list[str] | None = None) -> Any:
    def run(
        job: JobRef, distilled: DistilledLog, diagnosis: Diagnosis, path: str, config: str
    ) -> PatchProposal:
        if calls is not None:
            calls.append(path)
        assert config == CONFIG
        return PatchProposal(
            content=content,
            explanation="Declare the lint stage.",
            usage=Usage(model=MODEL, input_tokens=2000, output_tokens=800),
        )

    return run


def _no_model(*_args: Any) -> PatchProposal:
    raise AssertionError("the model must not be called before the cheaper gates pass")


# -- the happy path --------------------------------------------------------


def test_every_gate_passing_opens_a_draft_merge_request() -> None:
    writer = _Writer()
    outcome = open_fix_mr(_report(), writer, _propose())

    assert outcome.opened, outcome.message
    assert outcome.url == "https://gitlab.com/acme/web/-/merge_requests/7"
    assert writer.calls == [
        "has_label",
        "branch_exists",
        "project",
        "file",
        "lint",
        "branch_exists",
        "create_commit",
        "create_merge_request",
    ]
    assert writer.commit["branch"] == "pipelinemd/fix-job-4242"
    assert writer.commit["start_sha"] == SHA
    assert writer.commit["path"] == ".gitlab-ci.yml"
    assert writer.commit["content"] == FIXED
    request = writer.merge_request
    assert request["source_branch"] == "pipelinemd/fix-job-4242"
    assert request["target_branch"] == "main"
    assert request["title"].startswith("Draft: fix(ci): ")
    assert "+  - lint" in request["description"]
    assert "a/.gitlab-ci.yml" in request["description"]
    assert AUTOFIX_LABEL in request["description"]
    assert "CI Lint: valid" in request["description"]
    assert len(outcome.calls) == 1


def test_a_custom_pipeline_file_path_is_the_one_edited() -> None:
    writer = _Writer(ci_config_path="ci/pipeline.yml")
    seen: list[str] = []
    outcome = open_fix_mr(_report(), writer, _propose(calls=seen))

    assert outcome.opened
    assert seen == ["ci/pipeline.yml"]
    assert writer.commit["path"] == "ci/pipeline.yml"


# -- gates that need no network --------------------------------------------


def test_a_failure_that_is_not_yaml_patch_never_touches_gitlab() -> None:
    writer = _Writer()
    outcome = open_fix_mr(_report(NPM_TRACE, failure_class="test"), writer, _no_model)

    assert not outcome.opened
    assert "only yaml_patch" in outcome.message
    assert writer.calls == []


def test_a_model_that_disagrees_with_the_rules_lowers_confidence_and_stops_it() -> None:
    writer = _Writer()
    outcome = open_fix_mr(_report(failure_class="runner"), writer, _no_model)

    assert not outcome.opened
    assert "confidence is" in outcome.message
    assert writer.calls == []


def test_without_a_diagnosis_there_is_nothing_to_fix_from() -> None:
    report = _report()
    outcome = open_fix_mr(
        Report(job=report.job, distilled=report.distilled, hits=report.hits), _Writer(), _no_model
    )
    assert "no Claude diagnosis" in outcome.message


def test_a_log_file_is_not_a_gitlab_job() -> None:
    report = _report()
    local = Report(
        job=JobRef(name="x.log", source="file"),
        distilled=report.distilled,
        hits=report.hits,
        diagnosis=report.diagnosis,
    )
    assert "needs a GitLab job" in open_fix_mr(local, _Writer(), _no_model).message


# -- gates on GitLab, before the model ---------------------------------------


def test_without_the_label_nothing_is_written_and_the_model_is_not_asked() -> None:
    writer = _Writer(label=False)
    outcome = open_fix_mr(_report(), writer, _no_model)

    assert not outcome.opened
    assert f"no `{AUTOFIX_LABEL}` label" in outcome.message
    assert writer.calls == ["has_label"]


def test_a_job_not_on_a_branch_is_left_alone() -> None:
    writer = _Writer(branches=())
    outcome = open_fix_mr(_report(), writer, _no_model)
    assert "is not a branch" in outcome.message


@pytest.mark.parametrize("path", [".gitlab-ci.yml@infra/ci-templates", "https://x.test/ci.yml"])
def test_a_pipeline_file_from_elsewhere_is_left_alone(path: str) -> None:
    outcome = open_fix_mr(_report(), _Writer(ci_config_path=path), _no_model)
    assert "comes from elsewhere" in outcome.message


def test_a_gitlab_error_is_reported_not_raised() -> None:
    outcome = open_fix_mr(_report(), _Writer(fail_on="file"), _no_model)
    assert not outcome.opened
    assert "500" in outcome.message


# -- gates on the proposed fix -----------------------------------------------


def test_a_model_that_finds_no_safe_edit_is_billed_and_stops_it() -> None:
    def refuse(*_args: Any) -> PatchProposal:
        raise DiagnosisError(
            "Claude found no safe edit to .gitlab-ci.yml: it is in an include",
            usage=Usage(model=MODEL, input_tokens=10, output_tokens=10),
        )

    writer = _Writer()
    outcome = open_fix_mr(_report(), writer, refuse)

    assert "no safe edit" in outcome.message
    assert len(outcome.calls) == 1
    assert "lint" not in writer.calls


def test_an_unchanged_file_is_not_proposed() -> None:
    writer = _Writer()
    outcome = open_fix_mr(_report(), writer, _propose(CONFIG))
    assert "unchanged" in outcome.message
    assert "lint" not in writer.calls


def test_a_missing_final_newline_alone_is_not_a_change() -> None:
    outcome = open_fix_mr(_report(), _Writer(), _propose(CONFIG.rstrip("\n")))
    assert "unchanged" in outcome.message


def test_a_rewrite_is_refused_by_size() -> None:
    rewrite = CONFIG + "".join(f"job{i}:\n  script: echo {i}\n" for i in range(20))
    writer = _Writer()
    outcome = open_fix_mr(_report(), writer, _propose(rewrite), max_changed_lines=20)

    assert "over the limit of 20" in outcome.message
    assert outcome.diff
    assert "lint" not in writer.calls


def test_a_fix_that_adds_a_credential_is_refused() -> None:
    leaky = FIXED + "variables:\n  DEPLOY_TOKEN: glpat-abcdefghijklmnopqrstuvwxyz\n"
    writer = _Writer()
    outcome = open_fix_mr(_report(), writer, _propose(leaky))

    assert "looks like a" in outcome.message
    assert "glpat-abc" not in outcome.message
    assert "lint" not in writer.calls


def test_a_fix_that_names_a_variable_is_not_a_credential() -> None:
    referencing = FIXED + "variables:\n  DEPLOY_TOKEN: $DEPLOY_TOKEN\n"
    assert open_fix_mr(_report(), _Writer(), _propose(referencing)).opened


# -- the CI Lint gate (#25) --------------------------------------------------


def test_a_fix_ci_lint_rejects_is_never_committed() -> None:
    writer = _Writer(lint={"valid": False, "errors": ["jobs:lint stage parameter should be build"]})
    outcome = open_fix_mr(_report(), writer, _propose())

    assert not outcome.opened
    assert "CI Lint rejected the fix: jobs:lint stage parameter" in outcome.message
    assert "create_commit" not in writer.calls
    assert outcome.diff  # the reader can still see what was proposed


def test_an_existing_fix_branch_is_never_added_to() -> None:
    writer = _Writer(branches=("main", "pipelinemd/fix-job-4242"))
    outcome = open_fix_mr(_report(), writer, _propose())

    assert "already exists" in outcome.message
    assert "create_commit" not in writer.calls


def test_a_failed_commit_is_reported() -> None:
    outcome = open_fix_mr(_report(), _Writer(fail_on="create_commit"), _propose())
    assert not outcome.opened
    assert "500" in outcome.message


# -- helpers -------------------------------------------------------------------


def test_changed_lines_counts_additions_and_removals() -> None:
    count, diff = changed_lines("a\nb\n", "a\nc\n", ".gitlab-ci.yml")
    assert count == 2
    assert diff.startswith("--- a/.gitlab-ci.yml")


def test_the_fix_branch_is_named_after_the_job() -> None:
    assert branch_name(_job()) == "pipelinemd/fix-job-4242"


def test_the_fix_call_counts_in_the_reports_cost() -> None:
    report = _report()
    outcome = open_fix_mr(report, _Writer(), _propose())
    with_fix = Report(
        job=report.job,
        distilled=report.distilled,
        hits=report.hits,
        diagnosis=report.diagnosis,
        fix_calls=outcome.calls,
    )
    assert len(cost_of(with_fix).calls) == 2


# -- the patch call ------------------------------------------------------------


def _patch(payload: dict[str, Any], stop_reason: str = "end_turn") -> Any:
    report = _report()
    assert report.diagnosis is not None
    client = _Anthropic(payload, stop_reason)
    result = propose_patch(
        report.job,
        report.distilled,
        report.diagnosis,
        ".gitlab-ci.yml",
        CONFIG,
        model=MODEL,
        client=client,
    )
    return result, client


def test_propose_patch_returns_the_whole_file_and_its_usage() -> None:
    result, client = _patch({"fixable": True, "content": FIXED, "explanation": "Declare lint."})

    assert result.content == FIXED
    assert result.usage.input_tokens == 1000
    message = client.kwargs["messages"][0]["content"]
    assert CONFIG in message
    assert "names a stage the pipeline never declares" in message
    assert client.kwargs["output_config"]["format"]["type"] == "json_schema"


def test_propose_patch_reports_not_fixable_with_its_usage() -> None:
    with pytest.raises(DiagnosisError, match="no safe edit") as raised:
        _patch({"fixable": False, "content": "", "explanation": "It is in an include."})
    assert raised.value.usage is not None


def test_propose_patch_refuses_a_cut_off_file() -> None:
    with pytest.raises(DiagnosisError, match="did not fit"):
        _patch({"fixable": True, "content": FIXED[:20], "explanation": ""}, "max_tokens")


# -- the write client ------------------------------------------------------------


class _Opener:
    def __init__(self, responses: list[tuple[int, str]]) -> None:
        self.responses = responses
        self.requests: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, timeout: float) -> Any:
        import urllib.error

        self.requests.append(request)
        status, body = self.responses.pop(0)
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                "error",
                {},
                io.BytesIO(body.encode()),  # type: ignore[arg-type]
            )
        response = io.BytesIO(body.encode())
        response.status = status  # type: ignore[attr-defined]
        response.headers = {}  # type: ignore[attr-defined]
        response.__enter__ = lambda: response  # type: ignore[method-assign]
        return _Ctx(response)


class _Ctx:
    def __init__(self, response: Any) -> None:
        self.response = response

    def __enter__(self) -> Any:
        return self.response

    def __exit__(self, *args: Any) -> None:
        return None


def _write_client(responses: list[tuple[int, str]]) -> tuple[WriteClient, _Opener]:
    client = WriteClient("https://gitlab.com", "glpat-write")
    opener = _Opener(responses)
    client.http._opener = opener  # type: ignore[assignment]
    return client, opener


def test_writes_are_posts_with_the_write_token_and_a_json_body() -> None:
    client, opener = _write_client([(201, '{"web_url": "u"}')])
    client.create_merge_request(
        "acme/web", source_branch="s", target_branch="main", title="t", description="d"
    )

    request = opener.requests[0]
    assert request.get_method() == "POST"
    assert request.full_url == "https://gitlab.com/api/v4/projects/acme%2Fweb/merge_requests"
    assert request.get_header("Private-token") == "glpat-write"
    assert json.loads(request.data or b"{}")["remove_source_branch"] is True


def test_writes_are_never_retried() -> None:
    client, opener = _write_client([(502, "bad gateway"), (201, "{}")])
    with pytest.raises(GitLabError, match="502"):
        client.create_commit(
            "acme/web", branch="b", start_sha=SHA, path=".gitlab-ci.yml", content="x", message="m"
        )
    assert len(opener.requests) == 1


def test_the_label_must_match_exactly() -> None:
    client, _opener = _write_client([(200, json.dumps([{"name": f"{AUTOFIX_LABEL}-off"}]))])
    assert not client.has_label("acme/web", AUTOFIX_LABEL)
    client, _opener = _write_client([(200, json.dumps([{"name": AUTOFIX_LABEL}]))])
    assert client.has_label("acme/web", AUTOFIX_LABEL)


def test_a_missing_branch_reads_as_absent() -> None:
    client, _opener = _write_client([(404, '{"message":"404 Branch Not Found"}')])
    assert not client.branch_exists("acme/web", "pipelinemd/fix-job-1")


def test_a_missing_project_is_still_an_error() -> None:
    client, _opener = _write_client([(404, "{}")])
    with pytest.raises(NotFoundError):
        client.project("acme/nope")


# -- the CLI ---------------------------------------------------------------------


def _run(*argv: str) -> tuple[int, str]:
    err = io.StringIO()
    code = cli.main(["diagnose", *argv], stdout=io.StringIO(), stderr=err)
    return code, err.getvalue()


@pytest.fixture
def with_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "llm_available", lambda: True)


@pytest.mark.usefixtures("with_llm")
def test_open_mr_refuses_a_log_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WRITE_TOKEN_ENV, "glpat-write")
    code, err = _run("--from-file", str(YAML_TRACE), "--open-mr")
    assert code == cli.EXIT_USAGE
    assert "not --from-file" in err


@pytest.mark.usefixtures("with_llm")
def test_open_mr_refuses_no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WRITE_TOKEN_ENV, "glpat-write")
    code, err = _run("https://gitlab.com/acme/web/-/jobs/1", "--open-mr", "--no-llm")
    assert code == cli.EXIT_USAGE
    assert "not with --no-llm" in err


@pytest.mark.usefixtures("with_llm")
def test_open_mr_never_falls_back_to_the_read_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WRITE_TOKEN_ENV, raising=False)
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-read")
    code, err = _run("https://gitlab.com/acme/web/-/jobs/1", "--open-mr")
    assert code == cli.EXIT_USAGE
    assert WRITE_TOKEN_ENV in err


@pytest.mark.usefixtures("with_llm")
def test_the_write_token_stays_on_its_own_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WRITE_TOKEN_ENV, "glpat-write")
    monkeypatch.delenv("PIPELINEMD_GITLAB_URL", raising=False)
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    code, err = _run("https://evil.example.com/acme/web/-/jobs/1", "--open-mr")
    assert code == cli.EXIT_USAGE
    assert "belongs to https://gitlab.com" in err


@pytest.mark.usefixtures("with_llm")
def test_a_full_run_opens_the_merge_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WRITE_TOKEN_ENV, "glpat-write")
    monkeypatch.delenv("PIPELINEMD_GITLAB_URL", raising=False)
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    report = _report()
    writers: list[Any] = []
    configs: list[str | None] = []

    class _Reader:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def get_job(self, project: str, job_id: int) -> dict[str, Any]:
            return {"id": 4242, "name": "lint", "ref": "main", "sha": SHA}

        def job_ref(self, job: dict[str, Any], project: str) -> JobRef:
            return _job()

        def get_trace(self, project: str, job_id: int) -> str:
            return YAML_TRACE.read_text()

        def list_pipeline_jobs(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        def get_ci_config(self, project: str, ref: str, path: str = "") -> str:
            return CONFIG

    def fake_diagnose(*args: Any, ci_config: str | None = None, **kwargs: Any) -> Diagnosis:
        configs.append(ci_config)
        assert report.diagnosis is not None
        return report.diagnosis

    def fake_writer(base_url: str, token: str, **kwargs: Any) -> _Writer:
        assert (base_url, token) == ("https://gitlab.com", "glpat-write")
        writer = _Writer()
        writers.append(writer)
        return writer

    def fake_propose(*args: Any, **kwargs: Any) -> PatchProposal:
        return _propose()(*args)

    monkeypatch.setattr(cli, "GitLabClient", _Reader)
    monkeypatch.setattr(cli, "run_diagnosis", fake_diagnose)
    monkeypatch.setattr(cli, "WriteClient", fake_writer)
    monkeypatch.setattr(cli, "propose_patch", fake_propose)

    code, err = _run("https://gitlab.com/acme/web/-/jobs/4242", "--open-mr")

    assert code == 0, err
    assert "opened a draft merge request" in err
    assert configs == [CONFIG]  # --open-mr implies --with-ci-config
    assert "create_merge_request" in writers[0].calls


def test_without_open_mr_nothing_is_written(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WRITE_TOKEN_ENV, "glpat-write")
    monkeypatch.setattr(
        cli, "WriteClient", lambda *a, **k: pytest.fail("no write client without --open-mr")
    )
    code, _err = _run("--from-file", str(YAML_TRACE), "--no-llm")
    assert code == 0


def test_an_outcome_reads_as_a_sentence() -> None:
    assert AutofixOutcome(opened=False, message="no merge request: x.").message.endswith(".")


def test_propose_patch_reports_a_refusal_and_a_failed_request() -> None:
    with pytest.raises(DiagnosisError, match="declined") as raised:
        _patch({"fixable": True, "content": FIXED, "explanation": ""}, "refusal")
    assert raised.value.usage is not None

    class _Down:
        messages = None

        def __init__(self) -> None:
            self.messages = self

        def create(self, **_kwargs: Any) -> Any:
            raise ConnectionError("network unreachable")

    report = _report()
    assert report.diagnosis is not None
    with pytest.raises(DiagnosisError, match="request failed") as failed:
        propose_patch(
            report.job,
            report.distilled,
            report.diagnosis,
            ".gitlab-ci.yml",
            CONFIG,
            client=_Down(),
        )
    assert failed.value.usage is None


def test_lint_runs_in_the_projects_context_and_reads_back_a_file() -> None:
    client, opener = _write_client([(200, '{"valid": true}'), (200, "stages: [x]\n")])

    assert client.lint("acme/web", "stages: [x]\n", "main") == {"valid": True}
    assert json.loads(opener.requests[0].data or b"{}") == {
        "content": "stages: [x]\n",
        "ref": "main",
        "dry_run": False,
    }
    assert client.file("acme/web", "ci/p.yml", SHA) == "stages: [x]\n"
    assert "files/ci%2Fp.yml/raw?ref=" in opener.requests[1].full_url


def test_a_write_that_answers_with_something_other_than_json_is_an_error() -> None:
    client, _opener = _write_client([(201, "<html>")])
    with pytest.raises(GitLabError, match="Expected JSON"):
        client.create_merge_request(
            "acme/web", source_branch="s", target_branch="main", title="t", description="d"
        )

"""The CLI, exercised end to end through main() with captured streams."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pipelinemd import cli
from pipelinemd.cli import EXIT_GITLAB, EXIT_NOTHING, EXIT_OK, EXIT_USAGE, main

TRACES = Path(__file__).parent / "fixtures" / "traces"


def run(*argv: str, stdin: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), stdout=out, stderr=err, stdin=io.StringIO(stdin))
    return code, out.getvalue(), err.getvalue()


# -- distill ----------------------------------------------------------------


def test_distill_a_file() -> None:
    code, out, _err = run("distill", str(TRACES / "npm_eresolve.log"), "--color", "never")
    assert code == EXIT_OK
    assert "npm.eresolve" in out
    assert "Evidence" in out


def test_distill_from_stdin() -> None:
    code, out, _err = run(
        "distill",
        "-",
        "--color",
        "never",
        stdin="$ npm ci\nnpm ERR! code ERESOLVE\nERROR: Job failed: exit code 1\n",
    )
    assert code == EXIT_OK
    assert "npm.eresolve" in out


def test_distill_json_is_machine_readable() -> None:
    code, out, _err = run("distill", str(TRACES / "pytest_failures.log"), "--format", "json")
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["rule_hits"][0]["id"] == "test.pytest-failed"
    assert payload["trace"]["exit_code"] == 1


def test_distill_markdown() -> None:
    code, out, _err = run("distill", str(TRACES / "no_space_left.log"), "--format", "markdown")
    assert code == EXIT_OK
    assert out.startswith("### pipelinemd")
    assert "runner.no-space" in out


def test_distill_writes_to_a_file(tmp_path: Path) -> None:
    target = tmp_path / "report.md"
    code, out, _err = run(
        "distill", str(TRACES / "npm_eresolve.log"), "--format", "markdown", "-o", str(target)
    )
    assert code == EXIT_OK
    assert out == "", "output went to the file, not stdout"
    assert "npm.eresolve" in target.read_text()


def test_distill_missing_file_is_a_usage_error() -> None:
    code, _out, err = run("distill", "/nope/missing.log")
    assert code == EXIT_USAGE
    assert "No such file" in err


def test_distill_never_reaches_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The offline path must not construct a client at all."""

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("distill must not touch the network")

    monkeypatch.setattr(cli, "GitLabClient", explode)
    assert run("distill", str(TRACES / "npm_eresolve.log"))[0] == EXIT_OK


# -- rules / explain --------------------------------------------------------


def test_rules_lists_the_catalog() -> None:
    code, out, _err = run("rules", "--color", "never")
    assert code == EXIT_OK
    assert "npm.eresolve" in out
    assert "dependency" in out


def test_rules_filters_by_category() -> None:
    code, out, _err = run("rules", "--category", "docker", "--color", "never")
    assert code == EXIT_OK
    assert "docker.daemon-unreachable" in out
    assert "npm.eresolve" not in out


def test_rules_rejects_an_unknown_category() -> None:
    code, _out, err = run("rules", "--category", "banana")
    assert code == EXIT_USAGE
    assert "Try one of" in err


def test_rules_search() -> None:
    code, out, _err = run("rules", "--search", "lockfile", "--color", "never")
    assert code == EXIT_OK
    assert "npm.lockfile-out-of-sync" in out
    assert "docker.daemon-unreachable" not in out


def test_rules_json() -> None:
    code, out, _err = run("rules", "--json")
    payload = json.loads(out)
    assert code == EXIT_OK
    assert len(payload) >= 50
    assert all({"id", "title", "fixes"} <= set(rule) for rule in payload)


def test_explain_shows_the_patterns() -> None:
    code, out, _err = run("explain", "npm.eresolve", "--color", "never")
    assert code == EXIT_OK
    assert "peer dependency" in out
    assert "ERESOLVE" in out


def test_explain_suggests_near_misses() -> None:
    code, _out, err = run("explain", "npm")
    assert code == EXIT_USAGE
    assert "Did you mean" in err


def test_explain_json() -> None:
    code, out, _err = run("explain", "shell.command-not-found", "--json")
    payload = json.loads(out)
    assert code == EXIT_OK
    assert payload["exit_codes"] == [127]


# -- diagnose ---------------------------------------------------------------


def test_diagnose_from_file_offline() -> None:
    code, out, err = run(
        "diagnose",
        "--from-file",
        str(TRACES / "docker_daemon_unreachable.log"),
        "--no-llm",
        "--color",
        "never",
    )
    assert code == EXIT_OK
    assert "docker.daemon-unreachable" in out
    assert err == ""


def test_diagnose_without_a_target_and_outside_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "detect_ci", lambda: None)
    code, _out, err = run("diagnose", "--no-llm")
    assert code == EXIT_USAGE
    assert "Nothing to diagnose" in err


def test_diagnose_rejects_a_non_gitlab_url() -> None:
    code, _out, err = run("diagnose", "https://example.com/not/gitlab", "--no-llm")
    assert code == EXIT_USAGE
    assert "not a GitLab" in err


class _FakeClient:
    """Stands in for GitLabClient without any network."""

    def __init__(self, *args: Any, jobs: list[dict[str, Any]] | None = None, **kwargs: Any) -> None:
        self.jobs = (
            jobs
            if jobs is not None
            else [{"id": 1, "name": "build", "stage": "test", "status": "failed", "ref": "main"}]
        )

    def failed_jobs(self, project: str, pipeline_id: int) -> list[dict[str, Any]]:
        return self.jobs

    def get_job(self, project: str, job_id: int) -> dict[str, Any]:
        return self.jobs[0]

    def get_trace(self, project: str, job_id: int) -> str:
        return (TRACES / "npm_eresolve.log").read_text()

    def get_ci_config(self, project: str, ref: str, path: str = ".gitlab-ci.yml") -> str | None:
        return "build:\n  script: npm ci\n"

    def job_ref(self, job: dict[str, Any], project: str) -> Any:
        from pipelinemd.models import JobRef

        return JobRef(name=job["name"], id=job["id"], stage=job.get("stage"), project=project)


def test_diagnose_a_job_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "GitLabClient", _FakeClient)
    code, out, _err = run(
        "diagnose", "https://gitlab.com/acme/web/-/jobs/98765", "--no-llm", "--color", "never"
    )
    assert code == EXIT_OK
    assert "npm.eresolve" in out


def test_diagnose_a_pipeline_picks_the_failed_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "GitLabClient", _FakeClient)
    code, out, _err = run(
        "diagnose", "https://gitlab.com/acme/web/-/pipelines/12345", "--no-llm", "--format", "json"
    )
    assert code == EXIT_OK
    assert json.loads(out)["job"]["name"] == "build"


def test_diagnose_reports_when_nothing_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "GitLabClient", lambda *a, **k: _FakeClient(jobs=[]))
    code, _out, err = run("diagnose", "https://gitlab.com/acme/web/-/pipelines/1", "--no-llm")
    assert code == EXIT_NOTHING
    assert "No failed jobs" in err


def test_diagnose_mentions_the_other_failed_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [
        {"id": 1, "name": "build", "stage": "build", "status": "failed"},
        {"id": 2, "name": "test", "stage": "test", "status": "failed"},
    ]
    monkeypatch.setattr(cli, "GitLabClient", lambda *a, **k: _FakeClient(jobs=jobs))
    code, _out, err = run("diagnose", "https://gitlab.com/acme/web/-/pipelines/1", "--no-llm")
    assert code == EXIT_OK
    assert "--all-jobs" in err and "test" in err


def test_diagnose_all_jobs_emits_a_json_list(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [
        {"id": 1, "name": "build", "stage": "build", "status": "failed"},
        {"id": 2, "name": "test", "stage": "test", "status": "failed"},
    ]
    monkeypatch.setattr(cli, "GitLabClient", lambda *a, **k: _FakeClient(jobs=jobs))
    code, out, _err = run(
        "diagnose",
        "https://gitlab.com/acme/web/-/pipelines/1",
        "--all-jobs",
        "--no-llm",
        "--format",
        "json",
    )
    payload = json.loads(out)
    assert code == EXIT_OK
    assert isinstance(payload, list) and len(payload) == 2


def test_gitlab_errors_get_their_own_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    from pipelinemd.errors import AuthError

    class _Unauthorised(_FakeClient):
        def get_job(self, project: str, job_id: int) -> dict[str, Any]:
            raise AuthError("token lacks read_api", status=401)

    monkeypatch.setattr(cli, "GitLabClient", _Unauthorised)
    code, _out, err = run("diagnose", "https://gitlab.com/acme/web/-/jobs/1", "--no-llm")
    assert code == EXIT_GITLAB
    assert "read_api" in err


def test_a_failed_diagnosis_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deterministic findings must survive the model being unavailable."""
    from pipelinemd.errors import DiagnosisError

    monkeypatch.setattr(cli, "llm_available", lambda: True)
    monkeypatch.setattr(
        cli,
        "run_diagnosis",
        lambda *a, **k: (_ for _ in ()).throw(DiagnosisError("no credentials")),
    )
    code, out, err = run(
        "diagnose", "--from-file", str(TRACES / "npm_eresolve.log"), "--color", "never"
    )
    assert code == EXIT_OK
    assert "npm.eresolve" in out
    assert "no credentials" in err
    assert "Rules were still applied" in err


def test_missing_anthropic_package_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "llm_available", lambda: False)
    code, out, err = run("diagnose", "--from-file", str(TRACES / "npm_eresolve.log"))
    assert code == EXIT_OK
    assert "pipelinemd[llm]" in err
    assert "npm.eresolve" in out


def test_diagnosis_is_rendered_when_it_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    from pipelinemd.models import Category, Confidence, Diagnosis, Fix

    diagnosis = Diagnosis(
        summary="Peer dependency conflict",
        root_cause="react 17 vs 18.",
        confidence=Confidence.HIGH,
        category=Category.DEPENDENCY,
        fixes=(Fix(title="Regenerate the lockfile", detail="npm install", patch="npm install"),),
        model="claude-opus-5",
    )
    monkeypatch.setattr(cli, "llm_available", lambda: True)
    monkeypatch.setattr(cli, "run_diagnosis", lambda *a, **k: diagnosis)
    code, out, _err = run(
        "diagnose", "--from-file", str(TRACES / "npm_eresolve.log"), "--color", "never"
    )
    assert code == EXIT_OK
    assert "Peer dependency conflict" in out
    assert "Regenerate the lockfile" in out


def test_with_ci_config_reaches_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake(job: Any, distilled: Any, hits: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        raise __import__("pipelinemd.errors", fromlist=["DiagnosisError"]).DiagnosisError("stop")

    monkeypatch.setattr(cli, "GitLabClient", _FakeClient)
    monkeypatch.setattr(cli, "llm_available", lambda: True)
    monkeypatch.setattr(cli, "run_diagnosis", fake)
    run("diagnose", "https://gitlab.com/acme/web/-/jobs/1", "--with-ci-config")
    assert captured["ci_config"] == "build:\n  script: npm ci\n"


# -- misc -------------------------------------------------------------------


def test_version_flag() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0


def test_no_subcommand_is_an_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code != 0


def test_every_fixture_survives_the_cli(trace: Callable[[str], str]) -> None:
    for path in sorted(TRACES.glob("*.log")):
        code, out, _err = run("distill", str(path), "--format", "json")
        assert code == EXIT_OK, path.name
        json.loads(out)

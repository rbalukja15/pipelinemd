"""Open a merge request with the fix, for the one class whose fix is a file edit.

`diagnose --open-mr` only. Every gate below has to pass, in this order, and
the first that does not ends it with the reason: the cheap, local checks
before any request, the reads before the paid model call, and GitLab's own CI
Lint (#25) before anything is written. ADR-0002 sets the rules; this is them.

1. The report is a `yaml` failure with a `yaml_patch` fix type, decided by the
   rules, at high confidence, with a grounded diagnosis.
2. The project carries the `pipelinemd-autofix` label, so a maintainer decided
   automation is welcome there.
3. The job ran on a branch, and the pipeline file is a plain file in the
   project.
4. Claude returns the whole corrected file.
5. The change is small, a handful of lines rather than a rewrite, and adds
   nothing that looks like a credential.
6. GitLab's CI Lint accepts the corrected file in the project's context.
7. The fix branch does not exist yet, so a run never adds to someone's branch.

Then one commit on a new branch from the failing commit, and a draft merge
request for a person to review. pipelinemd never merges anything.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .assessment import assess
from .cost import Cost, format_usd
from .diagnose.patch import PatchProposal
from .distill.redact import MASK, redact
from .errors import DiagnosisError, GitLabError
from .models import Confidence, Diagnosis, DistilledLog, FixType, JobRef, Report, Usage
from .taxonomy import classify

#: The project label that allows `--open-mr` to act. A label, not a setting
#: in pipelinemd, so the project decides, and a maintainer can withdraw it.
AUTOFIX_LABEL = "pipelinemd-autofix"

#: The write token's variable. Never the read token (ADR-0002).
WRITE_TOKEN_ENV = "PIPELINEMD_WRITE_TOKEN"

#: Lines added plus lines removed. A config error is a typo, a missing stage,
#: a misplaced key; a fix bigger than this is a rewrite a person should make.
DEFAULT_MAX_CHANGED_LINES = 20

DEFAULT_CI_CONFIG_PATH = ".gitlab-ci.yml"

_VARIABLE = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?")

ProposeFn = Callable[[JobRef, DistilledLog, Diagnosis, str, str], PatchProposal]


class Writer(Protocol):
    """What the merge-request path needs from GitLab. `gitlab.write.WriteClient`."""

    def project(self, project: str) -> dict[str, Any]: ...
    def has_label(self, project: str, name: str) -> bool: ...
    def branch_exists(self, project: str, branch: str) -> bool: ...
    def file(self, project: str, path: str, ref: str) -> str: ...
    def lint(self, project: str, content: str, ref: str) -> dict[str, Any]: ...
    def create_commit(
        self, project: str, *, branch: str, start_sha: str, path: str, content: str, message: str
    ) -> dict[str, Any]: ...
    def create_merge_request(
        self, project: str, *, source_branch: str, target_branch: str, title: str, description: str
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class AutofixOutcome:
    """What `--open-mr` did, or the gate that stopped it."""

    opened: bool
    message: str
    url: str | None = None
    diff: str | None = None
    #: The model call that wrote the patch, when one was made. Billed even
    #: when a later gate threw the patch away.
    calls: tuple[Usage, ...] = ()


def _stop(message: str, **kwargs: Any) -> AutofixOutcome:
    return AutofixOutcome(opened=False, message=message, **kwargs)


def eligibility(report: Report) -> str | None:
    """Why this report may not become a merge request, or None if it may.

    No network: this runs before anything is asked of GitLab or the model.
    """
    job = report.job
    if job.source != "gitlab" or job.id is None or not job.project:
        return "--open-mr needs a GitLab job, not a log file"
    classification = classify(report)
    if classification.fix_type is not FixType.YAML_PATCH:
        fix = classification.fix_type.value if classification.fix_type else "none"
        return f"the fix type is {fix}, and only yaml_patch failures are fixed automatically"
    if report.diagnosis is None:
        return "there is no Claude diagnosis to base a fix on"
    if not report.diagnosis.fully_grounded:
        return "the diagnosis cites lines that are not in the evidence"
    assessment = assess(report)
    if assessment.confidence is not Confidence.HIGH:
        return f"confidence is {assessment.confidence.value} ({'; '.join(assessment.reasons)})"
    if not job.ref or not job.sha:
        return "the job's branch and commit are unknown"
    return None


def changed_lines(before: str, after: str, path: str) -> tuple[int, str]:
    """Lines added plus removed, and the unified diff a reviewer would read."""
    diff = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    count = sum(1 for line in diff if line[:1] in "+-" and not line.startswith(("+++", "---")))
    return count, "".join(diff)


def _added_secrets(before: str, after: str) -> str | None:
    """The first new line of `after` the redactor would mask, if any.

    Only new lines: a file that already holds a test database's password is
    the project's business, but the fix must not be the thing that adds one.
    """
    existing = set(before.splitlines())
    for line in after.splitlines():
        if line in existing:
            continue
        # `TOKEN: $DEPLOY_TOKEN` names a variable; it holds no secret itself.
        bare = _VARIABLE.sub("", line)
        if MASK in line or redact(bare) != bare:
            return redact(line).strip()
    return None


def branch_name(job: JobRef) -> str:
    return f"pipelinemd/fix-job-{job.id}"


def _lint_errors(result: dict[str, Any]) -> list[str]:
    errors = result.get("errors") or []
    return [str(error) for error in errors] or ["CI Lint did not say why"]


def _description(report: Report, proposal: PatchProposal, diff: str, path: str) -> str:
    diagnosis = report.diagnosis
    assert diagnosis is not None  # eligibility() checked it
    job = report.job
    cited = "\n".join(f"L{c.line_number:<6} {c.text}" for c in diagnosis.citations)
    job_link = f"[{job.label}]({job.url})" if job.url else job.label
    return "\n".join(
        [
            f"pipelinemd diagnosed {job_link} as a problem in `{path}` and proposes "
            "this fix. **Review it before merging**: it was written by a model, then "
            "checked by GitLab's CI Lint.",
            "",
            "### Diagnosis",
            "",
            diagnosis.summary,
            "",
            diagnosis.root_cause,
            "",
            "### Evidence from the job log",
            "",
            # Four backticks: a log line can hold three.
            "````",
            cited,
            "````",
            "",
            "### The change",
            "",
            proposal.explanation,
            "",
            "```diff",
            diff.rstrip(),
            "```",
            "",
            f"CI Lint: valid. Opened by pipelinemd because this project has the "
            f"`{AUTOFIX_LABEL}` label; remove the label to stop these.",
        ]
    )


def open_fix_mr(
    report: Report,
    writer: Writer,
    propose: ProposeFn,
    *,
    max_changed_lines: int = DEFAULT_MAX_CHANGED_LINES,
) -> AutofixOutcome:
    """Run every gate, then open the merge request. Never raises for a gate."""
    reason = eligibility(report)
    if reason is not None:
        return _stop(f"no merge request: {reason}.")
    job = report.job
    diagnosis = report.diagnosis
    assert diagnosis is not None and job.project and job.ref and job.sha  # eligibility()
    project = job.project

    try:
        if not writer.has_label(project, AUTOFIX_LABEL):
            return _stop(
                f"no merge request: the project has no `{AUTOFIX_LABEL}` label. Add it to "
                "allow pipelinemd to propose fixes there."
            )
        if not writer.branch_exists(project, job.ref):
            return _stop(f"no merge request: {job.ref} is not a branch to propose a fix to.")
        path = str(writer.project(project).get("ci_config_path") or DEFAULT_CI_CONFIG_PATH)
        if "@" in path or "://" in path:
            return _stop(
                f"no merge request: the pipeline file comes from elsewhere ({path}), "
                "not from this project."
            )
        config = writer.file(project, path, job.sha)
    except GitLabError as exc:
        return _stop(f"no merge request: {exc}")

    try:
        proposal = propose(job, report.distilled, diagnosis, path, config)
    except DiagnosisError as exc:
        calls = (exc.usage,) if exc.usage is not None else ()
        return _stop(f"no merge request: {exc}", calls=calls)
    calls = (proposal.usage,)

    content = proposal.content
    if config.endswith("\n") and not content.endswith("\n"):
        content += "\n"
    count, diff = changed_lines(config, content, path)
    if count == 0:
        return _stop("no merge request: the proposed file is unchanged.", calls=calls)
    if count > max_changed_lines:
        return _stop(
            f"no merge request: the fix changes {count} lines, over the limit of "
            f"{max_changed_lines}. That is a rewrite for a person to make.",
            diff=diff,
            calls=calls,
        )
    added = _added_secrets(config, content)
    if added:
        return _stop(
            "no merge request: the proposed file adds something that looks like a "
            f"credential ({added}). Secrets belong in masked CI/CD variables.",
            diff=diff,
            calls=calls,
        )

    branch = branch_name(job)
    try:
        lint = writer.lint(project, content, job.ref)
        if not lint.get("valid"):
            errors = "; ".join(_lint_errors(lint))
            return _stop(
                f"no merge request: GitLab's CI Lint rejected the fix: {errors}",
                diff=diff,
                calls=calls,
            )
        if writer.branch_exists(project, branch):
            return _stop(
                f"no merge request: {branch} already exists, so a fix for this job was "
                "already proposed.",
                diff=diff,
                calls=calls,
            )
        writer.create_commit(
            project,
            branch=branch,
            start_sha=job.sha,
            path=path,
            content=content,
            message=f"fix(ci): {diagnosis.summary}"[:200],
        )
        merge_request = writer.create_merge_request(
            project,
            source_branch=branch,
            target_branch=job.ref,
            title=f"Draft: fix(ci): {diagnosis.summary}"[:250],
            description=_description(report, proposal, diff, path),
        )
    except GitLabError as exc:
        return _stop(f"no merge request: {exc}", diff=diff, calls=calls)

    url = merge_request.get("web_url")
    spent = Cost(calls=calls).usd
    return AutofixOutcome(
        opened=True,
        message=(
            f"opened a draft merge request with the fix: {url} "
            f"({count} changed lines, CI Lint valid, patch cost {format_usd(spent)})"
        ),
        url=str(url) if url else None,
        diff=diff,
        calls=calls,
    )

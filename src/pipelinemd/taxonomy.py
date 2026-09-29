"""Place a failure in the v1 taxonomy, and say what kind of fix it wants.

Two inputs decide the class, and both are reproducible:

* **The rule that ranked first.** Every catalog rule has a base class, listed
  below in one table so the decisions can be reviewed side by side.
* **Retry history.** Every attempt of a job within one pipeline ran against the
  same commit. If another attempt passed, the job is flaky - that is GitLab's
  own record, not an inference from the log. It overrides the rule.

The model's reading of the class is recorded on its Diagnosis and shown, but it
never decides the classification. The class sets the fix type, and `yaml_patch`
is what #24's MR generator acts on: anything that can lead to an automated
write has to come from a signal that gives the same answer twice.
"""

from __future__ import annotations

from .models import (
    Classification,
    Confidence,
    FailureClass,
    FixType,
    Report,
    RetryHistory,
    RetryVerdict,
    Rule,
    RuleHit,
)

#: The classes pipelinemd v1 claims to handle. `unclassified` is not one.
V1_CLASSES: frozenset[FailureClass] = frozenset(
    c for c in FailureClass if c is not FailureClass.UNCLASSIFIED
)

#: What the model may answer. Flaky is excluded on purpose: #17 asks for flaky
#: detection from retry history, "not LLM guess", and a single log cannot show
#: that a job would have passed on a second run.
MODEL_CLASSES: tuple[FailureClass, ...] = tuple(
    c for c in FailureClass if c is not FailureClass.FLAKY
)

#: Suggested fix type per class.
#:
#: `yaml_patch` is reserved for the one class whose fix is, by definition, an
#: edit to `.gitlab-ci.yml`. Every class with a mixed fix gets the conservative
#: type instead: a false `yaml_patch` invites an automated MR (#24), while a
#: false `infra` just means a person does the work. So `cache_artifact` is
#: `infra` even though some of its fixes are YAML - the cache-backend credential
#: case fixed in #42 is not, and neither is an artifact that is missing because
#: the build before it failed.
SUGGESTED_FIX_TYPE: dict[FailureClass, FixType | None] = {
    FailureClass.YAML: FixType.YAML_PATCH,
    # Variables live in project settings, not the repository, and a secret must
    # never be written by automation.
    FailureClass.CI_VARS: FixType.INFRA,
    # Mixed: a wrong tag is a YAML fix, registry auth is not.
    FailureClass.IMAGE_PULL: FixType.INFRA,
    FailureClass.CACHE_ARTIFACT: FixType.INFRA,
    FailureClass.TEST: FixType.CODE_PATCH,
    FailureClass.RUNNER: FixType.INFRA,
    FailureClass.FLAKY: FixType.FLAKY_RETRY,
    FailureClass.UNCLASSIFIED: None,
}

# ---------------------------------------------------------------------------
# Rule -> base class
#
# Chosen from what each rule's failure *is*, not fitted to the corpus. Where a
# rule's meaning is unambiguous this agrees with the corpus labels; where it is
# not, the label and the mapping can disagree, and the eval reports it rather
# than either side being adjusted to match. See docs/evaluation.md.
#
# No rule is mapped to `flaky` unless the failure is transient by its nature.
# A test that times out, or a runner that falls over, is only flaky if another
# attempt passed - which is what retry history is for.
# ---------------------------------------------------------------------------

_YAML = FailureClass.YAML
_CI_VARS = FailureClass.CI_VARS
_IMAGE = FailureClass.IMAGE_PULL
_CACHE = FailureClass.CACHE_ARTIFACT
_TEST = FailureClass.TEST
_RUNNER = FailureClass.RUNNER
_FLAKY = FailureClass.FLAKY
_NONE = FailureClass.UNCLASSIFIED

RULE_CLASS: dict[str, FailureClass] = {
    # -- yaml: the CI configuration, including the job's own script ----------
    "ci.config-invalid": _YAML,
    "git.shallow-depth": _YAML,  # GIT_DEPTH is a CI setting
    "git.lfs-missing": _YAML,  # the job's image lacks a tool it was asked to use
    "shell.command-not-found": _YAML,
    "shell.permission-denied": _YAML,
    "shell.no-such-file": _YAML,
    # -- ci_vars: a credential the job needs is missing, wrong, or scoped away
    "aws.no-credentials": _CI_VARS,
    "git.auth-failed": _CI_VARS,
    "git.submodule-failed": _CI_VARS,  # almost always CI_JOB_TOKEN scope
    "gitlab.token-denied": _CI_VARS,
    "npm.registry-auth": _CI_VARS,
    "kube.forbidden": _CI_VARS,
    # Named for push, and a push denial is a credential's scope. The rule also
    # matches `unauthorized: authentication required`, which registries return
    # for pulls too - a catalog precision problem, recorded as an eval finding.
    "docker.push-denied": _CI_VARS,
    # -- image_pull: acquiring or running the job's container image ---------
    "docker.pull-denied": _IMAGE,
    "docker.manifest-unknown": _IMAGE,
    "docker.rate-limit": _IMAGE,
    "docker.exec-format": _IMAGE,
    "docker.daemon-unreachable": _IMAGE,
    "docker.dind-tls": _IMAGE,
    # -- cache_artifact ------------------------------------------------------
    "ci.cache-failed": _CACHE,
    "ci.artifact-missing": _CACHE,
    "ci.artifact-download-failed": _CACHE,
    # -- test: the project's own code or manifests ---------------------------
    "test.pytest-failed": _TEST,
    "test.jest-failed": _TEST,
    "test.junit-failed": _TEST,
    "test.go-failed": _TEST,
    "lint.eslint": _TEST,
    "build.typescript": _TEST,
    "build.module-not-found": _TEST,
    # Dependency resolution belongs here too: the fix is a change to the
    # project's manifest or lockfile. These are the least certain entries in
    # the table - see the note on `test` in the PR that introduced it.
    "npm.eresolve": _TEST,
    "npm.lockfile-out-of-sync": _TEST,
    "npm.registry-404": _TEST,
    "npm.bad-engine": _TEST,
    "yarn.frozen-lockfile": _TEST,
    "pnpm.outdated-lockfile": _TEST,
    "pip.no-matching-distribution": _TEST,
    "pip.resolution-impossible": _TEST,
    "pip.build-failed": _TEST,
    "python.module-not-found": _TEST,
    "poetry.lock-stale": _TEST,
    "maven.resolve-failed": _TEST,
    "go.missing-gosum": _TEST,
    "bundler.frozen": _TEST,
    "composer.lock-stale": _TEST,
    "cargo.locked": _TEST,
    # -- runner: the runner or its execution environment ---------------------
    "runner.none-available": _RUNNER,
    "runner.job-timeout": _RUNNER,
    # Often transient, but not by nature: a broken runner fails every time.
    # Retry history is what promotes it to flaky.
    "runner.system-failure": _RUNNER,
    "runner.log-limit": _RUNNER,
    "runner.no-space": _RUNNER,
    "runner.oom-killed": _RUNNER,
    "runner.canceled": _RUNNER,
    "node.heap-oom": _RUNNER,
    "gradle.daemon-lost": _RUNNER,
    # An unknown CA fails on every run, so it is not flaky. The fix is the
    # image's trust store or the runner's `tls-ca-file`: the environment.
    "net.tls": _RUNNER,
    # -- flaky: transient by nature ------------------------------------------
    "net.dns": _FLAKY,
    "net.connection-refused": _FLAKY,
    "net.connection-timeout": _FLAKY,
    # -- outside v1 ----------------------------------------------------------
    # A namespace storage quota. No v1 class covers GitLab's own billing
    # limits, and forcing one would be the fitted mapping this table avoids.
    "git.lfs-quota": _NONE,
}


def class_of(rule: Rule) -> FailureClass:
    """A rule's base class. A rule missing from the table is outside v1."""
    return RULE_CLASS.get(rule.id, FailureClass.UNCLASSIFIED)


def _attempts(retry: RetryHistory) -> str:
    return f"{retry.attempts} attempts: {', '.join(retry.statuses)}"


def classify_hits(
    hits: list[RuleHit] | tuple[RuleHit, ...],
    retry: RetryHistory | None = None,
) -> Classification:
    """The deterministic classification: rules first, then retry history."""
    top = hits[0] if hits else None
    rule_id = top.rule.id if top else None
    verdict = retry.verdict if retry else RetryVerdict.INCONCLUSIVE

    # Same job, same commit, different outcome. Nothing in a single log is
    # stronger evidence than that, so it overrides whatever the rule said.
    if retry is not None and verdict is RetryVerdict.PASSED_ON_ANOTHER_ATTEMPT:
        return Classification(
            failure_class=FailureClass.FLAKY,
            fix_type=SUGGESTED_FIX_TYPE[FailureClass.FLAKY],
            confidence=Confidence.HIGH,
            basis=f"another attempt of this job passed on the same commit ({_attempts(retry)})",
            rule_id=rule_id,
            retry=retry,
            source="retry-history",
        )

    if top is not None and (base := class_of(top.rule)) is not FailureClass.UNCLASSIFIED:
        confidence = top.rule.confidence
        # "from npm.eresolve", not "npm.eresolve is a test failure": the class
        # is a bucket, and a dependency conflict is not literally a test.
        basis = f"from {top.rule.id}"
        # Transient by nature, yet it failed identically every time it ran.
        # Keep the class - the rule still describes the failure - but say the
        # evidence cuts against it.
        if (
            retry is not None
            and base is FailureClass.FLAKY
            and verdict is RetryVerdict.FAILED_EVERY_ATTEMPT
        ):
            confidence = Confidence.LOW
            basis += (
                f", but it failed on every attempt ({_attempts(retry)}), "
                "which a transient fault usually does not"
            )
        return Classification(
            failure_class=base,
            fix_type=SUGGESTED_FIX_TYPE[base],
            confidence=confidence,
            basis=basis,
            rule_id=rule_id,
            retry=retry,
            source="rules",
        )

    basis = "no rule fired" if top is None else f"{top.rule.id} is outside the v1 taxonomy"
    return Classification(
        failure_class=FailureClass.UNCLASSIFIED,
        fix_type=None,
        confidence=Confidence.LOW,
        basis=basis,
        rule_id=rule_id,
        retry=retry,
        source="none",
    )


def classify(report: Report) -> Classification:
    """Classify a report. Derived on demand, so it can never go stale.

    A report is rebuilt with `dataclasses.replace` when the diagnosis arrives;
    storing the classification on it would mean remembering to recompute it
    every time, and forgetting once is silent.
    """
    return classify_hits(report.hits, report.retry)


def model_disagreement(report: Report) -> FailureClass | None:
    """The v1 class the model named, when it differs from the report's.

    Shown beside the classification, never applied - see the module docstring.
    A model answering `unclassified` is declining to place the failure, not
    disagreeing with a rule that did, so it is not surfaced: "the model reads
    it as unclassified" beside a confident rule is noise, not information.
    """
    diagnosis = report.diagnosis
    if diagnosis is None or diagnosis.failure_class is FailureClass.UNCLASSIFIED:
        return None
    if diagnosis.failure_class is classify(report).failure_class:
        return None
    return diagnosis.failure_class


def coerce_failure_class(value: object) -> FailureClass:
    """The model's answer, restricted to what it may say.

    Anything outside `MODEL_CLASSES` - including `flaky`, which constrained
    decoding already excludes - reads as unclassified rather than raising: a
    malformed class is not a reason to throw away a grounded diagnosis.
    """
    try:
        parsed = FailureClass(str(value).strip().lower())
    except ValueError:
        return FailureClass.UNCLASSIFIED
    return parsed if parsed in MODEL_CLASSES else FailureClass.UNCLASSIFIED

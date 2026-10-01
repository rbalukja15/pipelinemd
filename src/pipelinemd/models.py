"""Core data structures shared by every layer of pipelinemd.

These are deliberately plain dataclasses with no third-party dependencies:
the distiller, the rule engine and the renderers all speak this vocabulary,
and the JSON renderer is the serialisation contract for other tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Category(StrEnum):
    """Coarse bucket a failure falls into. Drives grouping and colouring."""

    RUNNER = "runner"
    DOCKER = "docker"
    RESOURCES = "resources"
    NETWORK = "network"
    AUTH = "auth"
    DEPENDENCY = "dependency"
    BUILD = "build"
    TEST = "test"
    LINT = "lint"
    CONFIG = "config"
    GIT = "git"
    DEPLOY = "deploy"
    SCRIPT = "script"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FailureClass(StrEnum):
    """The v1 taxonomy from #17: the failure modes pipelinemd v1 claims to handle.

    Deliberately coarser than :class:`Category`. A category says what kind of
    signature matched; a failure class says which supported failure mode that
    puts the job in, and so what kind of fix to reach for. The two are kept
    apart because they answer different questions - `dependency` and `lint`
    are useful categories, but both land in the `test` class, since for v1
    both mean "the project's own code failed, and the fix is a change to it".
    """

    #: The CI configuration itself: invalid YAML, or a job script that asks
    #: for something the job does not have.
    YAML = "yaml"
    #: A credential or variable the job needs is missing, wrong, or scoped away.
    CI_VARS = "ci_vars"
    #: Acquiring or running the job's container image.
    IMAGE_PULL = "image_pull"
    #: Moving cache or artifacts between jobs.
    CACHE_ARTIFACT = "cache_artifact"
    #: The project's own code or manifests: tests, lint, type checks,
    #: compilation, dependency resolution. Surfaced, not diagnosed.
    TEST = "test"
    #: The runner or its execution environment: resources and lifecycle.
    RUNNER = "runner"
    #: Transient: the same job would most likely pass unchanged on retry.
    FLAKY = "flaky"
    #: Outside the v1 taxonomy, or nothing recognisable fired. Always carries
    #: low confidence, because v1 makes no claim about it.
    UNCLASSIFIED = "unclassified"


#: The classes pipelinemd v1 claims to handle. `unclassified` is not one.
#: Defined here, beside the enum, so the corpus loader can validate labels
#: without importing the classifier.
V1_CLASSES: frozenset[FailureClass] = frozenset(
    c for c in FailureClass if c is not FailureClass.UNCLASSIFIED
)


class FixType(StrEnum):
    """Where the fix lives. The vocabulary is from #15.

    `yaml_patch` is the one that matters most: it is what #24's MR generator
    acts on, so it is the only value that can lead to an automated write.
    """

    YAML_PATCH = "yaml_patch"
    CODE_PATCH = "code_patch"
    INFRA = "infra"
    FLAKY_RETRY = "flaky_retry"


class RetryVerdict(StrEnum):
    """What a job's other attempts in the same pipeline say about it."""

    #: Another attempt of the same job, on the same commit, succeeded.
    PASSED_ON_ANOTHER_ATTEMPT = "passed_on_another_attempt"
    #: At least two attempts, and every one of them failed.
    FAILED_EVERY_ATTEMPT = "failed_every_attempt"
    #: One attempt, or none that finished either way. No signal.
    INCONCLUSIVE = "inconclusive"


# ---------------------------------------------------------------------------
# Trace / distillation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TraceLine:
    """One cleaned line of a job trace.

    ``number`` indexes the cleaned trace (1-based) and is what every other
    layer refers to. ``raw_number`` points back at the original download so a
    human can find the line in the GitLab web UI.
    """

    number: int
    raw_number: int
    text: str
    section: str | None = None


@dataclass(frozen=True, slots=True)
class Section:
    """A ``section_start``/``section_end`` span emitted by gitlab-runner."""

    name: str
    start_line: int
    end_line: int | None = None
    duration_s: float | None = None

    @property
    def failed_open(self) -> bool:
        """True when the section never closed - typically where the job died."""
        return self.end_line is None


@dataclass(frozen=True, slots=True)
class EvidenceLine:
    """A line selected for the evidence excerpt."""

    number: int
    text: str
    section: str | None = None
    is_anchor: bool = False
    repeat: int = 1

    @property
    def collapsed(self) -> bool:
        return self.repeat > 1


@dataclass(frozen=True, slots=True)
class EvidenceBlock:
    """A contiguous window of evidence lines, with why it was kept."""

    label: str
    lines: tuple[EvidenceLine, ...]

    @property
    def start(self) -> int:
        return self.lines[0].number if self.lines else 0

    @property
    def end(self) -> int:
        return self.lines[-1].number if self.lines else 0


@dataclass(frozen=True, slots=True)
class DistillStats:
    raw_bytes: int
    raw_lines: int
    clean_lines: int
    evidence_lines: int
    evidence_chars: int

    @property
    def reduction(self) -> float:
        """Fraction of the raw trace discarded, 0.0-1.0."""
        if self.raw_lines <= 0:
            return 0.0
        return 1.0 - (self.evidence_lines / self.raw_lines)


@dataclass(slots=True)
class DistilledLog:
    """The deterministic product: a huge trace reduced to what matters."""

    lines: list[TraceLine] = field(default_factory=list)
    evidence: list[EvidenceBlock] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    exit_code: int | None = None
    failure_reason: str | None = None
    runner: str | None = None
    image: str | None = None
    stats: DistillStats = field(default_factory=lambda: DistillStats(0, 0, 0, 0, 0))

    def evidence_text(self) -> str:
        """The excerpt as plain text, with gap markers between blocks."""
        out: list[str] = []
        prev_end: int | None = None
        for block in self.evidence:
            if prev_end is not None and block.start > prev_end + 1:
                out.append(f"... [{block.start - prev_end - 1} lines omitted] ...")
            for line in block.lines:
                suffix = f"   [x{line.repeat}]" if line.collapsed else ""
                out.append(f"{line.number:>6}  {line.text}{suffix}")
            prev_end = block.end
        return "\n".join(out)

    def clean_text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def failing_section(self) -> Section | None:
        """The section that was still open when the trace ended, if any."""
        for section in reversed(self.sections):
            if section.failed_open:
                return section
        return None


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rule:
    """A deterministic signature for a known CI failure mode.

    A rule fires when any of ``patterns`` matches, none of ``excludes``
    matches, and every entry in ``requires`` also matches somewhere in the
    searched text.
    """

    id: str
    title: str
    category: Category
    patterns: tuple[str, ...]
    explanation: str
    fixes: tuple[str, ...]
    confidence: Confidence = Confidence.MEDIUM
    requires: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    exit_codes: tuple[int, ...] = ()
    docs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuleHit:
    """A rule that matched, and where."""

    rule: Rule
    line_number: int
    line_text: str
    score: float
    in_evidence: bool = True


# ---------------------------------------------------------------------------
# Diagnosis (LLM layer)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Fix:
    title: str
    detail: str
    patch: str | None = None


@dataclass(frozen=True, slots=True)
class Citation:
    """A distilled-log line a diagnosis pointed at, resolved back to its text.

    A citation only exists once it has been checked against the evidence the
    model was actually shown. A line number the model invented never becomes a
    Citation - it is recorded separately as unresolved.
    """

    line_number: int
    text: str
    section: str | None = None
    #: How many trace lines the cited entry stands for. Above 1 the entry is a
    #: collapsed run, and a reader should know one quote covers several lines.
    repeat: int = 1


@dataclass(frozen=True, slots=True)
class Diagnosis:
    summary: str
    root_cause: str
    confidence: Confidence
    category: Category
    #: The model's reading of the v1 class. Recorded and shown, but it never
    #: decides the report's classification - see taxonomy.classify.
    failure_class: FailureClass = FailureClass.UNCLASSIFIED
    fixes: tuple[Fix, ...] = ()
    citations: tuple[Citation, ...] = ()
    #: Line numbers the model cited that do not exist in the evidence. Kept
    #: rather than discarded: a diagnosis that half-invents its evidence should
    #: say so on the face of the report.
    unresolved_citations: tuple[int, ...] = ()
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def fully_grounded(self) -> bool:
        """True when every line the diagnosis cited exists in the evidence."""
        return bool(self.citations) and not self.unresolved_citations


# ---------------------------------------------------------------------------
# Target / report
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class JobRef:
    """Identity of whatever was diagnosed - a real job, or a local file."""

    name: str
    id: int | None = None
    stage: str | None = None
    status: str | None = None
    url: str | None = None
    project: str | None = None
    pipeline_id: int | None = None
    ref: str | None = None
    sha: str | None = None
    duration_s: float | None = None
    allow_failure: bool = False
    source: str = "gitlab"

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.stage:
            parts.append(f"({self.stage})")
        if self.id is not None:
            parts.append(f"#{self.id}")
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class RetryHistory:
    """Every attempt of one job within its pipeline, oldest first.

    All attempts in one pipeline ran against the same commit, so a different
    outcome between them is about as direct as evidence of flakiness gets -
    and it comes from GitLab's own records, not from reading the log.
    """

    statuses: tuple[str, ...]

    @property
    def attempts(self) -> int:
        return len(self.statuses)

    @property
    def verdict(self) -> RetryVerdict:
        # Only finished attempts count. A canceled or still-running attempt says
        # nothing about whether the job can pass.
        finished = [status for status in self.statuses if status in ("success", "failed")]
        if len(self.statuses) < 2 or not finished:
            return RetryVerdict.INCONCLUSIVE
        if "success" in finished:
            return RetryVerdict.PASSED_ON_ANOTHER_ATTEMPT
        if len(finished) >= 2:
            return RetryVerdict.FAILED_EVERY_ATTEMPT
        return RetryVerdict.INCONCLUSIVE


@dataclass(frozen=True, slots=True)
class Classification:
    """Which v1 class a failure falls in, what kind of fix it wants, and why."""

    failure_class: FailureClass
    #: None for unclassified: v1 makes no suggestion about what it cannot place.
    fix_type: FixType | None
    confidence: Confidence
    #: Why this class, in a sentence a reader can check against the report.
    basis: str
    #: The rule the class came from, when one did.
    rule_id: str | None = None
    retry: RetryHistory | None = None
    #: Which signal decided it: "rules", "retry-history", or "none".
    source: str = "rules"


@dataclass(slots=True)
class Report:
    """Everything pipelinemd knows about one failed job."""

    job: JobRef
    distilled: DistilledLog
    hits: list[RuleHit] = field(default_factory=list)
    diagnosis: Diagnosis | None = None
    #: Other attempts of this job in the same pipeline, when they were fetched.
    retry: RetryHistory | None = None

    @property
    def top_hit(self) -> RuleHit | None:
        return self.hits[0] if self.hits else None

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
class Diagnosis:
    summary: str
    root_cause: str
    confidence: Confidence
    category: Category
    fixes: tuple[Fix, ...] = ()
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


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


@dataclass(slots=True)
class Report:
    """Everything pipelinemd knows about one failed job."""

    job: JobRef
    distilled: DistilledLog
    hits: list[RuleHit] = field(default_factory=list)
    diagnosis: Diagnosis | None = None

    @property
    def top_hit(self) -> RuleHit | None:
        return self.hits[0] if self.hits else None

"""Load the labelled failure corpus.

The corpus is the project's ground truth: real-shaped job traces, each tagged
with the class of failure it represents, which catalog rule ought to win, and
the line that a human would point at to explain it. Tests use it to catch
regressions; the eval harness uses it to put a number on accuracy.

It lives at the repository root rather than under ``tests/`` because it is a
project asset both of those consume, and because adding a case should not feel
like editing test plumbing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .errors import PipelinemdError

#: The v1 taxonomy from issue #17. A case outside these is a labelling error.
V1_CLASSES: frozenset[str] = frozenset(
    {
        "yaml",
        "ci_vars",
        "image_pull",
        "cache_artifact",
        "test",
        "runner",
        "flaky",
    }
)

#: How a trace came to exist. The distinction is load-bearing: an accuracy
#: figure computed only over authored traces measures the catalog against its
#: own author, and must never be reported as if it were measured in the wild.
PROVENANCE = frozenset({"authored", "observed"})

DEFAULT_CORPUS_DIR = Path(__file__).resolve().parents[2] / "corpus"


@dataclass(frozen=True, slots=True)
class CorpusCase:
    """One labelled failure."""

    id: str
    failure_class: str
    expected_rule: str | None
    exit_code: int
    evidence_marker: str
    trace_path: Path
    provenance: str
    notes: str = ""

    def read(self) -> str:
        """The raw trace, exactly as a runner would have uploaded it."""
        return self.trace_path.read_text(encoding="utf-8")

    @property
    def expects_rule(self) -> bool:
        """True when a catalog rule ought to fire.

        Every case *is* labelled; ``None`` is itself a label, meaning "no rule
        covers this". The predicate people want is whether a rule is expected,
        which is what this says.
        """
        return self.expected_rule is not None


def corpus_dir(path: Path | None = None) -> Path:
    directory = path or DEFAULT_CORPUS_DIR
    if not (directory / "corpus.jsonl").is_file():
        raise PipelinemdError(
            f"No corpus at {directory}. The corpus ships with the repository, "
            "not the wheel - run from a checkout, or pass an explicit path."
        )
    return directory


def load_corpus(path: Path | None = None) -> list[CorpusCase]:
    """Read every labelled case, in file order.

    Validation is strict on purpose: a corpus that quietly contains a typo'd
    class or a missing trace produces an eval number that is wrong rather than
    absent, which is the worse failure. Every rejection raises
    :class:`PipelinemdError`, so the CLI reports it rather than crashing.
    """
    directory = corpus_dir(path)
    cases: list[CorpusCase] = []
    seen: set[str] = set()

    for number, line in enumerate(
        (directory / "corpus.jsonl").read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PipelinemdError(f"corpus.jsonl line {number} is not JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise PipelinemdError(
                f"corpus.jsonl line {number} is {type(record).__name__}, not a JSON object"
            )

        case = _case_from_record(record, number=number, directory=directory)
        if case.id in seen:
            raise PipelinemdError(f"duplicate corpus id {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    return cases


def _case_from_record(record: dict[str, object], *, number: int, directory: Path) -> CorpusCase:
    case_id = str(record.get("id") or "")
    if not case_id:
        raise PipelinemdError(f"corpus.jsonl line {number} has no id")

    failure_class = str(record.get("failure_class") or "")
    if failure_class not in V1_CLASSES:
        raise PipelinemdError(
            f"{case_id}: unknown failure_class {failure_class!r}; "
            f"expected one of {sorted(V1_CLASSES)}"
        )

    provenance = str(record.get("provenance") or "")
    if provenance not in PROVENANCE:
        raise PipelinemdError(
            f"{case_id}: unknown provenance {provenance!r}; expected one of {sorted(PROVENANCE)}"
        )

    marker = str(record.get("evidence_marker") or "")
    if not marker:
        raise PipelinemdError(f"{case_id}: evidence_marker is required")

    return CorpusCase(
        id=case_id,
        failure_class=failure_class,
        expected_rule=_expected_rule(record, case_id),
        exit_code=_exit_code(record, case_id),
        evidence_marker=marker,
        trace_path=_trace_path(record, case_id, directory),
        provenance=provenance,
        notes=str(record.get("notes") or ""),
    )


def _expected_rule(record: dict[str, object], case_id: str) -> str | None:
    """``None`` is the only way to declare a gap.

    An empty string is falsy too, so a typo would otherwise load as the
    deliberate "no rule covers this" label - and the eval excludes those from
    the rule@1 denominator, so the mistake would move the score instead of
    failing the load.
    """
    expected = record.get("expected_rule")
    if expected is None:
        return None
    if not isinstance(expected, str) or not expected.strip():
        raise PipelinemdError(
            f"{case_id}: expected_rule must be a rule id or null, got {expected!r}. "
            "Use null - and only null - to declare that no rule covers a case."
        )
    return expected


def _exit_code(record: dict[str, object], case_id: str) -> int:
    """Required, never defaulted.

    Defaulting to 0 in a corpus of *failed* jobs would turn an omitted label
    into a plausible-looking one, and the eval scores exit-code accuracy
    against it: the mistake would surface as a metric regression rather than
    as the labelling error it is.
    """
    if "exit_code" not in record:
        raise PipelinemdError(f"{case_id}: exit_code is required; a failed job has one")
    value = record["exit_code"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise PipelinemdError(f"{case_id}: exit_code must be an integer, got {value!r}")
    return value


def _trace_path(record: dict[str, object], case_id: str, directory: Path) -> Path:
    """Resolve the trace, refusing to leave the corpus directory.

    People add cases by pull request; ``..`` in a path is one line to reject
    and an awkward thing to notice in review.
    """
    name = record.get("trace")
    if not isinstance(name, str) or not name:
        raise PipelinemdError(f"{case_id}: trace is required")
    path = directory / name
    if not path.resolve().is_relative_to(directory.resolve()):
        raise PipelinemdError(f"{case_id}: trace {name!r} points outside the corpus directory")
    if not path.is_file():
        raise PipelinemdError(f"{case_id}: no trace at {path}")
    return path

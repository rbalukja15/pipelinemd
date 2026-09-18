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
    def is_labelled(self) -> bool:
        """False when no catalog rule is expected to fire - a known gap."""
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
    absent, which is the worse failure.
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

        case_id = str(record.get("id") or "")
        if not case_id:
            raise PipelinemdError(f"corpus.jsonl line {number} has no id")
        if case_id in seen:
            raise PipelinemdError(f"duplicate corpus id {case_id!r}")
        seen.add(case_id)

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

        trace_path = directory / str(record.get("trace") or "")
        if not trace_path.is_file():
            raise PipelinemdError(f"{case_id}: no trace at {trace_path}")

        marker = str(record.get("evidence_marker") or "")
        if not marker:
            raise PipelinemdError(f"{case_id}: evidence_marker is required")

        expected = record.get("expected_rule")
        cases.append(
            CorpusCase(
                id=case_id,
                failure_class=failure_class,
                expected_rule=str(expected) if expected else None,
                exit_code=int(record.get("exit_code", 0)),
                evidence_marker=marker,
                trace_path=trace_path,
                provenance=provenance,
                notes=str(record.get("notes") or ""),
            )
        )
    return cases

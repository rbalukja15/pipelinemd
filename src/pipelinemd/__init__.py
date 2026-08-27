"""pipelinemd - GitLab CI/CD failure doctor.

Two halves, deliberately separable:

* a **deterministic distiller** that reduces a job trace to the lines that
  explain it and matches them against a catalog of known failure signatures -
  no network, no model, no API key;
* an optional **LLM diagnosis** that reads only the distilled evidence and
  names the root cause.

The first half is the product. The second is the upgrade.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .models import (
    Category,
    Confidence,
    Diagnosis,
    DistilledLog,
    Fix,
    JobRef,
    Report,
    Rule,
    RuleHit,
)

__all__ = [
    "Category",
    "Confidence",
    "Diagnosis",
    "DistilledLog",
    "Fix",
    "JobRef",
    "Report",
    "Rule",
    "RuleHit",
    "__version__",
]

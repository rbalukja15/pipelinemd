"""Deterministic trace distillation - no network, no model, no randomness."""

from .ansi import apply_overwrites, strip_ansi
from .distiller import distill
from .extract import Anchor, find_anchors, score_line, select_evidence
from .redact import redact, redact_lines, redaction_patterns
from .trace import CleanedTrace, clean_trace

__all__ = [
    "Anchor",
    "CleanedTrace",
    "apply_overwrites",
    "clean_trace",
    "distill",
    "find_anchors",
    "redact",
    "redact_lines",
    "redaction_patterns",
    "score_line",
    "select_evidence",
    "strip_ansi",
]

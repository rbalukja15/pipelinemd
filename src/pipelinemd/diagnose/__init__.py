"""Optional LLM diagnosis. Import-safe with `anthropic` absent."""

from .citations import citable_lines, resolve_citations
from .claude import DEFAULT_MODEL, available, diagnose
from .prompt import DIAGNOSIS_SCHEMA, SYSTEM_PROMPT, build_user_message

__all__ = [
    "DEFAULT_MODEL",
    "DIAGNOSIS_SCHEMA",
    "SYSTEM_PROMPT",
    "available",
    "build_user_message",
    "citable_lines",
    "diagnose",
    "resolve_citations",
]

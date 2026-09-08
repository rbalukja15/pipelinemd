"""Optional LLM diagnosis. Import-safe with `anthropic` absent."""

from .claude import DEFAULT_MODEL, available, diagnose
from .prompt import DIAGNOSIS_SCHEMA, SYSTEM_PROMPT, build_user_message

__all__ = [
    "DEFAULT_MODEL",
    "DIAGNOSIS_SCHEMA",
    "SYSTEM_PROMPT",
    "available",
    "build_user_message",
    "diagnose",
]

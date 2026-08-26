"""The optional LLM layer.

Everything above this module works with no API key. This adds the judgement
call rules cannot make - which of several signals is the cause - and is always
allowed to fail without taking the run down with it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..errors import DiagnosisError
from ..models import Diagnosis, DistilledLog, Fix, JobRef, RuleHit
from .prompt import (
    DIAGNOSIS_SCHEMA,
    SYSTEM_PROMPT,
    build_user_message,
    coerce_category,
    coerce_confidence,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from anthropic import Anthropic

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 8000
DEFAULT_EFFORT = "high"


def available() -> bool:
    """True when the optional ``anthropic`` dependency is installed."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _client(api_key: str | None) -> Anthropic:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - exercised via available()
        raise DiagnosisError(
            "The diagnosis layer needs the `anthropic` package. "
            "Install it with:  pip install 'pipelinemd[llm]'"
        ) from exc
    # A bare client also picks up an `ant auth login` profile, so an unset
    # ANTHROPIC_API_KEY does not necessarily mean "no credentials".
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def _extract_json(content: list[Any]) -> dict[str, Any]:
    """Pull the JSON object out of the response.

    With adaptive thinking on, the first block is a thinking block, so the
    payload is the first *text* block rather than ``content[0]``.
    """
    for block in content:
        if getattr(block, "type", None) == "text":
            text = block.text.strip()
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise DiagnosisError(
                    f"Model returned text that is not JSON: {text[:200]!r}"
                ) from exc
            if not isinstance(parsed, dict):
                raise DiagnosisError(f"Expected a JSON object, got {type(parsed).__name__}")
            return parsed
    raise DiagnosisError("Model response contained no text block.")


def _to_diagnosis(payload: dict[str, Any], model: str, usage: Any) -> Diagnosis:
    fixes = []
    for entry in payload.get("fixes") or []:
        if not isinstance(entry, dict):
            continue
        patch = (entry.get("patch") or "").strip()
        fixes.append(
            Fix(
                title=str(entry.get("title") or "").strip(),
                detail=str(entry.get("detail") or "").strip(),
                patch=patch or None,
            )
        )
    return Diagnosis(
        summary=str(payload.get("summary") or "").strip(),
        root_cause=str(payload.get("root_cause") or "").strip(),
        confidence=coerce_confidence(str(payload.get("confidence") or "low")),
        category=coerce_category(str(payload.get("category") or "script")),
        fixes=tuple(fixes),
        model=model,
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
    )


def diagnose(
    job: JobRef,
    distilled: DistilledLog,
    hits: list[RuleHit],
    *,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    ci_config: str | None = None,
    client: Any | None = None,
) -> Diagnosis:
    """Ask Claude to name the root cause, as validated structured output.

    ``client`` exists so tests can inject a stub; production callers leave it
    unset and let the SDK build one.
    """
    anthropic_client = client if client is not None else _client(api_key)
    user_message = build_user_message(job, distilled, hits, ci_config=ci_config)

    try:
        response = anthropic_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            thinking={"type": "adaptive"},
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": DIAGNOSIS_SCHEMA},
            },
        )
    except DiagnosisError:
        raise
    except Exception as exc:
        raise DiagnosisError(f"Claude request failed: {exc}") from exc

    if getattr(response, "stop_reason", None) == "refusal":
        raise DiagnosisError("Claude declined to analyse this trace.")

    payload = _extract_json(list(response.content))
    return _to_diagnosis(payload, model=model, usage=getattr(response, "usage", None))

"""Ask Claude for the corrected pipeline file, for `diagnose --open-mr`.

The diagnosis's own `patch` is a snippet for a person to paste, not something
a program can apply. A merge request needs the whole file, so this is a
second, narrower call: here is the file, here is what is wrong with it, return
the file with the smallest change that fixes it, or say it cannot be done
safely. Everything it returns is checked by code before it goes anywhere: the
size of the change, a credential check, and GitLab's own CI Lint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..errors import DiagnosisError
from ..models import Diagnosis, DistilledLog, JobRef, Usage
from .claude import (
    DEFAULT_EFFORT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    _client,
    _extract_json,
    _usage,
)

PATCH_SYSTEM_PROMPT = """\
You correct GitLab CI configuration files. You are given a pipeline file, the \
failed job's distilled log, and a diagnosis of why it failed.

Return the complete corrected file in `content`, with the smallest change that \
fixes the diagnosed problem:
- Change only what the fix needs. Keep every other line, comment, key order, \
quoting style, indentation and blank line exactly as it was.
- Do not add jobs, rules, variables, images or includes the fix does not need, \
and do not reformat or tidy anything.
- Never write a credential, token or password into the file.
- If the fix is not an edit to this file - the problem is in an included \
file, a CI/CD variable, the runner, or the project's code - or you cannot \
tell what the right edit is, set `fixable` to false, leave `content` empty, \
and say why in `explanation`. A wrong edit is worse than none: a person \
reviews this as a merge request.

`explanation` is one or two sentences naming what changed and why, for the \
merge request description.
"""

PATCH_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "fixable": {"type": "boolean"},
        "content": {
            "type": "string",
            "description": "The whole corrected file, or an empty string when not fixable.",
        },
        "explanation": {"type": "string"},
    },
    "required": ["fixable", "content", "explanation"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class PatchProposal:
    content: str
    explanation: str
    usage: Usage


def build_patch_message(
    job: JobRef, distilled: DistilledLog, diagnosis: Diagnosis, path: str, config: str
) -> str:
    cited = "\n".join(
        f"{citation.line_number:>6}  {citation.text}" for citation in diagnosis.citations
    )
    fixes = "\n".join(f"- {fix.title}: {fix.detail}" for fix in diagnosis.fixes)
    return "\n".join(
        [
            "# Job",
            f"{job.label} (exit code {distilled.exit_code})",
            "",
            "# Diagnosis",
            diagnosis.summary,
            "",
            diagnosis.root_cause,
            "",
            "# Evidence it cites",
            cited or "(none)",
            "",
            "# Fixes it suggests",
            fixes or "(none)",
            "",
            f"# {path}",
            "```yaml",
            config,
            "```",
            "",
            "Return the corrected file.",
        ]
    )


def propose_patch(
    job: JobRef,
    distilled: DistilledLog,
    diagnosis: Diagnosis,
    path: str,
    config: str,
    *,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    client: Any | None = None,
) -> PatchProposal:
    """The corrected file. Raises DiagnosisError, with usage once billed, if not."""
    anthropic_client = client if client is not None else _client(api_key)
    try:
        response = anthropic_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=PATCH_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": build_patch_message(job, distilled, diagnosis, path, config),
                }
            ],
            thinking={"type": "adaptive"},
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": PATCH_SCHEMA},
            },
        )
    except Exception as exc:
        raise DiagnosisError(f"Claude request failed: {exc}") from exc

    usage = _usage(model, getattr(response, "usage", None))
    if getattr(response, "stop_reason", None) == "refusal":
        raise DiagnosisError("Claude declined to write the fix.", usage=usage)
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise DiagnosisError("The corrected file did not fit in the reply.", usage=usage)
    try:
        payload = _extract_json(list(response.content))
    except DiagnosisError as exc:
        raise DiagnosisError(str(exc), usage=usage) from exc

    explanation = str(payload.get("explanation") or "").strip()
    content = str(payload.get("content") or "")
    if not payload.get("fixable") or not content.strip():
        raise DiagnosisError(
            f"Claude found no safe edit to {path}: {explanation or 'no reason given'}",
            usage=usage,
        )
    return PatchProposal(content=content, explanation=explanation, usage=usage)

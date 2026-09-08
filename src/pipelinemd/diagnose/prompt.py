"""Build the prompt sent to Claude.

The model never sees a raw trace. It sees the distilled evidence, the job's
metadata, and what the deterministic rules already concluded - which keeps the
request small, keeps it cheap, and means the model spends its effort on the
part rules cannot do: deciding which signal is the cause and which is fallout.
"""

from __future__ import annotations

from ..models import Category, Confidence, DistilledLog, JobRef, RuleHit

SYSTEM_PROMPT = """\
You are a CI/CD failure analyst. You are given a distilled excerpt of a failed \
GitLab CI job, plus the output of a deterministic rule engine that already \
scanned it.

Your job is to name the single most likely root cause and give fixes someone \
can act on.

How to work:
- Distinguish cause from fallout. A build that fails because a dependency \
install failed has one root cause, not two. Report the earliest failure that \
explains the rest.
- Treat the rule-engine matches as a strong prior, not as truth. If the \
evidence contradicts a rule, say so and explain why. If the rules found \
nothing, work from the evidence directly.
- Ground every claim in the excerpt. Refer to line numbers as shown.
- Prefer fixes specific to what you can see - the actual package name, the \
actual command, the actual image - over generic advice.
- A `patch` is a concrete snippet the user can paste (a .gitlab-ci.yml \
fragment, a shell command, a config change). Include one only when you can be \
specific; otherwise leave it as an empty string.
- If the excerpt genuinely does not explain the failure, say that plainly and \
set confidence to "low". Do not invent a cause to fill the field.

`«redacted»` marks a credential that pipelinemd masked before sending. Treat \
it as an opaque placeholder; never ask for its value.
"""

DIAGNOSIS_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "One sentence naming the failure. No preamble.",
        },
        "root_cause": {
            "type": "string",
            "description": (
                "Two to four sentences explaining why the job failed and how the "
                "evidence supports it. Cite line numbers where useful."
            ),
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "category": {
            "type": "string",
            "enum": [category.value for category in Category],
        },
        "fixes": {
            "type": "array",
            "description": "Ordered most-likely-to-work first. One to four entries.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Imperative, one line."},
                    "detail": {
                        "type": "string",
                        "description": "Why this fixes it, and any caveat.",
                    },
                    "patch": {
                        "type": "string",
                        "description": (
                            "A pasteable snippet, or an empty string when no "
                            "specific snippet applies."
                        ),
                    },
                },
                "required": ["title", "detail", "patch"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "root_cause", "confidence", "category", "fixes"],
    "additionalProperties": False,
}


def _job_block(job: JobRef) -> str:
    rows = [
        ("job", job.label),
        ("project", job.project),
        ("stage", job.stage),
        ("status", job.status),
        ("ref", job.ref),
        ("commit", (job.sha or "")[:12] or None),
        ("duration", f"{job.duration_s:.0f}s" if job.duration_s else None),
        ("allow_failure", "yes" if job.allow_failure else None),
    ]
    return "\n".join(f"{name}: {value}" for name, value in rows if value)


def _environment_block(distilled: DistilledLog) -> str:
    rows = [
        ("exit code", distilled.exit_code),
        ("runner verdict", distilled.failure_reason),
        ("runner", distilled.runner),
        ("image", distilled.image),
    ]
    lines = [f"{name}: {value}" for name, value in rows if value is not None]

    failing = distilled.failing_section
    if failing:
        lines.append(f"section still open when the job died: {failing.name}")
    slow = sorted(
        (s for s in distilled.sections if s.duration_s),
        key=lambda s: -(s.duration_s or 0),
    )[:4]
    if slow:
        timings = ", ".join(f"{s.name}={s.duration_s:.0f}s" for s in slow)
        lines.append(f"section timings: {timings}")
    return "\n".join(lines)


def _rules_block(hits: list[RuleHit]) -> str:
    if not hits:
        return "The rule engine matched nothing. Work from the evidence alone."
    lines = []
    for hit in hits:
        lines.append(
            f"- [{hit.rule.id}] {hit.rule.title} "
            f"(confidence {hit.rule.confidence.value}, matched line {hit.line_number})\n"
            f"  matched: {hit.line_text.strip()[:200]}\n"
            f"  rule says: {hit.rule.explanation}"
        )
    return "\n".join(lines)


def build_user_message(
    job: JobRef,
    distilled: DistilledLog,
    hits: list[RuleHit],
    *,
    ci_config: str | None = None,
    max_rules: int = 5,
) -> str:
    """Assemble the single user turn describing the failure."""
    stats = distilled.stats
    sections = [
        "# Job",
        _job_block(job),
        "",
        "# Environment",
        _environment_block(distilled) or "(nothing recorded)",
        "",
        "# Rule engine findings",
        _rules_block(hits[:max_rules]),
        "",
        "# Commands the job ran",
        "\n".join(f"$ {command}" for command in distilled.commands[:40]) or "(none captured)",
        "",
        "# Distilled evidence",
        (
            f"({stats.evidence_lines} lines kept from {stats.raw_lines}; "
            f"gaps are marked. Numbers are cleaned-trace line numbers.)"
        ),
        "```",
        distilled.evidence_text() or "(no evidence extracted)",
        "```",
    ]

    if ci_config:
        trimmed = ci_config if len(ci_config) <= 8000 else ci_config[:8000] + "\n# … truncated"
        sections += ["", "# .gitlab-ci.yml", "```yaml", trimmed, "```"]

    sections += [
        "",
        "Diagnose this failure.",
    ]
    return "\n".join(sections)


def coerce_confidence(value: str) -> Confidence:
    try:
        return Confidence(value.strip().lower())
    except ValueError:
        return Confidence.LOW


def coerce_category(value: str) -> Category:
    try:
        return Category(value.strip().lower())
    except ValueError:
        return Category.SCRIPT

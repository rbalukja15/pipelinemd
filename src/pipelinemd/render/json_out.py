"""JSON report - the machine-readable contract for other tooling."""

from __future__ import annotations

import json
from typing import Any

from ..assessment import REVIEW_BELOW, Assessment, assess
from ..cost import PRICES_AS_OF, PRICING_SOURCE, Cost, call_cost, cost_of, run_cost
from ..models import Classification, Report, Usage
from ..taxonomy import class_of, classify

#: Bumped only for a change that breaks an existing reader. Adding a key is
#: not one: `classification` and the per-hit `failure_class` arrived in #17,
#: and `assessment` and `cost` in #18, without a bump, because a reader that
#: ignores unknown keys is unaffected.
SCHEMA_VERSION = 1


def classification_to_dict(classification: Classification) -> dict[str, Any]:
    retry = classification.retry
    return {
        "failure_class": classification.failure_class.value,
        "fix_type": classification.fix_type.value if classification.fix_type else None,
        "confidence": classification.confidence.value,
        "basis": classification.basis,
        "rule_id": classification.rule_id,
        "source": classification.source,
        "retry": (
            {
                "attempts": retry.attempts,
                "statuses": list(retry.statuses),
                "verdict": retry.verdict.value,
            }
            if retry is not None
            else None
        ),
    }


def assessment_to_dict(assessment: Assessment) -> dict[str, Any]:
    return {
        "confidence": assessment.confidence.value,
        "needs_review": assessment.needs_review,
        # What `needs_review` was measured against, so a reader holding the
        # confidence can tell which side of the bar it fell.
        "review_below": REVIEW_BELOW.value,
        "reasons": list(assessment.reasons),
    }


def usage_to_dict(usage: Usage) -> dict[str, Any]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation_input_tokens": usage.cache_creation_input_tokens,
        "cache_read_input_tokens": usage.cache_read_input_tokens,
    }


def _usd(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def cost_to_dict(cost: Cost) -> dict[str, Any]:
    return {
        # An estimate from list prices; null when a model has no price on file.
        "estimated_usd": _usd(cost.usd),
        "prices_as_of": PRICES_AS_OF,
        "pricing_source": PRICING_SOURCE,
        "unpriced_models": list(cost.unpriced_models),
        # Summed over the calls, and the input total counts cached tokens too:
        # everything the model was shown. Per call, the API's own split.
        "total_input_tokens": cost.input_tokens,
        "total_output_tokens": cost.output_tokens,
        "discarded_calls": cost.discarded,
        "calls": [
            {"model": call.model, **usage_to_dict(call), "estimated_usd": _usd(call_cost(call))}
            for call in cost.calls
        ],
    }


def report_to_dict(report: Report) -> dict[str, Any]:
    job = report.job
    distilled = report.distilled
    stats = distilled.stats

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "job": {
            "name": job.name,
            "id": job.id,
            "stage": job.stage,
            "status": job.status,
            "url": job.url,
            "project": job.project,
            "pipeline_id": job.pipeline_id,
            "ref": job.ref,
            "sha": job.sha,
            "duration_s": job.duration_s,
            "allow_failure": job.allow_failure,
            "source": job.source,
        },
        "trace": {
            "exit_code": distilled.exit_code,
            "failure_reason": distilled.failure_reason,
            "runner": distilled.runner,
            "image": distilled.image,
            "commands": distilled.commands,
            "failing_section": (
                distilled.failing_section.name if distilled.failing_section else None
            ),
            "sections": [
                {
                    "name": section.name,
                    "start_line": section.start_line,
                    "end_line": section.end_line,
                    "duration_s": section.duration_s,
                }
                for section in distilled.sections
            ],
            "stats": {
                "raw_bytes": stats.raw_bytes,
                "raw_lines": stats.raw_lines,
                "clean_lines": stats.clean_lines,
                "evidence_lines": stats.evidence_lines,
                "evidence_chars": stats.evidence_chars,
                "reduction": round(stats.reduction, 4),
            },
        },
        "evidence": [
            {
                "label": block.label,
                "start": block.start,
                "end": block.end,
                "lines": [
                    {
                        "number": line.number,
                        "text": line.text,
                        "section": line.section,
                        "is_anchor": line.is_anchor,
                        "repeat": line.repeat,
                    }
                    for line in block.lines
                ],
            }
            for block in distilled.evidence
        ],
        "rule_hits": [
            {
                "id": hit.rule.id,
                "title": hit.rule.title,
                "category": hit.rule.category.value,
                "failure_class": class_of(hit.rule).value,
                "confidence": hit.rule.confidence.value,
                "line_number": hit.line_number,
                "line_text": hit.line_text,
                "score": hit.score,
                "in_evidence": hit.in_evidence,
                "explanation": hit.rule.explanation,
                "fixes": list(hit.rule.fixes),
                "docs": list(hit.rule.docs),
            }
            for hit in report.hits
        ],
        "classification": classification_to_dict(classify(report)),
        "assessment": assessment_to_dict(assess(report)),
        "cost": cost_to_dict(cost_of(report)),
        "diagnosis": None,
    }

    if diagnosis := report.diagnosis:
        payload["diagnosis"] = {
            "summary": diagnosis.summary,
            "root_cause": diagnosis.root_cause,
            "confidence": diagnosis.confidence.value,
            "category": diagnosis.category.value,
            "failure_class": diagnosis.failure_class.value,
            "fixes": [
                {"title": fix.title, "detail": fix.detail, "patch": fix.patch}
                for fix in diagnosis.fixes
            ],
            "citations": [
                {
                    "line_number": citation.line_number,
                    "text": citation.text,
                    "section": citation.section,
                    "repeat": citation.repeat,
                }
                for citation in diagnosis.citations
            ],
            "unresolved_citations": list(diagnosis.unresolved_citations),
            "fully_grounded": diagnosis.fully_grounded,
            "model": diagnosis.model,
            "usage": usage_to_dict(diagnosis.usage),
        }
    return payload


def run_to_dict(reports: list[Report]) -> dict[str, Any]:
    """Several reports, with the cost of the run as a whole beside them.

    The run total pools every call, so it is `null` if any report's is - the
    same rule as within a report. Without it, the obvious consumer adds up the
    per-report figures it can read and gets a partial sum that looks whole.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "reports": [report_to_dict(report) for report in reports],
        "cost": cost_to_dict(run_cost(reports)),
    }


def render_json(report: Report, *, indent: int | None = 2) -> str:
    return json.dumps(report_to_dict(report), indent=indent, ensure_ascii=False) + "\n"

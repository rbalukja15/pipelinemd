"""JSON report - the machine-readable contract for other tooling."""

from __future__ import annotations

import json
from typing import Any

from ..models import Report

SCHEMA_VERSION = 1


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
        "diagnosis": None,
    }

    if diagnosis := report.diagnosis:
        payload["diagnosis"] = {
            "summary": diagnosis.summary,
            "root_cause": diagnosis.root_cause,
            "confidence": diagnosis.confidence.value,
            "category": diagnosis.category.value,
            "fixes": [
                {"title": fix.title, "detail": fix.detail, "patch": fix.patch}
                for fix in diagnosis.fixes
            ],
            "model": diagnosis.model,
            "usage": {
                "input_tokens": diagnosis.input_tokens,
                "output_tokens": diagnosis.output_tokens,
            },
        }
    return payload


def render_json(report: Report, *, indent: int | None = 2) -> str:
    return json.dumps(report_to_dict(report), indent=indent, ensure_ascii=False) + "\n"

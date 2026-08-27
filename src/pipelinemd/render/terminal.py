"""Human-facing terminal report."""

from __future__ import annotations

from ..models import Confidence, EvidenceLine, Report, RuleHit
from .evidence import gap_before, select_display_lines
from .style import Style

_CONFIDENCE_MARK = {Confidence.HIGH: "●", Confidence.MEDIUM: "◐", Confidence.LOW: "○"}


def _colour_for(style: Style, confidence: Confidence) -> object:
    return {
        Confidence.HIGH: style.red,
        Confidence.MEDIUM: style.yellow,
        Confidence.LOW: style.dim,
    }[confidence]


def _wrap(text: str, width: int, indent: str = "") -> list[str]:
    """Greedy wrap that keeps existing newlines as paragraph breaks."""
    out: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            out.append("")
            continue
        line = indent
        for word in words:
            candidate = word if line == indent else f"{line} {word}"
            if len(candidate) > width and line != indent:
                out.append(line)
                line = f"{indent}{word}"
            else:
                line = candidate if line != indent else f"{indent}{word}"
        out.append(line)
    return out


def _header(report: Report, style: Style) -> list[str]:
    job = report.job
    distilled = report.distilled
    lines = [style.bold(f"pipelinemd  {job.label}")]

    facts: list[str] = []
    if job.project:
        facts.append(job.project)
    if job.ref:
        facts.append(f"ref {job.ref}")
    if distilled.exit_code is not None:
        facts.append(f"exit code {distilled.exit_code}")
    if job.duration_s:
        facts.append(f"{job.duration_s:.0f}s")
    if job.allow_failure:
        facts.append("allow_failure")
    if facts:
        lines.append(style.dim("  " + " · ".join(facts)))
    if job.url:
        lines.append(style.dim(f"  {job.url}"))
    if distilled.failure_reason:
        lines.append("  " + style.red(distilled.failure_reason))
    return lines


def _diagnosis(report: Report, style: Style, width: int) -> list[str]:
    diagnosis = report.diagnosis
    if diagnosis is None:
        return []
    colour = _colour_for(style, diagnosis.confidence)
    lines = [
        "",
        style.heading("Diagnosis")
        + style.dim(f"   {diagnosis.confidence.value} confidence · {diagnosis.category.value}"),
        "",
        "  " + colour(diagnosis.summary),  # type: ignore[operator]
    ]
    if diagnosis.root_cause:
        lines.append("")
        lines.extend(_wrap(diagnosis.root_cause, width, indent="  "))

    if diagnosis.fixes:
        lines += ["", style.heading("Suggested fixes")]
        for index, fix in enumerate(diagnosis.fixes, start=1):
            lines.append(f"  {style.bold(f'{index}.')} {style.bold(fix.title)}")
            if fix.detail:
                lines.extend(_wrap(fix.detail, width, indent="     "))
            if fix.patch:
                lines.append("")
                for patch_line in fix.patch.splitlines():
                    lines.append(style.green(f"       {patch_line}"))
            lines.append("")
    return lines


def _rules(hits: list[RuleHit], style: Style, limit: int) -> list[str]:
    if not hits:
        return ["", style.heading("Rule matches"), style.dim("  none")]
    lines = ["", style.heading("Rule matches")]
    width = max(len(hit.rule.id) for hit in hits[:limit])
    for hit in hits[:limit]:
        mark = _CONFIDENCE_MARK[hit.rule.confidence]
        colour = _colour_for(style, hit.rule.confidence)
        lines.append(
            f"  {colour(mark)} {style.bold(hit.rule.id.ljust(width))}  "  # type: ignore[operator]
            f"{hit.rule.title}  {style.dim(f'L{hit.line_number}')}"
        )
    if len(hits) > limit:
        lines.append(style.dim(f"  … and {len(hits) - limit} more (--all-rules to show)"))
    return lines


def _fixes_from_rules(report: Report, style: Style, width: int) -> list[str]:
    """When there is no LLM diagnosis, the top rule's fixes are the advice."""
    top = report.top_hit
    if report.diagnosis is not None or top is None:
        return []
    lines = ["", style.heading(f"What {top.rule.id} suggests"), ""]
    lines.extend(_wrap(top.rule.explanation, width, indent="  "))
    lines.append("")
    for fix in top.rule.fixes:
        wrapped = _wrap(fix, width, indent="     ")
        if wrapped:
            wrapped[0] = "   " + style.bold("→") + wrapped[0][3:]
        lines.extend(wrapped)
    if top.rule.docs:
        lines.append("")
        for doc in top.rule.docs:
            lines.append(style.dim(f"   docs: {doc}"))
    return lines


def _evidence(report: Report, style: Style, limit: int) -> list[str]:
    distilled = report.distilled
    stats = distilled.stats
    shown, dropped = select_display_lines(distilled, limit)

    header = style.heading("Evidence") + style.dim(
        f"   {stats.evidence_lines} of {stats.raw_lines} lines · {stats.reduction:.1%} reduced"
    )
    lines = ["", header]
    if dropped:
        lines.append(style.dim(f"       showing the {len(shown)} most relevant; {dropped} hidden"))

    previous: EvidenceLine | None = None
    for line in shown:
        if gap := gap_before(previous, line):
            lines.append(style.dim(f"       … {gap} lines omitted …"))
        number = style.dim(f"{line.number:>6}")
        text = style.red(line.text) if line.is_anchor else line.text
        suffix = style.dim(f"  [x{line.repeat}]") if line.collapsed else ""
        lines.append(f"{number}  {text}{suffix}")
        previous = line
    return lines


def render_terminal(
    report: Report,
    style: Style,
    *,
    width: int = 88,
    rule_limit: int = 5,
    evidence_limit: int = 40,
) -> str:
    """The default report: header, diagnosis, rules, evidence."""
    lines: list[str] = []
    lines += _header(report, style)
    lines += _diagnosis(report, style, width)
    lines += _fixes_from_rules(report, style, width)
    lines += _rules(report.hits, style, rule_limit)
    lines += _evidence(report, style, evidence_limit)
    lines.append("")
    return "\n".join(lines)

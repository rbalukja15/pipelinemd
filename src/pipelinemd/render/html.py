"""A self-contained HTML report: one file, no network, no dependencies.

GitLab keeps it as a job artifact and serves it at a stable URL, so a failed
pipeline links straight to its diagnosis. It also has to work opened from a
laptop's downloads folder or attached to a merge request, which is why
everything is inline: the styles are in the page, there are no fonts, images
or scripts to fetch, and nothing breaks offline.

Every string that came from a job is escaped. The text has already been
redacted by the distiller, so this adds no new exposure; escaping makes sure a
log line containing markup is shown as text rather than run as part of the
page.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from .. import __version__
from ..assessment import assess
from ..cost import cost_of, describe_cost, run_cost
from ..models import Confidence, EvidenceLine, Report
from ..taxonomy import classify, model_disagreement
from .evidence import gap_before, select_display_lines

PROJECT_URL = "https://github.com/rbalukja15/pipelinemd"

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --border: #d1d9e0;
  --panel: #f6f8fa; --code-bg: #f6f8fa;
  --high: #cf222e; --medium: #9a6700; --low: #59636e;
  --warn-bg: #fff8c5; --warn-border: #d4a72c;
  --anchor-fg: #cf222e; --cited-bg: #ddf4ff; --target-bg: #fff8c5;
  --link: #0969da;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --border: #3d444d;
    --panel: #151b23; --code-bg: #151b23;
    --high: #ff7b72; --medium: #d29922; --low: #9198a1;
    --warn-bg: #2b2111; --warn-border: #9e6a03;
    --anchor-fg: #ff7b72; --cited-bg: #0c2d6b; --target-bg: #4b3a0b;
    --link: #4493f8;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}
main { max-width: 64rem; margin: 0 auto; padding: 2rem 1rem 3rem; }
a { color: var(--link); }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.15rem; margin: 1.75rem 0 .5rem; }
h3 { font-size: 1rem; margin: 1.25rem 0 .5rem; }
code, pre, .log { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
code { font-size: .9em; background: var(--code-bg); padding: .1em .3em; border-radius: 4px; }
pre { background: var(--code-bg); padding: .75rem; border-radius: 6px; overflow-x: auto; }
.report { border-top: 1px solid var(--border); padding-top: 1rem; margin-top: 2rem; }
.report:first-of-type { border-top: 0; margin-top: 0; padding-top: 0; }
.facts, .meta, .footer { color: var(--muted); }
.facts span + span::before { content: " · "; }
.reason { color: var(--high); font-weight: 600; }
.summary-grid {
  display: grid; grid-template-columns: max-content 1fr; gap: .25rem 1rem;
  background: var(--panel); border: 1px solid var(--border); border-radius: 6px;
  padding: .75rem 1rem; margin: 1rem 0;
}
.summary-grid dt { color: var(--muted); }
.summary-grid dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
p, li { overflow-wrap: anywhere; }
.banner {
  border: 1px solid var(--warn-border); background: var(--warn-bg);
  border-radius: 6px; padding: .6rem 1rem; margin: 1rem 0;
}
.banner.info { border-color: var(--border); background: var(--panel); }
.level-high { color: var(--high); }
.level-medium { color: var(--medium); }
.level-low { color: var(--low); }
ol.fixes > li { margin-bottom: .75rem; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: .35rem .5rem; border-bottom: 1px solid var(--border); }
th { color: var(--muted); font-weight: 600; }
.log {
  font-size: 13px; line-height: 1.45; background: var(--code-bg);
  border: 1px solid var(--border); border-radius: 6px; padding: .5rem 0; overflow-x: auto;
}
.line { display: flex; padding: 0 .75rem; scroll-margin-top: 4rem; }
.line .ln {
  flex: 0 0 auto; min-width: 4.5rem; padding-right: 1rem; text-align: right;
  color: var(--muted); text-decoration: none; user-select: none;
}
.line .text { white-space: pre-wrap; word-break: break-word; }
.line.anchor .text { color: var(--anchor-fg); }
.line.cited { background: var(--cited-bg); }
.line:target { background: var(--target-bg); outline: 2px solid var(--warn-border); }
.repeat { color: var(--muted); padding-left: .75rem; white-space: nowrap; }
.gap { color: var(--muted); padding: 0 .75rem 0 6.25rem; font-style: italic; }
.run-total { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--border); }
@media print {
  :root {
    --bg: #fff; --fg: #000; --muted: #444; --panel: #fff; --code-bg: #fff;
    --cited-bg: #eee; --target-bg: #fff;
  }
  main { max-width: none; padding: 0; }
  a { color: inherit; }
  .log { overflow: visible; }
  .report { break-before: page; }
  .report:first-of-type { break-before: auto; }
}
"""


def _e(text: object) -> str:
    return escape(str(text), quote=True)


def _href(url: str) -> str | None:
    """Only web links become links: a `javascript:` URL in a job is text."""
    return _e(url) if url.lower().startswith(("https://", "http://")) else None


def _level(confidence: Confidence | str) -> str:
    value = confidence.value if isinstance(confidence, Confidence) else str(confidence)
    return f'<span class="level-{_e(value)}">{_e(value)}</span>'


def _line_id(prefix: str, number: int) -> str:
    return f"{prefix}L{number}"


def _header(report: Report) -> list[str]:
    job = report.job
    distilled = report.distilled
    out = [f"<h1>{_e(job.label)}</h1>"]
    facts: list[str] = []
    if job.project:
        facts.append(_e(job.project))
    if job.ref:
        facts.append(f"ref <code>{_e(job.ref)}</code>")
    if distilled.exit_code is not None:
        facts.append(f"exit code <code>{distilled.exit_code}</code>")
    if job.duration_s:
        facts.append(f"{job.duration_s:.0f}s")
    if job.allow_failure:
        facts.append("allow_failure")
    if job.url and (href := _href(job.url)):
        facts.append(f'<a href="{href}">view job</a>')
    if facts:
        out.append('<p class="facts">' + "".join(f"<span>{f}</span>" for f in facts) + "</p>")
    if distilled.failure_reason:
        out.append(f'<p class="reason">{_e(distilled.failure_reason)}</p>')
    return out


def _summary(report: Report) -> list[str]:
    classification = classify(report)
    assessment = assess(report)
    fix = f" → <code>{_e(classification.fix_type)}</code>" if classification.fix_type else ""
    rows = [
        ("class", f"<code>{_e(classification.failure_class)}</code>{fix}"),
        ("basis", f"{_level(classification.confidence)} · {_e(classification.basis)}"),
        ("confidence", _level(assessment.confidence)),
        ("cost", _e(describe_cost(cost_of(report)))),
    ]
    out = ['<dl class="summary-grid">']
    out += [f"<dt>{label}</dt><dd>{value}</dd>" for label, value in rows]
    out.append("</dl>")
    if assessment.needs_review:
        reasons = "".join(f"<li>{_e(reason)}</li>" for reason in assessment.reasons)
        out.append(
            '<div class="banner review" role="note"><strong>Needs human review.</strong> '
            f"Confidence is {_e(assessment.confidence)} because:<ul>{reasons}</ul></div>"
        )
    elif assessment.reasons:
        reasons = "; ".join(_e(reason) for reason in assessment.reasons)
        out.append(f'<p class="meta">Confidence is {_e(assessment.confidence)}: {reasons}.</p>')
    return out


def _diagnosis(report: Report, prefix: str) -> list[str]:
    diagnosis = report.diagnosis
    if diagnosis is None:
        return []
    other = model_disagreement(report)
    meta = f"{_level(diagnosis.confidence)} confidence · {_e(diagnosis.category.value)}"
    if other:
        meta += f" · the model reads it as <code>{_e(other)}</code>"
    out = [
        "<h2>Diagnosis</h2>",
        f'<p class="meta">{meta}</p>',
        f"<p><strong>{_e(diagnosis.summary)}</strong></p>",
    ]
    out += [
        f"<p>{_e(paragraph)}</p>" for paragraph in diagnosis.root_cause.split("\n\n") if paragraph
    ]
    if diagnosis.citations:
        out.append("<h3>Cited evidence</h3>")
        out.append('<div class="log">')
        for citation in diagnosis.citations:
            target = _line_id(prefix, citation.line_number)
            repeat = (
                f'<span class="repeat">×{citation.repeat}</span>' if citation.repeat > 1 else ""
            )
            out.append(
                f'<div class="line"><a class="ln" href="#{target}">{citation.line_number}</a>'
                f'<span class="text">{_e(citation.text)}</span>{repeat}</div>'
            )
        out.append("</div>")
    if diagnosis.unresolved_citations:
        invented = ", ".join(f"L{n}" for n in diagnosis.unresolved_citations)
        out.append(
            '<div class="banner" role="note">This diagnosis also cited '
            f"<strong>{_e(invented)}</strong>, which is not in the evidence it was shown. "
            "Treat the claims around it with suspicion.</div>"
        )
    if diagnosis.fixes:
        out.append("<h3>Suggested fixes</h3>")
        out.append('<ol class="fixes">')
        for fix in diagnosis.fixes:
            item = f"<strong>{_e(fix.title)}</strong>"
            if fix.detail:
                item += f"<br>{_e(fix.detail)}"
            if fix.patch:
                item += f"<pre><code>{_e(fix.patch)}</code></pre>"
            out.append(f"<li>{item}</li>")
        out.append("</ol>")
    return out


def _rules_only(report: Report) -> list[str]:
    """No diagnosis: say why, then let the top rule's advice stand in."""
    if report.diagnosis is not None:
        return []
    if report.discarded_calls:
        note = (
            "<strong>No diagnosis.</strong> The model was asked, but its answer was "
            "not usable: refused, unreadable, or citing nothing in the evidence it was "
            "shown. These are the rules' findings alone."
        )
    else:
        note = "<strong>Rules only.</strong> No model was asked; these are the rules' findings."
    out = [f'<div class="banner info" role="note">{note}</div>']
    top = report.top_hit
    if top is None:
        out.append("<p>No rule in the catalog matched this log.</p>")
        return out
    out += [
        f"<h2>{_e(top.rule.title)}</h2>",
        f'<p class="meta">{_level(top.rule.confidence)} · matched '
        f"<code>{_e(top.rule.id)}</code> at line {top.line_number}</p>",
        f"<p>{_e(top.rule.explanation)}</p>",
        "<h3>Try this</h3>",
        "<ul>" + "".join(f"<li>{_e(fix)}</li>" for fix in top.rule.fixes) + "</ul>",
    ]
    if top.rule.docs:
        links = "".join(
            f'<li><a href="{href}">{_e(doc)}</a></li>'
            if (href := _href(doc))
            else f"<li>{_e(doc)}</li>"
            for doc in top.rule.docs
        )
        out.append(f'<p class="meta">Docs:</p><ul>{links}</ul>')
    return out


def _rules(report: Report, limit: int) -> list[str]:
    out = ["<h2>Rule matches</h2>"]
    if not report.hits:
        return [*out, '<p class="meta">None.</p>']
    out.append('<div class="table-wrap"><table><thead><tr><th>rule</th><th>title</th><th>line</th>')
    out.append("<th>confidence</th></tr></thead><tbody>")
    for hit in report.hits[:limit]:
        out.append(
            f"<tr><td><code>{_e(hit.rule.id)}</code></td><td>{_e(hit.rule.title)}</td>"
            f"<td>{hit.line_number}</td><td>{_level(hit.rule.confidence)}</td></tr>"
        )
    out.append("</tbody></table></div>")
    if len(report.hits) > limit:
        out.append(
            f'<p class="meta">… and {len(report.hits) - limit} more (--all-rules to show).</p>'
        )
    return out


def _evidence(report: Report, prefix: str, limit: int) -> list[str]:
    distilled = report.distilled
    stats = distilled.stats
    shown, dropped = select_display_lines(distilled, limit)
    cited = (
        {citation.line_number for citation in report.diagnosis.citations}
        if report.diagnosis
        else set()
    )
    out = [
        "<h2>Evidence</h2>",
        f'<p class="meta">{stats.evidence_lines:,} of {stats.raw_lines:,} lines kept · '
        f"{stats.reduction:.1%} reduced"
        + (f" · showing the {len(shown)} most relevant, {dropped} hidden" if dropped else "")
        + "</p>",
    ]
    if not shown:
        return [*out, '<p class="meta">No evidence lines.</p>']
    out.append('<div class="log">')
    previous: EvidenceLine | None = None
    for line in shown:
        if gap := gap_before(previous, line):
            out.append(f'<div class="gap">… {gap:,} lines omitted …</div>')
        classes = ["line"]
        if line.is_anchor:
            classes.append("anchor")
        if line.number in cited:
            classes.append("cited")
        line_id = _line_id(prefix, line.number)
        repeat = f'<span class="repeat">×{line.repeat}</span>' if line.collapsed else ""
        out.append(
            f'<div class="{" ".join(classes)}" id="{line_id}">'
            f'<a class="ln" href="#{line_id}">{line.number}</a>'
            f'<span class="text">{_e(line.text)}</span>{repeat}</div>'
        )
        previous = line
    out.append("</div>")
    return out


def _report_section(report: Report, prefix: str, rule_limit: int, evidence_limit: int) -> str:
    section_id = prefix.rstrip("-") or "report"
    parts: list[str] = [f'<section class="report" id="{section_id}">']
    parts += _header(report)
    parts += _summary(report)
    parts += _diagnosis(report, prefix)
    parts += _rules_only(report)
    parts += _rules(report, rule_limit)
    parts += _evidence(report, prefix, evidence_limit)
    parts.append("</section>")
    return "\n".join(parts)


def render_html(
    reports: Sequence[Report],
    *,
    rule_limit: int = 5,
    evidence_limit: int = 200,
) -> str:
    """One standalone page for one report, or for every report of a run.

    Line anchors are ``#L<n>`` on a single report. With several, each report's
    lines are prefixed (``#j2-L<n>``) so the same line number in two jobs
    stays two different targets, and each report is a section (``#j2``).
    """
    several = len(reports) != 1
    if several:
        title = f"pipelinemd · {len(reports)} failed jobs"
    elif reports:
        title = f"pipelinemd · {reports[0].job.label}"
    else:
        title = "pipelinemd"

    body: list[str] = []
    if several:
        body.append(f"<h1>{len(reports)} failed jobs</h1>")
        body.append("<ul>")
        for index, report in enumerate(reports, start=1):
            body.append(f'<li><a href="#j{index}">{_e(report.job.label)}</a></li>')
        body.append("</ul>")
    for index, report in enumerate(reports, start=1):
        prefix = f"j{index}-" if several else ""
        body.append(_report_section(report, prefix, rule_limit, evidence_limit))
    if several:
        body.append(
            f'<p class="run-total"><strong>Run total</strong> · {len(reports)} jobs · '
            f"{_e(describe_cost(run_cost(list(reports))))}</p>"
        )
    model = next((r.diagnosis.model for r in reports if r.diagnosis and r.diagnosis.model), "")
    footer = f'Generated by <a href="{PROJECT_URL}">pipelinemd</a> {_e(__version__)}'
    if model:
        footer += f" · diagnosis by <code>{_e(model)}</code>"
    body.append(f'<p class="footer">{footer}</p>')

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            # No referrer leaves the page when someone follows a job link.
            '<meta name="referrer" content="no-referrer">',
            # Nothing may load from anywhere: the page is complete as written.
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            "style-src 'unsafe-inline'; img-src data:\">",
            f"<title>{_e(title)}</title>",
            f"<style>{_CSS}</style>",
            "</head>",
            "<body>",
            "<main>",
            *body,
            "</main>",
            "</body>",
            "</html>",
            "",
        ]
    )

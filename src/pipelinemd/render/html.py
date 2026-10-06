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

import re
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

from .. import __version__
from ..assessment import assess
from ..cost import cost_of, describe_cost, run_cost
from ..distill.trace import POST_JOB_SECTION
from ..models import Confidence, EvidenceLine, Report
from ..rules.engine import CLEANUP_SECTIONS
from ..taxonomy import classify, model_disagreement
from .evidence import gap_before, select_display_lines

PROJECT_URL = "https://github.com/rbalukja15/pipelinemd"

#: Phases that only run because the job already failed, or after its verdict.
#: The rule engine scores hits in them down for the same reason.
AFTER_FAILURE_SECTIONS = CLEANUP_SECTIONS | {POST_JOB_SECTION}

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --border: #d1d9e0;
  --panel: #f6f8fa; --code-bg: #f6f8fa;
  --high: #cf222e; --medium: #9a6700; --low: #59636e;
  --warn-bg: #fff8c5; --warn-border: #d4a72c;
  --anchor-fg: #cf222e; --cited-bg: #ddf4ff; --target-bg: #fff8c5;
  --link: #0969da; --ok: #1a7f37;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --border: #3d444d;
    --panel: #151b23; --code-bg: #151b23;
    --high: #ff7b72; --medium: #d29922; --low: #9198a1;
    --warn-bg: #2b2111; --warn-border: #9e6a03;
    --anchor-fg: #ff7b72; --cited-bg: #0c2d6b; --target-bg: #4b3a0b;
    --link: #4493f8; --ok: #3fb950;
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
.card {
  border: 1px solid var(--border); border-left: 4px solid var(--low);
  border-radius: 6px; padding: .75rem 1rem; margin: 1rem 0; background: var(--panel);
}
.card.level-border-high { border-left-color: var(--ok); }
.card.level-border-medium { border-left-color: var(--medium); }
.card.level-border-low { border-left-color: var(--high); }
.card-title { font-size: 1.15rem; font-weight: 600; margin: 0 0 .5rem; }
.card .summary-grid { background: none; border: 0; padding: 0; margin: 0; }
span.invented { text-decoration: underline wavy var(--warn-border); }
.line.invented .text { color: var(--medium); font-style: italic; }
.line.hit .ln { font-weight: 700; color: var(--fg); }
ol.timeline { list-style: none; padding: 0; margin: 0; }
.step {
  display: grid; grid-template-columns: minmax(8rem, 14rem) 1fr 3.5rem minmax(6rem, 12rem);
  gap: .75rem; align-items: center; padding: .15rem 0;
}
.step .name { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px; overflow-wrap: anywhere; }
.step .bar { height: .6rem; background: var(--code-bg); border-radius: 3px; overflow: hidden; }
.step .bar > span { display: block; height: 100%; background: var(--low); }
.step.failed .bar > span { background: var(--high); }
.step.failed .name, .step.failed .note { color: var(--high); font-weight: 600; }
.step.after { opacity: .7; }
.step .took { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
.step .where { color: var(--muted); font-size: .9em; }
@media (max-width: 40rem) {
  .step { grid-template-columns: 1fr 3rem; }
  .step .bar, .step .where { grid-column: 1 / -1; }
}
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


@dataclass(frozen=True, slots=True)
class _Page:
    """What every part of one report needs to link into its evidence."""

    prefix: str
    #: Line numbers the evidence view shows. Only these can be linked to: a
    #: link to a line the page does not contain would go nowhere.
    shown: frozenset[int]

    def line_id(self, number: int) -> str:
        return _line_id(self.prefix, number)

    def line_link(self, number: int, text: str | None = None) -> str:
        label = _e(text if text is not None else number)
        if number in self.shown:
            return f'<a href="#{self.line_id(number)}">{label}</a>'
        return label


def _failing_section(report: Report) -> str | None:
    """Where the job failed: the top rule's section, else one that never closed."""
    distilled = report.distilled
    if top := report.top_hit:
        section = next(
            (line.section for line in distilled.lines if line.number == top.line_number), None
        )
        if section:
            return section
    return next((s.name for s in distilled.sections if s.failed_open), None)


def _timeline(report: Report, page: _Page) -> list[str]:
    sections = report.distilled.sections
    # GitLab's sections are timed. A GitHub log only has the untimed post-job
    # span pipelinemd marks itself, and a timeline of one bar says nothing.
    if not any(section.duration_s is not None for section in sections):
        return []
    failing = _failing_section(report)
    total = sum(s.duration_s or 0.0 for s in sections)
    # The first displayed evidence line in each section, to jump to.
    first_shown: dict[str, int] = {}
    for line in report.distilled.lines:
        if line.section and line.number in page.shown:
            first_shown.setdefault(line.section, line.number)
    # Where it failed, jump to the line that says why rather than the first.
    top = report.top_hit
    if failing and top is not None and top.line_number in page.shown:
        first_shown[failing] = top.line_number

    out = ["<h2>Timeline</h2>", '<ol class="timeline">']
    for section in sections:
        classes = ["step"]
        notes: list[str] = []
        if section.name == failing:
            classes.append("failed")
            notes.append("failed here")
        elif section.name in AFTER_FAILURE_SECTIONS:
            classes.append("after")
            notes.append("after the failure")
        if section.failed_open:
            notes.append("never finished")
        duration = section.duration_s
        width = 0.0 if not total or duration is None else max(0.5, 100 * duration / total)
        took = "—" if duration is None else f"{duration:.0f}s"
        jump = (
            f' <a class="jump" href="#{page.line_id(first)}">L{first}</a>'
            if (first := first_shown.get(section.name)) is not None
            else ""
        )
        note = f' <span class="note">{_e(", ".join(notes))}</span>' if notes else ""
        out.append(
            f'<li class="{" ".join(classes)}"><span class="name">{_e(section.name)}</span>'
            f'<span class="bar"><span style="width:{width:.1f}%"></span></span>'
            f'<span class="took">{took}</span><span class="where">{note}{jump}</span></li>'
        )
    out.append("</ol>")
    return out


def _card(report: Report) -> list[str]:
    """The answer first: what failed, the class and fix type, and how far to trust it."""
    classification = classify(report)
    assessment = assess(report)
    diagnosis = report.diagnosis
    top = report.top_hit
    if diagnosis is not None:
        headline = diagnosis.summary
    elif top is not None:
        headline = top.rule.title
    else:
        headline = "No known failure signature matched"

    fix_type = f"<code>{_e(classification.fix_type)}</code>" if classification.fix_type else "none"
    rows = [
        ("class", f"<code>{_e(classification.failure_class)}</code>"),
        ("fix type", fix_type),
        ("basis", f"{_level(classification.confidence)} · {_e(classification.basis)}"),
        ("confidence", _level(assessment.confidence)),
        ("cost", _e(describe_cost(cost_of(report)))),
    ]
    out = [
        f'<div class="card level-border-{_e(assessment.confidence)}">',
        f'<p class="card-title">{_e(headline)}</p>',
        '<dl class="summary-grid">',
        *(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in rows),
        "</dl>",
    ]
    if assessment.needs_review:
        reasons = "".join(f"<li>{_e(reason)}</li>" for reason in assessment.reasons)
        out.append(
            '<div class="banner review" role="note"><strong>Needs human review.</strong> '
            f"Confidence is {_e(assessment.confidence)} because:<ul>{reasons}</ul></div>"
        )
    elif assessment.reasons:
        reasons = "; ".join(_e(reason) for reason in assessment.reasons)
        out.append(f'<p class="meta">Confidence is {_e(assessment.confidence)}: {reasons}.</p>')
    out.append("</div>")
    return out


#: "line 41203", "lines 12", "L41203" in the model's prose.
_LINE_MENTION = re.compile(r"\b(?:(?P<word>[Ll]ines?\s+)|L)(?P<number>\d+)\b")


def _prose(text: str, page: _Page, invented: frozenset[int]) -> str:
    """Escape the model's prose, linking each line it names to the evidence.

    A number the model cited but was never shown is marked, not linked: the
    claim beside it is the one to doubt.
    """

    def link(match: re.Match[str]) -> str:
        number = int(match.group("number"))
        whole = match.group(0)
        if number in page.shown:
            return f'<a href="#{page.line_id(number)}">{whole}</a>'
        if number in invented:
            return f'<span class="invented" title="not in the evidence">{whole}</span>'
        return whole

    return _LINE_MENTION.sub(link, _e(text))


def _diagnosis(report: Report, page: _Page) -> list[str]:
    diagnosis = report.diagnosis
    if diagnosis is None:
        return []
    invented = frozenset(diagnosis.unresolved_citations)
    other = model_disagreement(report)
    meta = f"{_level(diagnosis.confidence)} confidence · {_e(diagnosis.category.value)}"
    if other:
        meta += f" · the model reads it as <code>{_e(other)}</code>"
    out = ["<h2>Diagnosis</h2>", f'<p class="meta">{meta}</p>']
    out += [
        f"<p>{_prose(paragraph, page, invented)}</p>"
        for paragraph in diagnosis.root_cause.split("\n\n")
        if paragraph
    ]
    if diagnosis.citations or invented:
        out.append("<h3>Cited evidence</h3>")
        out.append('<div class="log">')
        for citation in diagnosis.citations:
            number = citation.line_number
            repeat = (
                f'<span class="repeat">×{citation.repeat}</span>' if citation.repeat > 1 else ""
            )
            out.append(
                f'<div class="line">'
                f'<a class="ln" href="#{page.line_id(number)}">{number}</a>'
                f'<span class="text">{_e(citation.text)}</span>{repeat}</div>'
            )
        # Shown, not dropped: an invented citation is the most useful thing a
        # reader can learn about the claim that leans on it.
        for number in diagnosis.unresolved_citations:
            out.append(
                f'<div class="line invented"><span class="ln">{number}</span>'
                '<span class="text">not in the evidence the model was shown; '
                "treat the claim that cites it with suspicion</span></div>"
            )
        out.append("</div>")
    if diagnosis.fixes:
        out.append("<h3>Suggested fixes</h3>")
        out.append('<ol class="fixes">')
        for fix in diagnosis.fixes:
            item = f"<strong>{_e(fix.title)}</strong>"
            if fix.detail:
                item += f"<br>{_prose(fix.detail, page, invented)}"
            if fix.patch:
                item += f"<pre><code>{_e(fix.patch)}</code></pre>"
            out.append(f"<li>{item}</li>")
        out.append("</ol>")
    return out


def _rules_only(report: Report, page: _Page) -> list[str]:
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
        f"<h2>What {_e(top.rule.id)} suggests</h2>",
        f'<p class="meta">{_level(top.rule.confidence)} · matched at line '
        f"{page.line_link(top.line_number)}</p>",
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


def _rules(report: Report, page: _Page, limit: int) -> list[str]:
    out = ["<h2>Rule matches</h2>"]
    if not report.hits:
        return [*out, '<p class="meta">None.</p>']
    out.append('<div class="table-wrap"><table><thead><tr><th>rule</th><th>title</th><th>line</th>')
    out.append("<th>confidence</th></tr></thead><tbody>")
    for hit in report.hits[:limit]:
        out.append(
            f"<tr><td><code>{_e(hit.rule.id)}</code></td><td>{_e(hit.rule.title)}</td>"
            f"<td>{page.line_link(hit.line_number)}</td>"
            f"<td>{_level(hit.rule.confidence)}</td></tr>"
        )
    out.append("</tbody></table></div>")
    if len(report.hits) > limit:
        out.append(
            f'<p class="meta">… and {len(report.hits) - limit} more (--all-rules to show).</p>'
        )
    return out


def _evidence(report: Report, page: _Page, shown: list[EvidenceLine], dropped: int) -> list[str]:
    stats = report.distilled.stats
    cited = (
        {citation.line_number for citation in report.diagnosis.citations}
        if report.diagnosis
        else set()
    )
    hit_lines = {hit.line_number for hit in report.hits}
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
        if line.number in hit_lines:
            classes.append("hit")
        line_id = page.line_id(line.number)
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
    shown, dropped = select_display_lines(report.distilled, evidence_limit)
    page = _Page(prefix=prefix, shown=frozenset(line.number for line in shown))
    section_id = prefix.rstrip("-") or "report"
    parts: list[str] = [f'<section class="report" id="{section_id}">']
    parts += _header(report)
    parts += _card(report)
    parts += _diagnosis(report, page)
    parts += _rules_only(report, page)
    parts += _timeline(report, page)
    parts += _rules(report, page, rule_limit)
    parts += _evidence(report, page, shown, dropped)
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

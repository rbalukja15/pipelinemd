"""The self-contained HTML report."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

import pytest

from pipelinemd import cli
from pipelinemd.distill import distill
from pipelinemd.models import (
    Category,
    Citation,
    Confidence,
    Diagnosis,
    EvidenceBlock,
    EvidenceLine,
    Fix,
    JobRef,
    Report,
    Usage,
)
from pipelinemd.render import render_html
from pipelinemd.rules import match_rules

from .conftest import TRACES
from .test_cli import EXIT_OK, EXIT_USAGE, run

DIAGNOSIS = Diagnosis(
    summary="npm ci failed on a peer dependency conflict",
    root_cause="The lockfile pins react@17.\n\nThe manifest asks for ^18.",
    confidence=Confidence.HIGH,
    category=Category.DEPENDENCY,
    fixes=(Fix(title="Regenerate the lockfile", detail="Run npm install.", patch="npm install"),),
    citations=(Citation(line_number=142, text="npm ERR! code ERESOLVE"),),
    model="claude-opus-5",
    input_tokens=1200,
    output_tokens=400,
)


@pytest.fixture
def report(trace: Callable[[str], str]) -> Report:
    distilled = distill(trace("npm_eresolve"))
    return Report(
        job=JobRef(
            name="build",
            id=98765,
            stage="test",
            project="acme/web",
            ref="main",
            url="https://gitlab.com/acme/web/-/jobs/98765",
            duration_s=47.0,
        ),
        distilled=distilled,
        hits=match_rules(distilled),
    )


class _Checker(HTMLParser):
    """Collects what a self-contained page must not have, and checks nesting."""

    VOID = frozenset({"meta", "br", "link", "img", "input", "hr"})

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.external: list[str] = []
        self.ids: list[str] = []
        self.scripts = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script":
            self.scripts += 1
        if tag in {"link", "img", "script", "iframe"} and (values.get("href") or values.get("src")):
            self.external.append(tag)
        if values.get("id"):
            self.ids.append(values["id"] or "")
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        assert self.stack and self.stack[-1] == tag, f"</{tag}> closes {self.stack[-1:]}"
        self.stack.pop()


def _check(page: str) -> _Checker:
    checker = _Checker()
    checker.feed(page)
    checker.close()
    assert checker.stack == [], f"unclosed: {checker.stack}"
    return checker


def test_the_page_is_one_well_formed_file_that_fetches_nothing(report: Report) -> None:
    page = render_html([replace(report, diagnosis=DIAGNOSIS)])
    checker = _check(page)
    assert page.startswith("<!doctype html>")
    assert checker.external == []
    assert checker.scripts == 0
    assert "@import" not in page and "url(" not in page
    assert "default-src 'none'" in page
    assert len(checker.ids) == len(set(checker.ids)), "ids must be unique"


def test_the_page_follows_the_colour_scheme_and_prints(report: Report) -> None:
    page = render_html([report])
    assert "prefers-color-scheme: dark" in page
    assert "@media print" in page


def test_log_text_is_escaped_not_run(report: Report) -> None:
    hostile = "<script>alert(1)</script> & <img src=x onerror=alert(2)>"
    block = EvidenceBlock(label="x", lines=(EvidenceLine(number=7, text=hostile, is_anchor=True),))
    distilled = replace(report.distilled, evidence=[block], failure_reason=hostile)
    diagnosis = replace(
        DIAGNOSIS,
        summary=hostile,
        root_cause=hostile,
        citations=(Citation(line_number=7, text=hostile),),
        fixes=(Fix(title=hostile, detail=hostile, patch=hostile),),
    )
    job = replace(report.job, name=hostile, url='javascript:"><b>')
    page = render_html([replace(report, job=job, distilled=distilled, diagnosis=diagnosis)])
    checker = _check(page)
    assert checker.scripts == 0
    assert "<img" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp;" in page
    assert '"><b>' not in page
    assert "javascript:" not in page


def test_a_diagnosis_shows_its_summary_fixes_and_cost(report: Report) -> None:
    page = render_html([replace(report, diagnosis=DIAGNOSIS)])
    assert "npm ci failed on a peer dependency conflict" in page
    assert "<p>The lockfile pins react@17.</p>" in page
    assert "Regenerate the lockfile" in page
    assert "<pre><code>npm install</code></pre>" in page
    assert "est. cost $" in page
    assert "diagnosis by <code>claude-opus-5</code>" in page


def test_rules_only_says_so_and_shows_the_rule_advice(report: Report) -> None:
    page = render_html([report])
    assert "Rules only." in page
    assert "Try this" in page
    assert report.hits[0].rule.id in page
    assert "cost $0" in page


def test_a_rejected_diagnosis_is_not_mistaken_for_rules_only(report: Report) -> None:
    rejected = replace(report, discarded_calls=(Usage(model="claude-opus-5", input_tokens=900),))
    page = render_html([rejected])
    assert "No diagnosis." in page
    assert "not usable" in page
    assert "Rules only." not in page


def test_needs_human_review_lists_every_reason(report: Report) -> None:
    weak = replace(DIAGNOSIS, confidence=Confidence.LOW, unresolved_citations=(41203,))
    page = render_html([replace(report, diagnosis=weak)])
    assert "Needs human review." in page
    assert "the model rated its diagnosis low" in page
    assert "L41203" in page
    assert "not in the evidence" in page


def test_evidence_keeps_original_line_numbers_as_anchors(report: Report) -> None:
    page = render_html([replace(report, diagnosis=DIAGNOSIS)])
    assert re.search(r'<div class="line[^"]*\bcited\b[^"]*" id="L142">', page)
    assert 'href="#L142"' in page


def test_html_shows_all_evidence_by_default(report: Report) -> None:
    flat = [line for block in report.distilled.evidence for line in block.lines]
    page = render_html([report], evidence_limit=0)
    assert page.count('class="ln"') == len(flat)


def test_several_reports_share_one_page_with_the_run_total(report: Report) -> None:
    second = replace(report, job=replace(report.job, name="lint", id=98766))
    page = render_html([report, second])
    checker = _check(page)
    assert "2 failed jobs" in page
    assert 'href="#j1"' in page and 'id="j2"' in page
    assert "j1-L142" in checker.ids and "j2-L142" in checker.ids
    assert "Run total" in page
    assert len(checker.ids) == len(set(checker.ids))


# -- the CLI ----------------------------------------------------------------


def test_distill_writes_html() -> None:
    code, out, _err = run("distill", str(TRACES / "npm_eresolve.log"), "--format", "html")
    assert code == EXIT_OK
    assert out.startswith("<!doctype html>")
    assert "\x1b[" not in out


def test_several_outputs_take_their_format_from_the_extension(tmp_path: Path) -> None:
    html, md, js = tmp_path / "d.html", tmp_path / "d.md", tmp_path / "d.json"
    code, out, _err = run(
        "diagnose",
        "--from-file",
        str(TRACES / "npm_eresolve.log"),
        "--no-llm",
        "-o",
        str(html),
        "-o",
        str(md),
        "-o",
        str(js),
    )
    assert code == EXIT_OK
    assert out == ""
    assert html.read_text().startswith("<!doctype html>")
    assert md.read_text().startswith("### pipelinemd")
    assert js.read_text().lstrip().startswith("{")


def test_one_output_keeps_the_format_flag(tmp_path: Path) -> None:
    target = tmp_path / "report.txt"
    code, _out, _err = run(
        "distill", str(TRACES / "npm_eresolve.log"), "--format", "html", "-o", str(target)
    )
    assert code == EXIT_OK
    assert target.read_text().startswith("<!doctype html>")


def test_an_unknown_extension_among_several_fails_before_any_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("no fetch should happen")

    monkeypatch.setattr(cli, "GitLabClient", explode)
    code, _out, err = run(
        "diagnose",
        "https://gitlab.com/acme/web/-/jobs/1",
        "-o",
        str(tmp_path / "a.html"),
        "-o",
        str(tmp_path / "a.pdf"),
    )
    assert code == EXIT_USAGE
    assert "a.pdf" in err

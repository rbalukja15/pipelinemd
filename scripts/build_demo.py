"""Build the demo site: five failed jobs, each with the report pipelinemd writes.

    python scripts/build_demo.py [OUT_DIR]

Writes ``index.html``, a page listing the cases, and one report per case, to
OUT_DIR (default ``build/demo``). The Pages workflow publishes that directory,
so anyone can open a diagnosis without a GitLab account, an API key or an
install.

Each report is exactly what ``pipelinemd distill --format html`` writes for
that log: the pages are built by the CLI, not drawn for the demo, and are
rebuilt from the code on every push to main, so they cannot drift from what
the tool does. That also means they are rules only. A model diagnosis needs an
API key and costs money on every build, and a canned one would be a page the
tool did not write.

Four cases come from the labelled corpus, which is authored, and one is a real
log: pipelinemd's own first CI failure, where no rule fires
(``docs/dogfood.md``). The index says which is which.
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path

from pipelinemd import __version__
from pipelinemd.cli import main as pipelinemd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "build" / "demo"
PROJECT_URL = "https://github.com/rbalukja15/pipelinemd"


@dataclass(frozen=True)
class Case:
    slug: str
    trace: Path
    title: str
    blurb: str
    real: bool = False


CASES = (
    Case(
        slug="yaml-unknown-stage",
        trace=ROOT / "corpus" / "traces" / "yaml-unknown-stage.log",
        title="A job names a stage the pipeline never declared",
        blurb="GitLab rejects the pipeline before anything runs. The fix is a change "
        "to .gitlab-ci.yml, and the report says which key to look at.",
    ),
    Case(
        slug="npm-registry-401",
        trace=ROOT / "corpus" / "traces" / "civars-npm-registry-401.log",
        title="npm cannot authenticate to a private registry",
        blurb="npm publish is refused with a 401. The cause is a CI/CD variable, "
        "not the code, and the report says so before anyone reads a diff.",
    ),
    Case(
        slug="dockerhub-rate-limit",
        trace=ROOT / "corpus" / "traces" / "imagepull-dockerhub-rate-limit.log",
        title="Docker Hub's pull rate limit",
        blurb="The runner could not pull the job's image. The report explains why "
        "shared runners run into this and how to stop hitting it.",
    ),
    Case(
        slug="pytest-assertion",
        trace=ROOT / "corpus" / "traces" / "test-pytest-assertion.log",
        title="A failing pytest assertion",
        blurb="A test failure, which is a code change rather than a CI problem. "
        "The excerpt keeps the assertion and the line it failed on.",
    ),
    Case(
        slug="pip-missing-readme",
        trace=ROOT / "tests" / "fixtures" / "traces" / "github" / "pip_metadata_missing_readme.log",
        title="pipelinemd's own first CI failure",
        blurb="A real GitHub Actions log. No rule covers it, so the report says it "
        "does not know and points at the line to read, rather than guessing.",
        real=True,
    ),
)

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --border: #d1d9e0;
  --panel: #f6f8fa; --link: #0969da;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --border: #3d444d;
    --panel: #151b23; --link: #4493f8;
  }
}
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}
main { max-width: 52rem; margin: 0 auto; padding: 2rem 1rem; }
a { color: var(--link); }
h1 { margin: 0 0 .25rem; }
.lede, .meta, .footer { color: var(--muted); }
code { font-size: .9em; background: var(--panel); padding: .1em .3em; border-radius: 4px; }
ol.cases { list-style: none; padding: 0; margin: 1.5rem 0; display: grid; gap: .75rem; }
.case {
  display: block; padding: .9rem 1rem; border: 1px solid var(--border);
  border-radius: 6px; background: var(--panel); color: inherit; text-decoration: none;
}
.case:hover, .case:focus-visible { border-color: var(--link); }
.case .title { font-weight: 600; color: var(--link); }
.case .blurb { margin: .25rem 0 0; }
.case .meta { font-size: .9em; margin: .35rem 0 0; }
.footer { border-top: 1px solid var(--border); margin-top: 2rem; padding-top: 1rem; font-size: .9em; }
"""


def build_report(case: Case, out_dir: Path) -> Path:
    """Write one case's report with the CLI, exactly as a user would get it."""
    target = out_dir / f"{case.slug}.html"
    stderr = io.StringIO()
    code = pipelinemd(
        ["distill", str(case.trace), "--format", "html", "-o", str(target)],
        stdout=io.StringIO(),
        stderr=stderr,
    )
    if code != 0:
        raise SystemExit(f"pipelinemd distill failed on {case.trace}: {stderr.getvalue()}")
    return target


def render_index(cases: tuple[Case, ...] = CASES) -> str:
    items = []
    for case in cases:
        origin = (
            "real log, from pipelinemd's own CI"
            if case.real
            else "authored corpus case, written to match a real failure"
        )
        items.append(
            "<li>"
            f'<a class="case" href="{escape(case.slug)}.html">'
            f'<span class="title">{escape(case.title)}</span>'
            f'<p class="blurb">{escape(case.blurb)}</p>'
            f'<p class="meta">{escape(origin)} · <code>{escape(case.trace.name)}</code></p>'
            "</a>"
            "</li>"
        )
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            "style-src 'unsafe-inline'\">",
            "<title>pipelinemd demo</title>",
            f"<style>{_CSS}</style>",
            "</head>",
            "<body>",
            "<main>",
            "<h1>pipelinemd demo</h1>",
            '<p class="lede">Five failed CI jobs and the report pipelinemd wrote for each. '
            "Pick one to see what lands in a failed pipeline's artifacts.</p>",
            '<ol class="cases">',
            *items,
            "</ol>",
            "<p>Every report here is the unedited output of "
            "<code>pipelinemd distill --format html</code> on that log, rebuilt from the "
            "code on each change. They are rules only: no model was called and nothing "
            "was spent. With an API key, <code>pipelinemd diagnose</code> adds a written "
            "diagnosis that cites these same evidence lines.</p>",
            f'<p class="footer">pipelinemd {escape(__version__)} · '
            f'<a href="{PROJECT_URL}">source on GitHub</a> · '
            f'<a href="{PROJECT_URL}/blob/main/docs/evaluation.md">how it is scored</a></p>',
            "</main>",
            "</body>",
            "</html>",
            "",
        ]
    )


def build(out_dir: Path = DEFAULT_OUT) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [build_report(case, out_dir) for case in CASES]
    index = out_dir / "index.html"
    index.write_text(render_index(), encoding="utf-8")
    return [index, *written]


def run(argv: list[str]) -> int:
    out_dir = Path(argv[0]) if argv else DEFAULT_OUT
    for path in build(out_dir):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))

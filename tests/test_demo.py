"""The demo site: five cases, each one a real report the CLI wrote.

The Pages workflow is the only thing that runs the builder, and it runs after
merge. Building here means a renamed trace or a broken case fails the pull
request instead of the deploy.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _load_build_demo() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_demo", ROOT / "scripts" / "build_demo.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # A dataclass looks its module up while it is being defined.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_demo = _load_build_demo()


def test_there_are_five_cases_with_distinct_pages() -> None:
    slugs = [case.slug for case in build_demo.CASES]
    assert len(slugs) == 5
    assert len(set(slugs)) == 5
    for case in build_demo.CASES:
        assert case.trace.is_file(), case.trace


def test_the_index_links_every_case_and_nothing_else(tmp_path: Path) -> None:
    written = build_demo.build(tmp_path)

    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    links = re.findall(r'href="([^"]+\.html)"', index)
    assert sorted(links) == sorted(f"{case.slug}.html" for case in build_demo.CASES)
    assert sorted(p.name for p in written) == sorted(["index.html", *links])


def test_each_page_is_the_report_distill_writes(tmp_path: Path) -> None:
    build_demo.build(tmp_path)

    for case in build_demo.CASES:
        page = (tmp_path / f"{case.slug}.html").read_text(encoding="utf-8")
        assert f"pipelinemd · {case.trace.name}" in page
        assert 'id="L' in page  # the evidence, with its line anchors
        assert "<script" not in page


def test_the_index_is_static_and_says_which_log_is_real() -> None:
    index = build_demo.render_index()

    assert "<script" not in index
    assert "default-src 'none'" in index
    real = [case for case in build_demo.CASES if case.real]
    assert len(real) == 1
    assert index.count("real log") == 1
    assert index.count("authored corpus case") == 4

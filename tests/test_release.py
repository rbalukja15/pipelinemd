"""Release hygiene, checked without Docker or a network.

The release workflow refuses a version that has no changelog section, but only
once the bump reaches main. Checking here means a version bump without a
changelog entry fails on the pull request instead.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

import pytest

import pipelinemd

ROOT = Path(__file__).resolve().parents[1]


def _load_release_notes() -> ModuleType:
    # scripts/ is not a package, and should not become one just to be tested.
    spec = importlib.util.spec_from_file_location(
        "release_notes", ROOT / "scripts" / "release_notes.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_notes = _load_release_notes()

CHANGELOG = """\
# Changelog

## [Unreleased]

## [1.2.0] - 2026-01-31

### Added

- Something.

## [1.1.0] - YYYY-MM-DD

- Not yet dated.

[Unreleased]: https://example.invalid/compare/v1.2.0...HEAD
[1.2.0]: https://example.invalid/releases/tag/v1.2.0
"""


def test_changelog_has_a_section_for_the_current_version() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release_notes.changelog_section(changelog, pipelinemd.__version__) is not None, (
        f"CHANGELOG.md needs a ## [{pipelinemd.__version__}] section"
    )


def test_the_script_reads_the_version_the_package_reports() -> None:
    source = (ROOT / "src" / "pipelinemd" / "__init__.py").read_text(encoding="utf-8")
    assert release_notes.package_version(source) == pipelinemd.__version__


def test_a_matching_dated_section_gives_its_body_as_the_notes() -> None:
    notes = release_notes.check("v1.2.0", '__version__ = "1.2.0"\n', CHANGELOG)
    assert notes == "### Added\n\n- Something."


@pytest.mark.parametrize(
    ("tag", "version", "reason"),
    [
        ("v1.2.1", "1.2.0", "does not match"),
        ("v1.3.0", "1.3.0", "no section"),
        ("v1.1.0", "1.1.0", "not dated"),
        ("v1.3.0rc1", "1.3.0rc1", "not a final"),
    ],
)
def test_a_release_is_refused(tag: str, version: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        release_notes.check(tag, f'__version__ = "{version}"\n', CHANGELOG)


def test_a_release_with_unlisted_changes_is_refused() -> None:
    changelog = CHANGELOG.replace("## [Unreleased]\n", "## [Unreleased]\n\n- Merged since.\n")
    with pytest.raises(ValueError, match=r"under \[Unreleased\]"):
        release_notes.check("v1.2.0", '__version__ = "1.2.0"\n', changelog)


def test_the_release_tag_is_the_final_version() -> None:
    assert release_notes.release_tag('__version__ = "1.2.0"\n') == "v1.2.0"
    with pytest.raises(ValueError, match="not a final"):
        release_notes.release_tag('__version__ = "1.3.0rc1"\n')


def test_the_image_entrypoint_is_the_cli() -> None:
    # The README and the GitLab example both depend on this: `docker run
    # <image> distill -` only works if the entrypoint is pipelinemd itself.
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    entrypoints = re.findall(r"^ENTRYPOINT (.+)$", dockerfile, re.MULTILINE)
    assert [json.loads(entry) for entry in entrypoints] == [["pipelinemd"]]

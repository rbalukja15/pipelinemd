"""Check a release tag against the code, and print that version's changelog.

    python scripts/release_notes.py v0.2.0

Exits non-zero, saying why, unless all of these hold:

* the tag minus its leading ``v`` is ``pipelinemd.__version__``, read from the
  same line hatch reads to stamp the wheel - so the tag, the package on PyPI and
  the image are one version;
* the version is a final ``X.Y.Z`` release. The image tags are computed as
  semver, which reads ``1.3.0rc1`` (or a ``.post``/``.dev`` version) as no
  version at all, so the image job would fail only after PyPI had accepted the
  upload. Pre-, post- and dev releases are refused here instead;
* CHANGELOG.md has a section for that version;
* the section is dated, so the template's ``YYYY-MM-DD`` cannot be published.

On success the section's body goes to stdout, which is what the GitHub
Release's notes are made from. The release workflow runs it before anything
is built, so a mismatch stops a release while it can still be fixed by
re-tagging, rather than after PyPI has accepted a version it will never let go.

Standard library only: it runs on a bare runner before anything is installed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "src" / "pipelinemd" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"

_VERSION = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)
_HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\](?: - (?P<date>.+))?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# No leading zeros, as strict semver (and so docker/metadata-action) requires.
_FINAL = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


def package_version(source: str) -> str:
    match = _VERSION.search(source)
    if match is None:
        raise ValueError(f"no __version__ line in {VERSION_FILE.name}")
    return match.group(1)


def changelog_section(changelog: str, version: str) -> tuple[str | None, str] | None:
    """Return ``(date, body)`` for ``version``'s section, or None if it has none.

    A section runs from its ``## [x.y.z]`` heading to the next ``## `` heading
    or the link references at the foot of the file.
    """
    lines = changelog.splitlines()
    for start, line in enumerate(lines):
        heading = _HEADING.match(line)
        if heading is None or heading.group("version") != version:
            continue
        body: list[str] = []
        for following in lines[start + 1 :]:
            if following.startswith("## ") or re.match(r"^\[[^\]]+\]: ", following):
                break
            body.append(following)
        return heading.group("date"), "\n".join(body).strip()
    return None


def check(tag: str, source: str, changelog: str) -> str:
    """Return the release notes for ``tag``, or raise ValueError saying what is wrong."""
    version = package_version(source)
    if tag.removeprefix("v") != version:
        raise ValueError(f"tag {tag} does not match __version__ {version}")
    if not _FINAL.fullmatch(version):
        raise ValueError(
            f"{version} is not a final X.Y.Z release; pre-, post- and dev releases are not published"
        )
    section = changelog_section(changelog, version)
    if section is None:
        raise ValueError(f"CHANGELOG.md has no section for {version}")
    date, body = section
    if date is None or not _DATE.match(date):
        raise ValueError(f"CHANGELOG.md section for {version} is not dated (YYYY-MM-DD)")
    if not body:
        raise ValueError(f"CHANGELOG.md section for {version} is empty")
    return body


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: release_notes.py vX.Y.Z", file=sys.stderr)
        return 2
    try:
        notes = check(
            argv[0],
            VERSION_FILE.read_text(encoding="utf-8"),
            CHANGELOG.read_text(encoding="utf-8"),
        )
    except ValueError as error:
        print(f"release check failed: {error}", file=sys.stderr)
        return 1
    print(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""The Claude Code plugin, checked against the CLI it drives.

The skill is instructions, not code, so nothing fails when a flag it tells the
agent to pass is renamed. These tests do: every command the skill spells out
has to parse with today's CLI.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest

from pipelinemd.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"


def _plugin_dir() -> Path:
    marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    (entry,) = marketplace["plugins"]
    return (ROOT / entry["source"]).resolve()


SKILL = _plugin_dir() / "skills" / "pipelinemd" / "SKILL.md"


def _frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "SKILL.md must start with a frontmatter block"
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def _subcommands() -> dict[str, argparse.ArgumentParser]:
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    raise AssertionError("the CLI has no subcommands")


def _skill_commands() -> list[str]:
    """Every inline `pipelinemd <subcommand> ...` the skill tells the agent to run."""
    spans = re.findall(r"`([^`]+)`", SKILL.read_text(encoding="utf-8"))
    return [span for span in spans if re.search(r"\bpipelinemd (diagnose|distill)\b", span)]


def test_marketplace_points_at_a_plugin() -> None:
    marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    plugin = json.loads(
        (_plugin_dir() / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )

    assert marketplace["plugins"][0]["name"] == plugin["name"] == "pipelinemd"
    assert _plugin_dir().is_relative_to(ROOT)


def test_skill_frontmatter() -> None:
    fields = _frontmatter(SKILL.read_text(encoding="utf-8"))

    assert fields["name"] == SKILL.parent.name
    # Claude Code reads only the description to decide when to load a skill.
    assert 0 < len(fields["description"]) <= 1024


def test_skill_names_real_commands() -> None:
    assert len(_skill_commands()) >= 3


@pytest.mark.parametrize("command", _skill_commands())
def test_skill_commands_parse(command: str) -> None:
    # From the subcommand on: a docker run spells the binary as the image name.
    match = re.search(r"\bpipelinemd ((?:diagnose|distill)\b.*)", command)
    assert match
    subcommand, *rest = match.group(1).split()
    known = _subcommands()[subcommand]._option_string_actions

    flags = [word for word in rest if word.startswith("--")]
    assert flags or subcommand == "distill"
    for flag in flags:
        assert flag in known, f"{flag} is not a `pipelinemd {subcommand}` option"

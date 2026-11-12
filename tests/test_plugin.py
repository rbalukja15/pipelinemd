"""The Claude Code plugin, checked against the CLI it drives.

The skill is instructions, not code, so nothing fails when a flag it tells the
agent to pass is renamed. These tests do: every command the skill spells out
has to parse with today's CLI.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

import pipelinemd
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


def test_plugin_version_matches_the_package() -> None:
    # Claude Code offers installed plugins an update only when this changes.
    plugin = json.loads(
        (_plugin_dir() / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )

    assert plugin["version"] == pipelinemd.__version__


def test_plugin_icon_fits_the_directory() -> None:
    # The plugin directory wants a square PNG, 512 to 2048 px a side, under 2 MB.
    icon = (_plugin_dir() / ".claude-plugin" / "icon.png").read_bytes()
    width, height = struct.unpack(">II", icon[16:24])

    assert icon.startswith(b"\x89PNG\r\n\x1a\n")
    assert width == height and 512 <= width <= 2048
    assert len(icon) < 2 * 1024 * 1024


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


HOOKS = _plugin_dir() / "hooks" / "hooks.json"
WATCH_HOOK = _plugin_dir() / "hooks" / "watch_failed.py"
RUN_URL = "https://github.com/acme/app/actions/runs/123/job/456"


def _hook_events() -> dict[str, list[dict[str, object]]]:
    data = json.loads(HOOKS.read_text(encoding="utf-8"))
    # Claude Code ignores a hooks file without the top-level "hooks" wrapper.
    events: dict[str, list[dict[str, object]]] = data["hooks"]
    return events


def _run_hook(event: dict[str, object]) -> str:
    result = subprocess.run(
        [sys.executable, str(WATCH_HOOK)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return result.stdout


def _bash_event(command: str, stdout: str = "", error: str | None = None) -> dict[str, object]:
    event: dict[str, object] = {
        "hook_event_name": "PostToolUseFailure" if error else "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    if error:
        event["error"] = error
    else:
        event["tool_response"] = {"stdout": stdout, "stderr": "", "interrupted": False}
    return event


def test_hooks_run_the_bundled_script_after_bash() -> None:
    events = _hook_events()

    # A failing command fires PostToolUseFailure, not PostToolUse, so both are needed.
    assert set(events) == {"PostToolUse", "PostToolUseFailure"}
    for groups in events.values():
        (group,) = groups
        assert group["matcher"] == "Bash"
        (handler,) = group["hooks"]  # type: ignore[misc]  # JSON, checked by the unpack
        assert "${CLAUDE_PLUGIN_ROOT}/hooks/watch_failed.py" in handler["command"]
    assert WATCH_HOOK.is_file()


@pytest.mark.parametrize(
    "event",
    [
        _bash_event(
            "gh run watch 123 --exit-status",
            error=f"Exit code 1\nX main CI · 123\nRun CI (123) completed with 'failure'\n{RUN_URL}",
        ),
        _bash_event(
            "gh pr checks 42 --watch",
            error=f"Exit code 1\nSome checks were not successful\ntest\tfail\t1m\t{RUN_URL}",
        ),
        _bash_event(
            "gh run watch 123", stdout=f"X Run CI (123) completed with 'failure'\n{RUN_URL}"
        ),
    ],
)
def test_watch_hook_names_the_failed_run(event: dict[str, object]) -> None:
    output = json.loads(_run_hook(event))["hookSpecificOutput"]

    assert output["hookEventName"] == event["hook_event_name"]
    assert "pipelinemd skill" in output["additionalContext"]
    assert RUN_URL in output["additionalContext"]


@pytest.mark.parametrize(
    "event",
    [
        # Green: the summary still counts "0 failing".
        _bash_event(
            "gh pr checks --watch",
            stdout="All checks were successful\n0 cancelled, 0 failing, 5 successful",
        ),
        # Still pending: gh pr checks exits 8.
        _bash_event("gh pr checks", error="Exit code 8\nbuild\tpending\t0\tlink"),
        # Not a watch command, even though it failed.
        _bash_event("pytest", error="Exit code 1\n1 failed, 3 passed"),
        # The skill reading a log is not a watch.
        _bash_event("gh run view 123 --log-failed", stdout="FAILED tests/test_x.py"),
        # Already diagnosing.
        _bash_event("gh run watch 1 && uvx pipelinemd diagnose x", error="Exit code 1\nfailed"),
        {"hook_event_name": "PostToolUse", "tool_name": "Write", "tool_input": {}},
    ],
)
def test_watch_hook_stays_quiet(event: dict[str, object]) -> None:
    assert _run_hook(event) == ""


def test_watch_hook_ignores_input_it_cannot_read() -> None:
    result = subprocess.run(
        [sys.executable, str(WATCH_HOOK)], input="not json", capture_output=True, text=True
    )

    assert result.returncode == 0 and result.stdout == ""

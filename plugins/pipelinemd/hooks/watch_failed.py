"""Tell Claude to run the pipelinemd skill when a CI watch command shows a failure.

Claude Code runs this after every Bash call (PostToolUse) and after every Bash
call that exits non-zero (PostToolUseFailure), with the call as JSON on stdin.
Most calls are not CI watches, so the script stays silent unless the command
watches a run and its output says that run failed. Then it prints one line of
context for Claude, naming the failed URLs it found. It never blocks a call and
never runs pipelinemd itself: the skill decides which command fits.

Standard library only, so it runs on any python3 without the package installed.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

# Commands that wait on, or report, a run's result. `gh run view` only counts
# with --exit-status: without it, it is how the skill itself reads a log.
WATCH = re.compile(
    r"\bgh\s+run\s+watch\b"
    r"|\bgh\s+pr\s+checks\b"
    r"|\bgh\s+run\s+view\b[^|;&]*--exit-status"
    r"|\bglab\s+(?:ci|pipeline)\s+status\b"
)
FAILED = re.compile(r"\b(?:fail|failed|failure|failing)\b|(?-i:^\s*X\s)", re.I | re.M)
# Summaries count every outcome, so a green run still says "0 failing".
NONE_FAILED = re.compile(r"\b0\s+(?:fail|failed|failures?|failing)\b", re.I)
# After a clean exit the loose match above would trip on a green job named
# `fail-fast`, so only the CLIs' own failure lines count there.
FAILED_STRICT = re.compile(
    r"completed with 'failure'"
    r"|\b[1-9]\d*\s+(?:failing|failed)\b"
    r"|\tfail\t"
    r"|\(failed\)"
    r"|Pipeline State:\s*failed",
    re.I,
)
URL = re.compile(
    r"https://([^\s/'\"<>)]+)[^\s'\"<>)]*?"
    r"/(?:actions/runs/\d+(?:/job/\d+)?|-/(?:pipelines|jobs)/\d+)"
)
# Check links in `gh pr checks` come from whoever runs the check, and the skill
# fetches whatever URL it is given with the user's token. So pass on only hosts
# the user already works with: the public forges, the ones their CLIs are set
# to, and any named in the watch command itself.
KNOWN_HOSTS = frozenset({"github.com", "gitlab.com"})
HOST_VARS = ("GH_HOST", "GITLAB_HOST", "PIPELINEMD_GITHUB_URL", "PIPELINEMD_GITLAB_URL")
# `gh pr checks` exits 8 while checks are still pending, which is not a failure.
PENDING = re.compile(r"^Exit code 8\b")
MAX_URLS = 5


def _text(event: dict[str, Any]) -> str:
    response = event.get("tool_response")
    parts = [str(event.get("error") or "")]
    if isinstance(response, dict):
        parts += [str(response.get("stdout") or ""), str(response.get("stderr") or "")]
    elif response:
        parts.append(str(response))
    return NONE_FAILED.sub("", "\n".join(parts))


def _host(value: str) -> str:
    return value.split("://", 1)[-1].split("/", 1)[0].lower()


def _trusted_hosts(command: str) -> set[str]:
    hosts = set(KNOWN_HOSTS)
    hosts.update(_host(os.environ[var]) for var in HOST_VARS if os.environ.get(var))
    hosts.update(match.group(1).lower() for match in URL.finditer(command))
    return hosts


def _failed_urls(text: str, trusted: set[str]) -> list[str]:
    """Trusted URLs on lines that report a failure first, then any others, without repeats."""
    lines = text.splitlines()
    ranked = [line for line in lines if FAILED.search(line)]
    ranked += [line for line in lines if not FAILED.search(line)]
    urls: list[str] = []
    for line in ranked:
        for match in URL.finditer(line):
            url = match.group(0)
            if match.group(1).lower() in trusted and url not in urls:
                urls.append(url)
    return urls[:MAX_URLS]


def context_for(event: dict[str, Any]) -> str | None:
    """The message for Claude after this tool call, or None to say nothing."""
    if event.get("tool_name") != "Bash" or event.get("is_interrupt"):
        return None
    command = str((event.get("tool_input") or {}).get("command") or "")
    if not WATCH.search(command) or "pipelinemd" in command:
        return None
    error = str(event.get("error") or "")
    if PENDING.match(error):
        return None
    text = _text(event)
    if not (FAILED if error else FAILED_STRICT).search(text):
        return None

    urls = _failed_urls(text, _trusted_hosts(command))
    where = (
        "Failed runs or jobs: " + ", ".join(urls) + "."
        if urls
        else "Find the failed run's URL or id (for example with `gh run list`)."
    )
    return (
        f"The CI run watched by `{command.strip()}` failed. Before anything else, use "
        "the pipelinemd skill to diagnose it: run `uvx pipelinemd diagnose <url> "
        "--no-llm --format markdown` on the failed run or job, then explain the root "
        f"cause from its report. {where}"
    )


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    message = context_for(event)
    if message:
        output = {
            "hookSpecificOutput": {
                "hookEventName": event.get("hook_event_name", "PostToolUse"),
                "additionalContext": message,
            }
        }
        json.dump(output, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Mask credentials before a trace leaves the machine.

pipelinemd can send distilled evidence to an LLM, and users paste its output
into merge requests. GitLab masks *known* CI variables, but anything a build
tool prints itself - a registry URL with inline credentials, an
``aws configure`` echo, a JWT in a debug dump - arrives in the trace intact.
Redaction runs before evidence selection, so a secret never reaches the
prompt, the terminal, or the JSON output.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

MASK = "«redacted»"

# Ordered: more specific shapes first so they win over the generic key=value
# rule and produce a more informative mask.
_PATTERNS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    # GitLab personal / project / runner tokens and their newer prefixes.
    (
        "gitlab-token",
        re.compile(r"\b(?:glpat|glrt|gldt|glft|glsoat|glimt|glagent)-[A-Za-z0-9_\-]{20,}"),
        0,
    ),
    ("gitlab-legacy-runner-token", re.compile(r"\bGR1348941[A-Za-z0-9_\-]{20,}"), 0),
    # GitHub tokens (common in cross-CI setups).
    ("github-token", re.compile(r"\b gh[pousr]_[A-Za-z0-9]{16,} ".replace(" ", "")), 0),
    # AWS.
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA)[0-9A-Z]{16}\b"), 0),
    # Slack.
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), 0),
    # JSON Web Tokens.
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), 0),
    # Credentials embedded in a URL: scheme://user:password@host
    ("url-credentials", re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/:@]+:)([^\s/@]+)(@)"), 2),
    # Authorization headers.
    ("auth-header", re.compile(r"((?i:authorization)\s*:\s*(?i:bearer|basic|token)\s+)(\S+)"), 2),
    ("bearer", re.compile(r"\b(?i:bearer)\s+([A-Za-z0-9_\-.=+/]{16,})"), 1),
    # Command-line flags that take a secret.
    (
        "secret-flag",
        re.compile(r"(--(?:password|token|api[-_]?key|secret|access[-_]?key)(?:[= ]))(\S+)"),
        2,
    ),
    # key=value / key: value where the key names a secret.
    (
        "secret-assignment",
        re.compile(
            r"((?i:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
            r"private[_-]?key|client[_-]?secret|auth[_-]?token)[\"']?\s*[=:]\s*[\"']?)"
            r"([^\s\"',;]{4,})"
        ),
        2,
    ),
)

_PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PRIVATE_KEY_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")

# Values that look secret-shaped but carry no secret - masking them only
# destroys signal.
_SAFE_VALUES = frozenset(
    {
        "true",
        "false",
        "null",
        "none",
        "nil",
        "yes",
        "no",
        "0",
        "1",
        "***",
        "[masked]",
        "[filtered]",
        "xxx",
        "redacted",
        "<nil>",
        MASK,
    }
)


def _mask_group(match: re.Match[str], group: int) -> str:
    """Replace one group of a match, leaving the rest of the match intact."""
    if group == 0:
        return MASK
    value = match.group(group)
    if value.strip().strip("\"'").lower() in _SAFE_VALUES:
        return match.group(0)
    start, end = match.span(group)
    whole_start = match.start()
    text = match.group(0)
    return text[: start - whole_start] + MASK + text[end - whole_start :]


def redact(text: str) -> str:
    """Return ``text`` with credential-shaped substrings replaced by a mask."""
    for _name, pattern, group in _PATTERNS:
        text = pattern.sub(lambda m, g=group: _mask_group(m, g), text)  # type: ignore[misc]
    return text


def redact_lines(lines: Iterable[str]) -> list[str]:
    """Redact a sequence of lines, never changing how many there are.

    Line-preserving matters because evidence selection refers to lines by
    number. PEM blocks are handled statefully here rather than with a
    DOTALL regex, so a multi-line key collapses to one mask per line.
    """
    out: list[str] = []
    in_key_block = False
    for line in lines:
        if in_key_block:
            end = _PRIVATE_KEY_END.search(line)
            if end:
                in_key_block = False
                out.append(end.group(0))
            else:
                out.append(MASK)
            continue
        begin = _PRIVATE_KEY_BEGIN.search(line)
        if begin:
            in_key_block = True
            out.append(begin.group(0))
            continue
        out.append(redact(line))
    return out


def redaction_patterns() -> tuple[str, ...]:
    """Names of the credential shapes we mask - surfaced by ``pipelinemd rules``."""
    return (*(name for name, _pattern, _group in _PATTERNS), "private-key-block")

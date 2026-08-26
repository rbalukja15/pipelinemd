"""Turn a raw gitlab-runner trace into clean, numbered, attributed lines.

The runner interleaves four things in one stream: the script's own output,
collapsible section markers, optional per-line timestamps, and terminal
control codes. This module separates them, so everything downstream sees
plain text plus structured metadata about it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Section, TraceLine
from .ansi import apply_overwrites, expand_tabs, strip_ansi
from .redact import redact_lines

# section_start:<unix-ts>:<name>[collapsed=true]\r\x1b[0K
# The \r\x1b[0K suffix is what hides the marker in a terminal; ANSI has
# already been stripped by the time we match, so only the \r remains.
_SECTION = re.compile(r"^section_(start|end):(\d{1,12}):([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\r?")

# Runner "timestamps" feature: RFC3339 followed by a short stream descriptor.
_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s+(?:[0-9A-Za-z]{2,4}\s)?"
)

_RUNNER = re.compile(r"^Running with gitlab-runner\s+(.+?)\s*$")
_RUNNER_ON = re.compile(r"^\s*on\s+(.+?)\s*$")
_IMAGE = re.compile(
    r"(?:Using Docker executor with image|Using effective pull policy .* for|"
    r"Pulling docker image)\s+([^\s]+)"
)
_EXIT_CODE = re.compile(r"(?:exit code|exit status|exited with code)\s+(\d{1,3})\b", re.IGNORECASE)
_JOB_FAILED = re.compile(r"^ERROR: Job failed.*$")
_COMMAND = re.compile(r"^\$ (.+)$")

MAX_LINE_CHARS = 1000
MAX_RAW_BYTES = 64 * 1024 * 1024


@dataclass(slots=True)
class CleanedTrace:
    lines: list[TraceLine]
    sections: list[Section]
    commands: list[str]
    exit_code: int | None
    failure_reason: str | None
    runner: str | None
    image: str | None
    raw_bytes: int
    raw_lines: int


def _truncate(line: str, limit: int = MAX_LINE_CHARS) -> str:
    """Clip pathological lines (minified bundles, base64 blobs) but say so."""
    if len(line) <= limit:
        return line
    dropped = len(line) - limit
    return f"{line[:limit]}… [+{dropped} chars truncated]"


def _clip_raw(raw: str, limit: int = MAX_RAW_BYTES) -> tuple[str, bool]:
    """Guard against a trace too large to hold twice in memory.

    Keeps the head (setup, image pull) and the much more valuable tail.
    """
    if len(raw) <= limit:
        return raw, False
    head = raw[: limit // 4]
    tail = raw[-(limit - limit // 4) :]
    marker = "\n… [pipelinemd: trace exceeded size limit, middle discarded] …\n"
    return head + marker + tail, True


def clean_trace(raw: str) -> CleanedTrace:
    """Parse a raw trace into numbered lines plus the metadata it carries."""
    raw_bytes = len(raw.encode("utf-8", errors="replace"))
    clipped, _was_clipped = _clip_raw(raw)
    raw_line_list = clipped.replace("\r\n", "\n").split("\n")

    lines: list[TraceLine] = []
    sections: list[Section] = []
    commands: list[str] = []
    open_sections: dict[str, tuple[int, int]] = {}  # name -> (start_line, ts)
    section_stack: list[str] = []

    exit_code: int | None = None
    failure_reason: str | None = None
    runner: str | None = None
    image: str | None = None
    expecting_runner_on = False

    cleaned_texts: list[str] = []
    metadata: list[tuple[int, str | None]] = []  # (raw_number, section)

    for raw_number, raw_line in enumerate(raw_line_list, start=1):
        text = strip_ansi(raw_line)
        text = _TIMESTAMP.sub("", text)

        # A single physical line can carry several markers back to back,
        # e.g. "section_end:..:a\rsection_start:..:b\r$ make".
        while True:
            match = _SECTION.match(text)
            if not match:
                break
            kind, ts_text, name, _flags = match.groups()
            ts = int(ts_text)
            next_line_number = len(cleaned_texts) + 1
            if kind == "start":
                open_sections[name] = (next_line_number, ts)
                section_stack.append(name)
            else:
                opened = open_sections.pop(name, None)
                if name in section_stack:
                    section_stack.remove(name)
                if opened is not None:
                    start_line, start_ts = opened
                    sections.append(
                        Section(
                            name=name,
                            start_line=start_line,
                            end_line=next_line_number,
                            duration_s=float(ts - start_ts),
                        )
                    )
            text = text[match.end() :]

        text = apply_overwrites(text)
        text = expand_tabs(text)
        text = text.rstrip()
        text = _truncate(text)

        cleaned_texts.append(text)
        metadata.append((raw_number, section_stack[-1] if section_stack else None))

    # Redact after cleaning (so escape codes cannot split a token) and before
    # anything else reads the text.
    cleaned_texts = redact_lines(cleaned_texts)

    for index, (text, (raw_number, section)) in enumerate(
        zip(cleaned_texts, metadata, strict=True), start=1
    ):
        lines.append(TraceLine(number=index, raw_number=raw_number, text=text, section=section))

        if command := _COMMAND.match(text):
            commands.append(command.group(1))

        if expecting_runner_on:
            expecting_runner_on = False
            if on_match := _RUNNER_ON.match(text):
                runner = f"{runner} on {on_match.group(1)}" if runner else on_match.group(1)
        if runner_match := _RUNNER.match(text):
            runner = runner_match.group(1)
            expecting_runner_on = True

        if image is None and (image_match := _IMAGE.search(text)):
            image = image_match.group(1).rstrip(".")

        if _JOB_FAILED.match(text):
            failure_reason = text
        if exit_match := _EXIT_CODE.search(text):
            exit_code = int(exit_match.group(1))

    # Sections still open when the trace ended - usually where the job died.
    for name, (start_line, _ts) in open_sections.items():
        sections.append(Section(name=name, start_line=start_line, end_line=None))
    sections.sort(key=lambda s: s.start_line)

    return CleanedTrace(
        lines=lines,
        sections=sections,
        commands=commands,
        exit_code=exit_code,
        failure_reason=failure_reason,
        runner=runner,
        image=image,
        raw_bytes=raw_bytes,
        raw_lines=len(raw_line_list),
    )

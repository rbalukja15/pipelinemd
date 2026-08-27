"""Find the part of a job trace that explains the failure.

A cleaned trace is still mostly noise: dependency resolution, test names that
passed, artifact uploads. This module scores every line for how much it looks
like a failure signal, grows a window around the strong ones, merges
overlapping windows, and spends a fixed line budget on the best of them. The
tail is always kept - gitlab-runner writes its verdict there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import EvidenceBlock, EvidenceLine, TraceLine

# (name, pattern, weight). Weights are additive: a line that both says
# "ERROR" and names an exit code outranks one that only says "failed".
SIGNALS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("job-failed", re.compile(r"^ERROR: Job failed"), 100.0),
    ("error-prefix", re.compile(r"^\s*(?:ERROR|FATAL)\b[: ]"), 40.0),
    ("fatal-git", re.compile(r"^\s*fatal:"), 35.0),
    ("npm-err", re.compile(r"^npm ERR!"), 25.0),
    ("traceback", re.compile(r"Traceback \(most recent call last\)"), 60.0),
    ("panic", re.compile(r"^\s*(?:panic|goroutine \d+ \[running\]):"), 55.0),
    ("go-test-fail", re.compile(r"^\s*--- FAIL:"), 40.0),
    ("pytest-failures", re.compile(r"^=+ (?:FAILURES|ERRORS|short test summary)"), 50.0),
    ("assertion", re.compile(r"\b(?:AssertionError|assert(?:ion)? failed)\b", re.I), 30.0),
    ("py-exception", re.compile(r"^\s*\w*(?:Error|Exception)\b.*:"), 28.0),
    ("compiler-error", re.compile(r"\berror(?:\[[A-Z0-9]+\])?\s*(?:TS\d+)?\s*:", re.I), 22.0),
    ("exit-code", re.compile(r"\bexit (?:code|status)\s+[1-9]"), 25.0),
    ("command-not-found", re.compile(r"command not found|: not found$"), 40.0),
    ("permission-denied", re.compile(r"[Pp]ermission denied"), 28.0),
    ("no-such-file", re.compile(r"No such file or directory"), 25.0),
    ("denied", re.compile(r"\b(?:denied|unauthorized|forbidden)\b", re.I), 22.0),
    (
        "timeout",
        re.compile(r"\b(?:timed out|timeout exceeded|context deadline exceeded)\b", re.I),
        25.0,
    ),
    ("killed", re.compile(r"\b(?:Killed|OOMKilled|out of memory)\b"), 30.0),
    ("no-space", re.compile(r"no space left on device", re.I), 45.0),
    (
        "cannot-connect",
        re.compile(r"\b(?:connection refused|could not connect|cannot connect)\b", re.I),
        28.0,
    ),
    ("failed-word", re.compile(r"\b(?:FAILED|FAILURE)\b"), 18.0),
    ("failed-lower", re.compile(r"\bfail(?:ed|ing|ure)\b", re.I), 9.0),
    ("error-word", re.compile(r"\berror\b", re.I), 8.0),
    ("exception-word", re.compile(r"\bexception\b", re.I), 8.0),
    ("stack-frame", re.compile(r'^\s*(?:at |File ")'), 4.0),
    ("caused-by", re.compile(r"^\s*Caused by:"), 20.0),
    ("warning", re.compile(r"^\s*WARNING\b", re.I), 1.0),
)

# Lines that use failure vocabulary while reporting success. Without these,
# a passing summary line ("0 errors, 0 warnings") outscores the real fault.
DAMPENERS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    (
        "zero-errors",
        re.compile(r"\b(?:0|no)\s+(?:errors?|failed|failures?|problems?)\b", re.I),
        -20.0,
    ),
    (
        "all-passed",
        re.compile(r"\b(?:all|\d+)\s+(?:tests?|checks?|specs?)\s+passed\b", re.I),
        -20.0,
    ),
    ("failures-zero", re.compile(r"\bFailures:\s*0\b|\bfailed:\s*0\b", re.I), -25.0),
    (
        "error-option",
        re.compile(r"--[\w-]*error|error[_-](?:log|page|handler|reporting)", re.I),
        -10.0,
    ),
    (
        "ignore-errors",
        re.compile(r"\b(?:ignoring|ignored|suppress(?:ed|ing)?)\b.*\berrors?\b", re.I),
        -12.0,
    ),
)

DEFAULT_THRESHOLD = 10.0
DEFAULT_MAX_LINES = 200
DEFAULT_TAIL_LINES = 30
# An exactly-repeated line carries no information the count does not.
COLLAPSE_MIN_RUN = 3
# Lines that differ only in their digits ("fetching package-1", "package-2")
# need a longer run before folding: enumerated *failures* look the same way,
# and folding three distinct failing tests into one would lose real signal.
COLLAPSE_FUZZY_MIN_RUN = 8
# Below this, splitting an oversized window produces two useless slivers.
MIN_SPLIT_LINES = 8
# Of a split allowance, how much goes to the head - the top of an error block
# names the fault; the bottom usually repeats it.
SPLIT_HEAD_SHARE = 0.6
_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True, slots=True)
class Anchor:
    line_number: int
    score: float
    signals: tuple[str, ...]


@dataclass(slots=True)
class Window:
    start: int
    end: int
    score: float
    label: str


def score_line(text: str) -> tuple[float, tuple[str, ...]]:
    """Score one line for failure-likeness. Returns (score, matched signals)."""
    if not text.strip():
        return 0.0, ()
    total = 0.0
    names: list[str] = []
    for name, pattern, weight in SIGNALS:
        if pattern.search(text):
            total += weight
            names.append(name)
    for name, pattern, weight in DAMPENERS:
        if pattern.search(text):
            total += weight
            names.append(name)
    return max(0.0, total), tuple(names)


def find_anchors(lines: list[TraceLine], threshold: float = DEFAULT_THRESHOLD) -> list[Anchor]:
    """Every line scoring at or above ``threshold``, in trace order."""
    anchors: list[Anchor] = []
    for line in lines:
        score, signals = score_line(line.text)
        if score >= threshold:
            anchors.append(Anchor(line.number, score, signals))
    return anchors


def _window_for(anchor: Anchor) -> tuple[int, int]:
    """How much context a hit deserves, by strength."""
    if anchor.score >= 60:
        return 15, 15
    if anchor.score >= 25:
        return 8, 8
    return 3, 4


def _merge(windows: list[Window], gap: int = 3) -> list[Window]:
    """Fuse windows that touch or nearly touch, keeping the best label."""
    if not windows:
        return []
    ordered = sorted(windows, key=lambda w: (w.start, w.end))
    merged = [ordered[0]]
    for window in ordered[1:]:
        last = merged[-1]
        if window.start <= last.end + gap:
            if window.score > last.score:
                last.label = window.label
            last.end = max(last.end, window.end)
            last.score = max(last.score, window.score)
        else:
            merged.append(window)
    return merged


def _split(window: Window, allowance: int) -> tuple[Window, Window]:
    """Cut an oversized window down to ``allowance`` lines, head and tail."""
    head_len = max(1, int(allowance * SPLIT_HEAD_SHARE))
    tail_len = max(1, allowance - head_len)
    head = Window(
        start=window.start,
        end=window.start + head_len - 1,
        score=window.score,
        label=window.label,
    )
    tail = Window(
        start=max(window.start + head_len, window.end - tail_len + 1),
        end=window.end,
        score=window.score,
        label=f"{window.label}:end",
    )
    return head, tail


def _last_meaningful_line(lines: list[TraceLine]) -> int:
    for line in reversed(lines):
        if line.text.strip():
            return line.number
    return lines[-1].number if lines else 0


def _normalise(text: str) -> str:
    """Comparison key for collapsing near-identical consecutive lines."""
    return _DIGITS.sub("0", text.strip())


def _collapse(lines: list[TraceLine], anchor_numbers: frozenset[int]) -> list[EvidenceLine]:
    """Fold runs of repeated lines into one entry with a repeat count.

    Two thresholds: an exact repeat folds after three, because the count says
    everything the copies would. A merely digit-similar repeat needs eight, and
    never folds anchors - an enumerated list of failing tests differs only in
    its digits too, and each entry matters.
    """
    out: list[EvidenceLine] = []
    index = 0
    while index < len(lines):
        head = lines[index]
        run = _run_length(lines, index, exact=True)
        if run < COLLAPSE_MIN_RUN:
            fuzzy = _run_length(lines, index, exact=False)
            has_anchor = any(
                lines[offset].number in anchor_numbers for offset in range(index, index + fuzzy)
            )
            run = fuzzy if (fuzzy >= COLLAPSE_FUZZY_MIN_RUN and not has_anchor) else 1

        out.append(
            EvidenceLine(
                number=head.number,
                text=head.text,
                section=head.section,
                is_anchor=head.number in anchor_numbers,
                repeat=max(1, run),
            )
        )
        index += max(1, run)
    return out


def _run_length(lines: list[TraceLine], start: int, *, exact: bool) -> int:
    """How many consecutive lines from ``start`` match the first one."""
    key = lines[start].text if exact else _normalise(lines[start].text)
    if not key.strip():
        return 1
    end = start + 1
    while end < len(lines):
        candidate = lines[end].text if exact else _normalise(lines[end].text)
        if candidate != key:
            break
        end += 1
    return end - start


def select_evidence(
    lines: list[TraceLine],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    max_lines: int = DEFAULT_MAX_LINES,
    tail_lines: int = DEFAULT_TAIL_LINES,
) -> tuple[list[EvidenceBlock], list[Anchor]]:
    """Pick the excerpt. Returns the blocks plus the anchors that drove them."""
    if not lines:
        return [], []

    anchors = find_anchors(lines, threshold)
    anchor_numbers = frozenset(anchor.line_number for anchor in anchors)
    total = lines[-1].number

    windows = [
        Window(
            start=max(1, anchor.line_number - before),
            end=min(total, anchor.line_number + after),
            score=anchor.score,
            label=anchor.signals[0] if anchor.signals else "signal",
        )
        for anchor in anchors
        for before, after in (_window_for(anchor),)
    ]

    # The runner's verdict lives in the last lines; never let the budget
    # squeeze it out.
    last_line = _last_meaningful_line(lines)
    tail = Window(
        start=max(1, last_line - tail_lines + 1),
        end=min(total, last_line),
        score=float("inf"),
        label="tail",
    )
    windows.append(tail)

    merged = _merge(windows)
    by_number = {line.number: line for line in lines}

    # Spend the budget on the strongest windows first, then restore order.
    chosen: list[Window] = []
    spent = 0
    for window in sorted(merged, key=lambda w: -w.score):
        remaining = max_lines - spent
        if remaining <= 0:
            break
        size = window.end - window.start + 1
        if size <= remaining:
            chosen.append(window)
            spent += size
        elif remaining >= MIN_SPLIT_LINES:
            # One window can be larger than the whole budget - hundreds of
            # consecutive error lines merge into a single span. Keep its head
            # and its end rather than letting it swallow everything.
            head, tail = _split(window, remaining)
            chosen.extend((head, tail))
            spent += remaining
    chosen.sort(key=lambda w: w.start)

    blocks: list[EvidenceBlock] = []
    for window in chosen:
        window_lines = [by_number[n] for n in range(window.start, window.end + 1) if n in by_number]
        if not window_lines:
            continue
        collapsed = _collapse(window_lines, anchor_numbers)
        blocks.append(EvidenceBlock(label=window.label, lines=tuple(collapsed)))
    return blocks, anchors

"""Turn raw terminal bytes into the text a human would actually have seen.

A gitlab-runner trace is a terminal recording, not a log file: it carries
colour codes, cursor moves, and progress bars that rewrite the same line
hundreds of times. Reading it as plain text massively overstates how much
content there is. These helpers replay it the way a terminal would.
"""

from __future__ import annotations

import re

# CSI: ESC [ params intermediates final. Covers colour (m), erase (K/J),
# cursor moves (A-H) - everything gitlab-runner emits.
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
# OSC: ESC ] ... terminated by BEL or ST. Used for window titles / hyperlinks.
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# Two-character escapes such as ESC ( B or ESC =.
_SHORT_ESC = re.compile(r"\x1b[@-Z\\-_]")
# Anything left over that a terminal would not print. \t \n \r \b are handled
# separately, so they are excluded here.
_CONTROL = re.compile(r"[\x00-\x07\x0b\x0c\x0e-\x1a\x1c-\x1f\x7f]")


def strip_ansi(text: str) -> str:
    """Remove escape sequences, leaving printable text plus \\t \\n \\r \\b."""
    text = _OSC.sub("", text)
    text = _CSI.sub("", text)
    text = _SHORT_ESC.sub("", text)
    return _CONTROL.sub("", text)


def apply_overwrites(line: str) -> str:
    """Replay carriage returns and backspaces within a single line.

    ``"loading 10%\\rloading 90%"`` becomes ``"loading 90%"``; a progress bar
    that rewrote one line 500 times collapses to its final frame. Overwriting
    is positional, so a short write over a long line leaves the tail intact -
    which is what the terminal would have shown.
    """
    if "\r" not in line and "\b" not in line:
        return line

    buf: list[str] = []
    col = 0
    for ch in line:
        if ch == "\r":
            col = 0
        elif ch == "\b":
            col = max(0, col - 1)
        else:
            if col < len(buf):
                buf[col] = ch
            else:
                buf.append(ch)
            col += 1
    return "".join(buf)


def expand_tabs(line: str, width: int = 8) -> str:
    return line.expandtabs(width)

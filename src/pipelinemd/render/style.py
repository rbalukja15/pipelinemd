"""Minimal ANSI styling.

Written by hand rather than pulled from a dependency: the core of this tool
has none, and a log reader that ships its own colour library while telling you
to strip colour codes would be a bit rich.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import IO, Literal

ColorChoice = Literal["auto", "always", "never"]

_CODES = {
    "reset": "\x1b[0m",
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue": "\x1b[34m",
    "magenta": "\x1b[35m",
    "cyan": "\x1b[36m",
}


@dataclass(frozen=True, slots=True)
class Style:
    enabled: bool

    def _wrap(self, text: str, *names: str) -> str:
        if not self.enabled or not text:
            return text
        prefix = "".join(_CODES[name] for name in names)
        return f"{prefix}{text}{_CODES['reset']}"

    def bold(self, text: str) -> str:
        return self._wrap(text, "bold")

    def dim(self, text: str) -> str:
        return self._wrap(text, "dim")

    def red(self, text: str) -> str:
        return self._wrap(text, "red")

    def green(self, text: str) -> str:
        return self._wrap(text, "green")

    def yellow(self, text: str) -> str:
        return self._wrap(text, "yellow")

    def blue(self, text: str) -> str:
        return self._wrap(text, "blue")

    def magenta(self, text: str) -> str:
        return self._wrap(text, "magenta")

    def cyan(self, text: str) -> str:
        return self._wrap(text, "cyan")

    def heading(self, text: str) -> str:
        return self._wrap(text, "bold", "cyan")


def make_style(choice: ColorChoice = "auto", stream: IO[str] | None = None) -> Style:
    """Decide whether to emit colour, honouring NO_COLOR and non-TTY output."""
    if choice == "never":
        return Style(enabled=False)
    if choice == "always":
        return Style(enabled=True)
    if os.environ.get("NO_COLOR"):
        return Style(enabled=False)
    target = stream if stream is not None else sys.stdout
    return Style(enabled=bool(getattr(target, "isatty", lambda: False)()))

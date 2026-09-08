"""Output formats: terminal, markdown, JSON."""

from .evidence import select_display_lines
from .json_out import SCHEMA_VERSION, render_json, report_to_dict
from .markdown import render_markdown
from .style import ColorChoice, Style, make_style
from .terminal import render_terminal

__all__ = [
    "SCHEMA_VERSION",
    "ColorChoice",
    "Style",
    "make_style",
    "render_json",
    "render_markdown",
    "render_terminal",
    "report_to_dict",
    "select_display_lines",
]

"""Output formats: terminal, markdown, JSON, HTML."""

from .evidence import select_display_lines
from .html import render_html
from .json_out import SCHEMA_VERSION, render_json, report_to_dict, run_to_dict
from .markdown import render_markdown, render_markdown_run_total
from .style import ColorChoice, Style, make_style
from .terminal import render_run_total, render_terminal

__all__ = [
    "SCHEMA_VERSION",
    "ColorChoice",
    "Style",
    "make_style",
    "render_html",
    "render_json",
    "render_markdown",
    "render_markdown_run_total",
    "render_run_total",
    "render_terminal",
    "report_to_dict",
    "run_to_dict",
    "select_display_lines",
]

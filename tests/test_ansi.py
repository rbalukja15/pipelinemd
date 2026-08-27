"""Terminal replay: escape stripping and in-line overwrites."""

from __future__ import annotations

import pytest

from pipelinemd.distill.ansi import apply_overwrites, expand_tabs, strip_ansi


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("\x1b[31mred\x1b[0m", "red"),
        ("\x1b[0;32;1mbold green\x1b[0;m", "bold green"),
        ("before\x1b[0Kafter", "beforeafter"),
        ("\x1b[2J\x1b[Hcleared", "cleared"),
        ("\x1b]8;;https://example.com\x07link\x1b]8;;\x07", "link"),
        ("plain text", "plain text"),
        ("null\x00byte", "nullbyte"),
    ],
)
def test_strip_ansi(raw: str, expected: str) -> None:
    assert strip_ansi(raw) == expected


def test_strip_ansi_keeps_meaningful_whitespace() -> None:
    assert strip_ansi("a\tb\nc\rd") == "a\tb\nc\rd"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("loading 10%\rloading 90%", "loading 90%"),
        ("abcdef\rXY", "XYcdef"),
        ("abc\b\bZ", "aZc"),
        ("no control chars", "no control chars"),
        ("\rstart at zero", "start at zero"),
        ("trailing\r", "trailing"),
    ],
)
def test_apply_overwrites(raw: str, expected: str) -> None:
    assert apply_overwrites(raw) == expected


def test_apply_overwrites_collapses_a_progress_bar() -> None:
    frames = "\r".join(f"downloading {pct}%" for pct in range(0, 101, 5))
    assert apply_overwrites(frames) == "downloading 100%"


def test_apply_overwrites_is_a_noop_without_control_chars() -> None:
    text = "a" * 5000
    assert apply_overwrites(text) is text


def test_expand_tabs() -> None:
    assert expand_tabs("a\tb", 4) == "a   b"

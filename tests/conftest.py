"""Shared fixtures: the recorded traces every layer is tested against."""

from __future__ import annotations

from pathlib import Path

import pytest

from .fixtures.secrets import build_secrets_trace

TRACES = Path(__file__).parent / "fixtures" / "traces"


def load_trace(name: str) -> str:
    path = TRACES / name
    if not path.suffix:
        path = path.with_suffix(".log")
    return path.read_text(encoding="utf-8")


@pytest.fixture
def trace() -> object:
    return load_trace


@pytest.fixture(params=sorted(p.name for p in TRACES.glob("*.log")))
def any_trace(request: pytest.FixtureRequest) -> tuple[str, str]:
    """Every recorded trace, one per test run."""
    return request.param, load_trace(request.param)


@pytest.fixture
def secrets_trace() -> str:
    """A trace that leaks credentials, built at test time rather than stored.

    Committing a file with real-shaped tokens would trip push protection - see
    tests/fixtures/secrets.py.
    """
    return build_secrets_trace()

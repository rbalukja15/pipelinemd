# Developer entry points. Everything here runs offline.
#
# Tools are invoked through the interpreter rather than by bare name: a
# standalone `pytest` or `mypy` on PATH may belong to a different environment
# than the one pipelinemd is installed into, and then the suite fails to import
# the package it is meant to be testing.
PYTHON ?= python3
.PHONY: help install lint format typecheck test eval check clean

help:
	@echo "install    editable install with dev extras"
	@echo "lint       ruff check"
	@echo "format     ruff format (rewrites files)"
	@echo "typecheck  mypy --strict"
	@echo "test       pytest"
	@echo "eval       score the deterministic pipeline against corpus/"
	@echo "check      lint + typecheck + test, as CI runs them"

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests

format:
	$(PYTHON) -m ruff format src tests

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest

eval:
	$(PYTHON) -m pipelinemd eval

check: lint typecheck test

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

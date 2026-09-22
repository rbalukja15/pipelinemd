# Developer entry points. Everything here runs offline.
#
# Tools are invoked through the interpreter rather than by bare name: a
# standalone `pytest` or `mypy` on PATH may belong to a different environment
# than the one pipelinemd is installed into, and then the suite fails to import
# the package it is meant to be testing. CI calls these same targets, so that
# fix applies there too and the two cannot drift.
PYTHON ?= python3
PYTEST_ARGS ?=

# The regression floor for `make gate`. rule@1 is 53/53 today, so 0.92 is 49/53
# - the gate trips on the fifth regression. That headroom exists for one
# reason: the corpus is meant to grow with observed traces, which will be
# harder than the authored ones, and a gate that goes red the moment someone
# commits a real failing log discourages the contribution this project most
# needs. Single-case regressions stay visible either way, since `make eval`
# names every miss.
#
# Adding hard traces will eventually push the rate under this floor, and the
# right response is to lower it in a commit that says why. That is the point:
# it makes the number move in review rather than silently.
MIN_RULE_ACCURACY ?= 0.92

# Known gaps are excluded from every rate, so a rule that starts firing on one
# moves no number. Zero tolerance here: the catalog growing a confident wrong
# answer where it used to stay silent is a regression worth failing over.
MAX_GAP_FALSE_POSITIVES ?= 0

.PHONY: help install lint format typecheck test eval gate check clean

help:
	@echo "install    editable install with dev extras"
	@echo "lint       ruff check + ruff format --check"
	@echo "format     ruff format (rewrites files)"
	@echo "typecheck  mypy --strict"
	@echo "test       pytest"
	@echo "eval       score the deterministic pipeline against corpus/"
	@echo "gate       eval, failing below rule@1 $(MIN_RULE_ACCURACY)"
	@echo "check      lint + typecheck + test + gate, the targets CI runs"

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
	$(PYTHON) -m pytest $(PYTEST_ARGS)

eval:
	$(PYTHON) -m pipelinemd eval

gate:
	$(PYTHON) -m pipelinemd eval \
		--min-rule-accuracy $(MIN_RULE_ACCURACY) \
		--max-gap-false-positives $(MAX_GAP_FALSE_POSITIVES)

check: lint typecheck test gate

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

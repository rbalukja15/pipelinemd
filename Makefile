# Developer entry points. Everything here runs offline.
#
# Tools are invoked through the interpreter rather than by bare name: a
# standalone `pytest` or `mypy` on PATH may belong to a different environment
# than the one pipelinemd is installed into, and then the suite fails to import
# the package it is meant to be testing. CI calls these same targets, so that
# fix applies there too and the two cannot drift.
PYTHON ?= python3
PYTEST_ARGS ?=

# The regression floor for `make gate`. rule@1 is 55/55 today, so 0.92 is 51/55
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

# The class floor. Class is the input to the fix type, and yaml_patch is what
# #24's MR generator acts on, so a regression here is the one with the sharpest
# downside - it should fail a build, not just move a printed number. Class is
# 51/62 today; 0.78 is 49/62, so the gate trips on the third regression. Same
# rule as above: lowering it in a commit that says why is the intended response
# to adding hard traces, not a failure.
MIN_CLASS_ACCURACY ?= 0.78

# The evidence floor. Unlike rule@1, the evidence rate does not depend on a
# rule existing for the failure: it asks whether the line that explains it
# survived into the excerpt, which is all the model is ever shown. A miss means
# a diagnosis written without its cause, the failure the distiller exists to
# prevent, so the floor is every case. A hard observed trace that the distiller
# cannot keep is a distiller bug to fix; if it cannot be fixed yet, lower this
# in a commit that names the case.
MIN_EVIDENCE_RATE ?= 1.0

# Known gaps are excluded from every rate, so a rule that starts firing on one
# moves no number. Zero tolerance here: the catalog growing a confident wrong
# answer where it used to stay silent is a regression worth failing over.
MAX_GAP_FALSE_POSITIVES ?= 0

# The image `make image` builds and `make image-smoke` runs. PACKAGE_VERSION
# is read from the same line hatch stamps the wheel with, so the smoke test
# checks the image reports the version the package claims. Not plain VERSION:
# that name is common enough in a CI environment to be picked up by accident.
IMAGE ?= pipelinemd:dev
PACKAGE_VERSION ?= $(shell sed -n 's/^__version__ = "\(.*\)"$$/\1/p' src/pipelinemd/__init__.py)
SMOKE_TRACE ?= corpus/traces/runner-oom-killed.log

.PHONY: help install lint format typecheck test eval gate check dist image image-smoke \
	release-notes clean

help:
	@echo "install        editable install with dev extras"
	@echo "lint           ruff check + ruff format --check"
	@echo "format         ruff format (rewrites files)"
	@echo "typecheck      mypy --strict"
	@echo "test           pytest"
	@echo "eval           score the deterministic pipeline against corpus/"
	@echo "gate           eval, failing below rule@1 $(MIN_RULE_ACCURACY), class $(MIN_CLASS_ACCURACY) or evidence $(MIN_EVIDENCE_RATE)"
	@echo "check          lint + typecheck + test + gate, CI's test job"
	@echo "dist           sdist + wheel into dist/, then twine check (needs build, twine)"
	@echo "image          build the Docker image as $(IMAGE) (needs docker)"
	@echo "image-smoke    run $(IMAGE) the ways CI and GitLab will (needs docker)"
	@echo "release-notes  check \$$TAG against the version and changelog, print its notes"

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check src tests scripts
	$(PYTHON) -m ruff format --check src tests scripts

format:
	$(PYTHON) -m ruff format src tests scripts

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest $(PYTEST_ARGS)

eval:
	$(PYTHON) -m pipelinemd eval

gate:
	$(PYTHON) -m pipelinemd eval \
		--min-rule-accuracy $(MIN_RULE_ACCURACY) \
		--min-class-accuracy $(MIN_CLASS_ACCURACY) \
		--min-evidence-rate $(MIN_EVIDENCE_RATE) \
		--max-gap-false-positives $(MAX_GAP_FALSE_POSITIVES)

check: lint typecheck test gate

# --strict turns twine's warnings into failures: PyPI will not let a version be
# uploaded twice, so a broken long description is cheaper to catch here.
dist:
	rm -rf dist
	$(PYTHON) -m build
	$(PYTHON) -m twine check --strict dist/*

# Not part of `check`: that has to run anywhere Python does, and these need a
# Docker daemon. CI runs them in a job of their own. buildx with --load works
# with both the default builder and a docker-container one, and leaves the
# result where `docker run` can find it.
image:
	docker buildx build --load --build-arg VERSION=$(PACKAGE_VERSION) -t $(IMAGE) .

# Each line is a promise the README makes about the image. The output is
# captured before it is searched, so a container that fails after printing
# the right text still fails the target.
image-smoke:
	@echo "--version reports $(PACKAGE_VERSION)"
	@out=$$(docker run --rm $(IMAGE) --version) && echo "$$out" && \
		test "$$out" = "pipelinemd $(PACKAGE_VERSION)"
	@echo "the default command runs"
	docker run --rm $(IMAGE) > /dev/null
	@echo "distill - reads a trace on stdin, offline, and finds its rule and evidence"
	@out=$$(docker run --rm -i --network none $(IMAGE) distill --color never - < $(SMOKE_TRACE)) && \
		echo "$$out" | grep -F "runner.oom-killed" && echo "$$out" | grep -E "^Evidence +[0-9]+ of"
	@echo "the [llm] extra is installed"
	docker run --rm --entrypoint python $(IMAGE) -c "import anthropic; print(anthropic.__version__)"
	@echo "it does not run as root"
	@uid=$$(docker run --rm --entrypoint id $(IMAGE) -u) && echo "uid $$uid" && test "$$uid" != 0
	@echo "a shell script runs with the entrypoint cleared, as GitLab runs one"
	docker run --rm --entrypoint "" $(IMAGE) sh -c 'pipelinemd --version'

# The tag comes from the environment, never the command line, and is only ever
# expanded by the shell inside quotes: a tag name is text someone else chose,
# and make would paste it into the recipe unquoted.
release-notes:
	@$(PYTHON) scripts/release_notes.py "$$TAG"

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

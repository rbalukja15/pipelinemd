# AGENTS.md

Conventions for agents and contributors working in this repository. The
README says what pipelinemd does; this says how changes to it are made.

## The project

pipelinemd diagnoses failed GitLab CI jobs. A deterministic distiller turns
the trace into a short, redacted excerpt and a rule catalog matches it against
known failure signatures; an optional Claude layer then explains what the
rules found, citing evidence lines. Python 3.11+, packaged with hatch, code in
`src/pipelinemd/`, tests in `tests/`, a labelled failure corpus in `corpus/`.
How the pieces fit is in [docs/architecture.md](docs/architecture.md), why
they are built this way in [docs/adr/](docs/adr/README.md).

## Commands

Everything in the Makefile runs offline unless it says otherwise. Tools run
through `$(PYTHON) -m`, so they use the environment pipelinemd is installed
into; CI calls the same targets.

| Command | What it does |
| --- | --- |
| `make install` | Editable install with the dev extras. |
| `make lint` | `ruff check` and `ruff format --check` over src, tests and scripts. |
| `make format` | `ruff format`, rewriting files. |
| `make typecheck` | `mypy --strict`. |
| `make test` | pytest; `PYTEST_ARGS` passes flags through. |
| `make eval` | Scores the distiller and rules against `corpus/`, naming every miss. |
| `make gate` | `eval` with CI's floors: rule@1, class, evidence rate, gap false positives. |
| `make check` | lint + typecheck + test + gate. CI's test job; must pass before a PR. |
| `make eval-llm` | Scores the Claude diagnosis too. Billed, needs `ANTHROPIC_API_KEY`, capped by `MAX_LLM_COST` ($5). Never part of `check`. |
| `make demo` | Builds the demo site into `build/demo`, as the Pages workflow does. |
| `make dist` | sdist and wheel into `dist/`, then `twine check --strict`. |
| `make image`, `make image-smoke` | Build the Docker image and run the README's promises against it. Need Docker. |
| `make release-notes` | Checks `$TAG` against the version and changelog and prints its notes. |
| `make clean` | Removes caches and build output. |

The gate's floors are Makefile variables with the reasoning beside each. If a
new, harder corpus case pushes a rate under its floor, lower the floor in a
commit that says why; do not loosen it quietly.

Further reading in `docs/`: [evaluation.md](docs/evaluation.md) (what the
numbers mean), [autofix.md](docs/autofix.md) (the merge-request path),
[releasing.md](docs/releasing.md) (how a version ships),
[dogfood.md](docs/dogfood.md) and [devlog.md](docs/devlog.md).

## Conventions

- **Conventional Commits**, subject under 72 characters, with a scope where
  one fits: `feat(render): …`, `fix(rules): …`, `docs: …`, `chore(release): …`.
- **One issue per PR.** The PR template's `Closes #N` names it.
- **Branches** are `feat/#N-slug`, `fix/#N-slug`, or `chore/…` for work with
  no feature behind it.
- **Every PR adds an entry under `[Unreleased]` in
  [CHANGELOG.md](CHANGELOG.md)**, in the existing style: what changed for a
  user, in prose, ending with the issue link. A release is refused while its
  section is missing.
- **The plugin version equals the package version.**
  `plugins/pipelinemd/.claude-plugin/plugin.json` repeats `__version__` from
  `src/pipelinemd/__init__.py`; bump both together.
  `tests/test_plugin.py::test_plugin_version_matches_the_package` fails while
  they differ. See [docs/releasing.md](docs/releasing.md).
- **Zero runtime dependencies in the core.** `dependencies = []` in
  `pyproject.toml` stays empty. `anthropic` lives in the `[llm]` extra and is
  imported only inside `diagnose/claude.py`, behind `available()`. Fetching,
  distilling and rule matching use the standard library alone.
- **`mypy --strict` and ruff are clean.** No new `type: ignore` or `noqa`
  without a comment saying why.
- **Comments and docstrings say why**, not what. Match the tone of the
  existing modules: short, plain, specific.

## Per-issue workflow

1. Branch from an up-to-date `main`: `git switch -c feat/#N-slug origin/main`.
2. Make the change. Keep it to the one issue.
3. Add or update tests beside it. A new rule gets a fixture under
   `tests/fixtures/traces/` and a class in `RULE_CLASS` in `taxonomy.py`
   (README, *Contributing a rule*).
4. Run `make check` until it passes. Run `make eval` if rules, the distiller
   or the corpus changed, and read the misses, not just the totals.
5. Add the CHANGELOG `[Unreleased]` entry.
6. Open the PR with the template filled in: what, the issue, how to verify.

## Product rules

These are what the project is for. A change that weakens one needs an ADR,
not just a code review.

**The model never invents a diagnosis.** Rules run first and decide the
class; the model explains what they found and which signal is cause rather
than fallout. The prompt treats rule matches as a strong prior and carries
only distilled evidence, never the raw trace (`diagnose/prompt.py`). The
classification comes from the top rule and retry history alone
(`taxonomy.classify_hits`); the model's class is shown beside it, never
applied (`taxonomy.model_disagreement`). Flaky comes from retry history, and
the model may not answer it (`taxonomy.MODEL_CLASSES`). See
[ADR-0001](docs/adr/0001-stack-and-scope.md).

**Every diagnosis cites evidence lines, and code checks them.**
`diagnose/citations.py` resolves each cited number against the lines printed
in the excerpt, and nothing else. `diagnose/claude.py` (`_to_diagnosis`)
rejects a diagnosis whose citations all fail to resolve; partly invented ones
are reported beside it, not dropped, and lower the confidence
(`assessment.py`). Never widen resolution to the whole trace or to the inside
of a collapsed run.

**Read-only by default.** `gitlab/client.py` only reads, with a `read_api`
token. The one write path is `diagnose --open-mr`: `autofix.py` with
`gitlab/write.py`, a separate `$PIPELINEMD_WRITE_TOKEN`, and every gate in
[docs/autofix.md](docs/autofix.md), in order: `yaml_patch` from the rules,
high confidence with a grounded diagnosis, the `pipelinemd-autofix` label, a
small change with no literal credential, CI Lint, and a new branch. It opens a
draft MR and never merges or pushes to an existing branch. The rules for any
write are in [ADR-0002](docs/adr/0002-gitlab-access.md); a new write follows
them or amends the ADR first.

**Credentials are redacted at read time.** `distill/redact.py` masks
credential-shaped text, called from `distill/trace.py` before evidence is
selected, so a secret never reaches the prompt, the terminal, the reports or
the JSON. A new output path reads the redacted lines, never the raw trace. A
new credential shape gets a pattern and a case in `tests/test_redact.py`.

**The corpus stays honest.** [corpus/README.md](corpus/README.md) governs it:

- Every case carries `provenance`. Authored cases are a regression signal,
  not a measurement; do not quote them as real-world accuracy.
- Redact a trace before committing it. Read-time masking does not cover the
  file on disk.
- Gaps (`"expected_rule": null`) stay in the corpus, with notes that say why
  no rule fires. Adding a rule flips a gap to a real id.
- Never delete or relabel a case to lift a number. A case that regresses is
  fixed in the code, or the floor is lowered in a commit that names it.

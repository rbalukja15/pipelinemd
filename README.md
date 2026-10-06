# pipelinemd

[![CI](https://github.com/rbalukja15/pipelinemd/actions/workflows/ci.yml/badge.svg)](https://github.com/rbalukja15/pipelinemd/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/rbalukja15/pipelinemd/blob/main/LICENSE)

**GitLab CI/CD failure doctor** — takes a failed pipeline, works out what
actually broke, and tells you how to fix it.

A failed job's log is a terminal recording, not a report. It can run to tens of
thousands of lines, most of them progress bars that rewrote themselves five
hundred times, and the one line that matters is somewhere in the middle.
pipelinemd does two things about that:

1. **A deterministic distiller** replays the trace the way a terminal would,
   strips the noise, scores every line for failure-likeness, and keeps only the
   regions that explain the outcome — then matches them against a catalog of
   **59 known CI failure signatures**, each with a real fix.
   Every report is placed in one of seven **v1 failure classes** — `yaml`,
   `ci_vars`, `image_pull`, `cache_artifact`, `test`, `runner`, `flaky` — with
   the kind of fix it wants. Flaky comes from GitLab's retry history, not from
   a guess: if another attempt of the same job passed on the same commit, it
   says so.
2. **An optional Claude diagnosis** reads only that distilled evidence and
   names the root cause, separating the actual fault from its fallout — and
   must cite the evidence lines it relied on. A diagnosis that cites nothing
   real is rejected rather than reported.

Every report says how far to trust it and what it cost. The **confidence** is
never more than the weakest signal behind it; at low, the report says **needs
human review** and why. The **cost** is $0 for rules alone, and an estimate
from the API's own token counts when Claude was asked.

The first half needs no API key, no model, and **no third-party packages at
all**. The second is the upgrade.

```
$ pipelinemd diagnose https://gitlab.com/acme/web/-/jobs/98765

pipelinemd  build (test) #98765
  acme/web · ref main · exit code 1 · 47s
  ERROR: Job failed: exit code 1
  test → code_patch  ·  high  ·  from npm.lockfile-out-of-sync
  confidence high
  est. cost $0.0519 · claude-opus-5 · 5,210 in / 1,034 out tokens

Diagnosis   high confidence · dependency

  npm ci refused to install because package-lock.json no longer matches package.json.

  Line 41203 shows npm rejecting the install outright rather than resolving it.
  The lockfile still pins react@17 while package.json now asks for ^18 (line
  41211), which is the change that broke the pair.

Suggested fixes
  1. Regenerate the lockfile and commit it

       npm install
       git add package-lock.json && git commit -m 'Regenerate lockfile'

Rule matches
  ● npm.lockfile-out-of-sync  package-lock.json is out of sync with package.json  L41203

Evidence   9 of 41,284 lines · 100.0% reduced
```

---

## Install

```bash
pip install pipelinemd              # core: distiller + rules, zero dependencies
pip install 'pipelinemd[llm]'       # adds the Claude diagnosis layer
```

Requires Python 3.11+.

Or skip Python altogether. The image has the `[llm]` extra installed, runs as
an unprivileged user, and is built for amd64 and arm64:

```bash
docker run --rm ghcr.io/rbalukja15/pipelinemd:0.1 --version
docker run --rm -i ghcr.io/rbalukja15/pipelinemd:0.1 distill - < build.log
```

The image runs as uid 10001, so to save a report redirect stdout rather than
passing `-o` into a mounted directory, which that user may not be allowed to
write to:

```bash
docker run --rm -i ghcr.io/rbalukja15/pipelinemd:0.1 distill - < build.log > report.txt
```

On rootful Linux Docker, `--user "$(id -u):$(id -g)"` also lets `-o` write into
a mount; rootless Docker and Podman map that uid elsewhere, so redirect there.

Tags follow the package: `0.1.0` exactly, `0.1` for the latest patch release,
and `latest`. The image and the PyPI package are published by one workflow
from one commit, so a version means the same thing in both
([docs/releasing.md](https://github.com/rbalukja15/pipelinemd/blob/main/docs/releasing.md)).

## Use

### Diagnose a failed job or pipeline

```bash
pipelinemd diagnose https://gitlab.com/acme/web/-/jobs/98765
pipelinemd diagnose https://gitlab.com/acme/web/-/pipelines/12345   # picks the failed job
pipelinemd diagnose --project acme/web --pipeline 12345
```

Give it a pipeline and it finds the failed jobs for you; with more than one it
diagnoses the first and names the rest (`--all-jobs` does them all).

Credentials come from `--token`, `$PIPELINEMD_TOKEN`, `$GITLAB_TOKEN`,
`$GITLAB_PRIVATE_TOKEN` or `$CI_JOB_TOKEN` — in that order, with the right
header for each. Public projects need no token at all.

### Distil a log you already have — fully offline

```bash
pipelinemd distill build.log
kubectl logs job/ci-run | pipelinemd distill -
```

No network, no model, no key. Useful on its own for shrinking a log before
pasting it anywhere.

### Score it against the corpus

```bash
make eval
```

Runs the distiller and rule engine over 62 labelled traces in [`corpus/`](https://github.com/rbalukja15/pipelinemd/blob/main/corpus/README.md)
and reports rule@1, evidence hit rate and exit-code accuracy per failure class.
Offline and deterministic. Results and their caveats: [docs/evaluation.md](https://github.com/rbalukja15/pipelinemd/blob/main/docs/evaluation.md).

`make gate` is the same run with CI's thresholds, and runs on every push — a
catalog regression fails the build rather than being noticed later.

### Browse the rule catalog

```bash
pipelinemd rules                        # all 59, grouped by category
pipelinemd rules --category dependency
pipelinemd rules --search docker
pipelinemd explain npm.eresolve         # one rule in full, patterns included
```

### Inside GitLab CI

Run with no target at all and it diagnoses the pipeline it is running in:

```yaml
diagnose:
  stage: .post
  image:
    name: ghcr.io/rbalukja15/pipelinemd:0.1
    entrypoint: [""]
  when: on_failure
  script:
    - pipelinemd diagnose --format markdown -o diagnosis.md
  artifacts:
    when: always
    paths: [diagnosis.md]
```

Nothing is installed when the job runs, so a failure is diagnosed even while
PyPI is slow or unreachable. The `entrypoint: [""]` matters: the image's
entrypoint is the CLI, and GitLab needs a shell to run `script:`.

`CI_JOB_TOKEN` and `CI_PIPELINE_ID` are picked up automatically. Set
`ANTHROPIC_API_KEY` as a masked CI variable to enable the diagnosis layer.

Any image with Python 3.11+ works too, at the cost of an install on every
failure:

```yaml
diagnose:
  stage: .post
  image: python:3.12-slim
  when: on_failure
  script:
    - pip install 'pipelinemd[llm]'
    - pipelinemd diagnose --format markdown -o diagnosis.md
  artifacts:
    when: always
    paths: [diagnosis.md]
```

A ready-made job is in [`examples/gitlab-ci-diagnose.yml`](https://github.com/rbalukja15/pipelinemd/blob/main/examples/gitlab-ci-diagnose.yml).

### In Claude Code

The repository is also a Claude Code plugin marketplace. Install the plugin
once, from inside Claude Code:

```
/plugin marketplace add rbalukja15/pipelinemd
/plugin install pipelinemd@pipelinemd
```

Then paste a failed job URL, or point at a log, and ask why it failed. The
`pipelinemd` skill runs `uvx pipelinemd diagnose <url> --no-llm` (or `distill`
on a log file, such as one saved with `gh run view --log-failed`), and Claude
works from the distilled evidence and matched rules rather than the whole log,
then fixes the cause in your repository. The rules-only run needs no API key;
set `GITLAB_TOKEN` for private projects. It needs [uv](https://docs.astral.sh/uv/);
the skill falls back to an installed `pipelinemd`, `pipx` or the image.

The skill lives in [`plugins/pipelinemd/`](https://github.com/rbalukja15/pipelinemd/tree/main/plugins/pipelinemd).

## Output formats

| `--format` | For |
| --- | --- |
| `terminal` (default) | Reading it yourself. Colour honours `NO_COLOR` and non-TTY output. |
| `markdown` | Pasting into a merge request or issue. Collapsible evidence. |
| `json` | Other tooling. Versioned via `schema_version`. One job is one report object; several (`--all-jobs`) are `{"reports": [...], "cost": {...}}`, with the run's total cost beside them. |

## How the distiller works

Each stage is pure, independently testable, and does one thing:

| Stage | What it removes or adds |
| --- | --- |
| **ANSI** | Colour codes, cursor moves, erase sequences, OSC hyperlinks. |
| **Overwrites** | Replays `\r` and `\b` positionally — a progress bar that rewrote one line 500 times collapses to its final frame. |
| **Sections** | Parses `section_start`/`section_end` markers into structured spans with durations, and attributes every line to the section it ran in. |
| **Timestamps** | Strips the runner's optional per-line RFC3339 prefix. |
| **Redaction** | Masks credential-shaped substrings *before* anything leaves the process. |
| **Scoring** | Weights every line against 27 failure signals and 5 dampeners, so `0 failed, 12 passed` does not outrank the real fault. |
| **Windowing** | Grows context around strong signals, merges overlapping windows, spends a fixed line budget on the best, and always keeps the tail — the runner writes its verdict there. |
| **Collapsing** | Folds runs of near-identical lines into one entry with a repeat count. |

Same trace in, same evidence out — which is what makes it cheap to cache and
safe to assert on in tests.

## Redaction

pipelinemd can send evidence to an LLM, and you will paste its output into
merge requests. GitLab masks *known* CI variables; anything a build tool prints
itself does not get that protection. So before evidence reaches the prompt, the
terminal, or the JSON output, these shapes are masked:

GitLab tokens (`glpat-`, `glrt-`, …) · GitHub tokens · AWS access key ids ·
Slack tokens · JWTs · credentials embedded in URLs · `Authorization:` headers ·
`--password`/`--token` style flags · `KEY=value` where the key names a secret ·
PEM private key blocks.

Values that only look secret-shaped (`SECRET=false`, `***`) are left alone.
This reduces exposure; it is not a guarantee — treat traces from untrusted
pipelines accordingly.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | A report was produced. |
| `2` | Usage error — bad URL, missing argument, unknown rule. |
| `3` | GitLab error — unreachable, unauthorised, not found. |
| `4` | Nothing to diagnose — the pipeline has no failed jobs. |

A failed Claude call is **not** fatal: pipelinemd warns on stderr and reports
the deterministic findings anyway.

For how the pieces fit together, see [docs/architecture.md](https://github.com/rbalukja15/pipelinemd/blob/main/docs/architecture.md);
for why they are built this way, the decision records starting with
[ADR-0001](https://github.com/rbalukja15/pipelinemd/blob/main/docs/adr/0001-stack-and-scope.md);
for how a version reaches PyPI and the image, [docs/releasing.md](https://github.com/rbalukja15/pipelinemd/blob/main/docs/releasing.md);
and for how the project got here, the [devlog](https://github.com/rbalukja15/pipelinemd/blob/main/docs/devlog.md).

## Design notes

- **The core has no dependencies.** Not "few" — none. It installs into any
  runner image without dragging a tree behind it, which matters for a tool
  whose whole job is to run in someone else's broken build.
- **Rules first, model second.** Every rule fires without a network call. The
  model is asked to do only what rules cannot: decide which of several signals
  is the cause and which is the consequence.
- **The model never sees a raw trace.** It sees distilled, redacted evidence
  plus what the rules already concluded — which keeps requests small and cheap,
  and keeps the model's effort on the judgement call.
- **A diagnosis must point at something.** Every diagnosis cites line numbers,
  and every cited number is resolved against the excerpt the model was shown.
  Cite nothing real and the diagnosis is rejected; cite a mix and the invented
  numbers are printed alongside it. Confident prose is easy; a claim you can
  check is the product.
- **Confidence is earned, not asserted.** An analysis is only as confident as
  its weakest part — the rule, the model's own rating — and loses a level for
  each sign of trouble. It stays high/medium/low: a decimal would claim a
  calibration nothing here has, and `make eval` reports how often each level
  is right instead.
- **The distiller is pure.** No clock, no network, no randomness.

## Contributing a rule

Rules live in `src/pipelinemd/rules/catalog.py`. A good one is narrow: anchor
`patterns` to text the tool actually prints, write `explanation` as *why this
happens*, and make each entry of `fixes` something someone can do. Add a
fixture under `tests/fixtures/traces/` and assert the rule fires on it.

Then give it a v1 class in `RULE_CLASS` in `src/pipelinemd/taxonomy.py` — the
suite fails until you do. Choose from what the failure *is*, and only use
`flaky` if it is transient whatever the environment; anything that is merely
often transient is flaky only when retry history says so.

## License

MIT — see [LICENSE](https://github.com/rbalukja15/pipelinemd/blob/main/LICENSE).

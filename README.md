# pipelinemd

[![CI](https://github.com/rbalukja15/pipelinemd/actions/workflows/ci.yml/badge.svg)](https://github.com/rbalukja15/pipelinemd/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**GitLab CI/CD failure doctor** — takes a failed pipeline, works out what
actually broke, and tells you how to fix it.

A failed job's log is a terminal recording, not a report. It can run to tens of
thousands of lines, most of them progress bars that rewrote themselves five
hundred times, and the one line that matters is somewhere in the middle.
pipelinemd does two things about that:

1. **A deterministic distiller** replays the trace the way a terminal would,
   strips the noise, scores every line for failure-likeness, and keeps only the
   regions that explain the outcome — then matches them against a catalog of
   **58 known CI failure signatures**, each with a real fix.
2. **An optional Claude diagnosis** reads only that distilled evidence and
   names the root cause, separating the actual fault from its fallout.

The first half needs no API key, no model, and **no third-party packages at
all**. The second is the upgrade.

```
$ pipelinemd diagnose https://gitlab.com/acme/web/-/jobs/98765

pipelinemd  build (test) #98765
  acme/web · ref main · exit code 1 · 47s
  ERROR: Job failed: exit code 1

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

### Browse the rule catalog

```bash
pipelinemd rules                        # all 58, grouped by category
pipelinemd rules --category dependency
pipelinemd rules --search docker
pipelinemd explain npm.eresolve         # one rule in full, patterns included
```

### Inside GitLab CI

Run with no target at all and it diagnoses the pipeline it is running in:

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

`CI_JOB_TOKEN` and `CI_PIPELINE_ID` are picked up automatically. Set
`ANTHROPIC_API_KEY` as a masked CI variable to enable the diagnosis layer.

A ready-made job is in [`examples/gitlab-ci-diagnose.yml`](examples/gitlab-ci-diagnose.yml).

## Output formats

| `--format` | For |
| --- | --- |
| `terminal` (default) | Reading it yourself. Colour honours `NO_COLOR` and non-TTY output. |
| `markdown` | Pasting into a merge request or issue. Collapsible evidence. |
| `json` | Other tooling. Versioned via `schema_version`. |

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

For how the pieces fit together, see [docs/architecture.md](docs/architecture.md).

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
- **The distiller is pure.** No clock, no network, no randomness.

## Contributing a rule

Rules live in `src/pipelinemd/rules/catalog.py`. A good one is narrow: anchor
`patterns` to text the tool actually prints, write `explanation` as *why this
happens*, and make each entry of `fixes` something someone can do. Add a
fixture under `tests/fixtures/traces/` and assert the rule fires on it.

## License

MIT — see [LICENSE](LICENSE).

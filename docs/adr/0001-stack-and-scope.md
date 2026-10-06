# 0001. Build a GitLab CI failure doctor as a deterministic core with the model at the edge

- **Status:** Accepted
- **Date:** 2026-10-06

This records decisions taken when the project started in August 2026, and one
confirmed on 2026-10-05, when the hosted service in the original plan was
dropped in favour of the CLI.

## Context

**The problem.** When a CI job fails, the person who has to fix it opens a log
that can run to tens of thousands of lines. Most of it is progress bars that
rewrote themselves hundreds of times, dependency downloads and the runner's
own bookkeeping. The line that explains the failure is usually somewhere in
the middle, and several error-shaped lines around it are fallout from it, not
its cause. Reading a log is a skill, and it is slow even for people who have
it.

**Why GitLab first.** Tools in this space overwhelmingly target GitHub
Actions. GitLab CI is widely used in companies that self-host, and its logs
have structure worth exploiting: `section_start`/`section_end` markers name
each phase of the job, `ERROR: Job failed` is a machine-readable verdict, and
the API records every retry of a job against the same commit. That retry
history is what makes "flaky" a fact rather than a guess. A tool that knows
one CI system well is more useful than one that knows several shallowly, and
the distiller's generic parts (terminal replay, scoring, windowing) carry over
to other systems later ([#50](https://github.com/rbalukja15/pipelinemd/issues/50)).

**The obvious design, and why it is not enough.** Paste the log into a large
language model and ask what went wrong. It works often enough to be
tempting, and it fails in ways that matter here:

- **Size.** A 40,000-line log does not fit a prompt cheaply, and much of it
  does not fit at all. Truncating it blindly drops the middle, which is where
  the cause usually is.
- **Reproducibility.** The same log can get two different answers. A tool
  whose output decides whether an automated fix is proposed
  ([#24](https://github.com/rbalukja15/pipelinemd/issues/24)) needs the
  decision to come out the same twice.
- **Checkability.** A model will cite "line 41203" confidently whether or not
  line 41203 says anything of the sort. Prose alone gives the reader no way to
  tell.
- **Leakage.** Build tools print credentials. Sending raw logs to a third
  party sends those too.
- **Cost and availability.** A diagnosis that needs an API key and a network
  call is unavailable to anyone who has neither, and costs money on every
  failure.

The opposite extreme, rules alone, is cheap, reproducible and offline, but
rules cannot judge which of two genuine errors is upstream of the other.

**How it ships.** The original plan was a hosted web application: an ingest
endpoint, a webhook receiver, a record of every analysis, and a dashboard.
That means a server to run, credentials for other people's GitLab instances
to hold, and a deployment to keep alive, all before the first log is read.

## Decision

**pipelinemd diagnoses failed GitLab CI jobs, and the work is split between a
deterministic core and an optional model at the edge.**

1. **A pure distiller** reduces a trace to the lines that explain it: replays
   ANSI escapes and `\r`/`\b` overwrites, parses sections, redacts credential
   shapes, scores every line, and spends a fixed line budget on the best
   windows. It reads no clock, opens no socket and uses no randomness, so the
   same trace always gives the same evidence.
2. **A rule catalog** matches that evidence against known failure signatures,
   each with an explanation and concrete fixes. Rules alone place every
   report in a v1 failure class and pick its fix type, together with GitLab's
   retry history. The model never decides the class.
3. **An optional Claude diagnosis** reads only the distilled, redacted
   evidence and what the rules concluded, and is asked for the one judgement
   rules cannot make: which signal is the cause and which is fallout. It must
   cite evidence lines, and a diagnosis citing nothing it was shown is
   rejected. A failed call is never fatal; the deterministic report stands
   without it.

**The stack** is Python 3.11 or newer, with:

- **No third-party dependencies in the core.** Fetching uses `urllib`,
  rendering is hand-written, and the data model is plain dataclasses. The
  `anthropic` SDK is the one optional extra (`pipelinemd[llm]`), imported
  lazily so `import pipelinemd` never needs it.
- **Quality bars enforced in CI:** `mypy --strict`, `ruff`, `pytest` with
  coverage, and an accuracy gate that scores the distiller and rules against a
  labelled corpus of failure traces on every push.

**It ships as a CLI, not a service.** `pipelinemd diagnose <url>` runs on a
laptop, and the same command with no arguments runs as an `on_failure` job
inside the pipeline that failed, using the job's own `CI_JOB_TOKEN`. It is
published to PyPI and as a container image, so a GitLab job can name it in
`image:` without installing anything at the moment something is already
broken.

## Consequences

**What this makes easy.**

- The deterministic half can be tested on exact output and scored against a
  corpus, so a catalog regression fails the build instead of being noticed by
  a user ([evaluation](../evaluation.md)).
- A report costs nothing without a key, and the rules-only path works fully
  offline (`pipelinemd distill`).
- Prompts stay small, so model calls are cheap, and two runs on the same
  trace send byte-identical requests, which makes prompt regressions
  attributable.
- Every model claim points at a line a reader can check.
- It installs into any runner image with Python, without a dependency tree to
  conflict with the project's own pins.
- There is no server to host or secure. pipelinemd holds no one's
  credentials; it borrows the job's token for as long as the job runs.

**What it costs.**

- Rules have to be written and maintained by hand, and a failure no rule
  covers gets an unclassified report. The corpus names those gaps rather than
  hiding them.
- The distiller's line budget can, in principle, drop the line that matters.
  The evidence hit rate in `make eval` exists to catch that.
- No dependencies means no HTTP library, no YAML parser and no templating
  engine. Anything that would want one, such as the HTML report
  ([#19](https://github.com/rbalukja15/pipelinemd/issues/19)), is written
  against the standard library or justified as an extra.
- With no service there is no history across runs, no dashboard and no
  webhook. Each diagnosis is a file the job keeps as an artifact. The issues
  that assumed a service (#8, #9, #10) were closed as not planned.
- Only GitLab is diagnosed end to end. Other CI systems' logs can be
  distilled from a file, but not fetched.

**Worth revisiting if** the catalog stops scaling (rules colliding faster than
they can be told apart), if users need cross-run history badly enough to run
something persistent, or if a second CI system's structure turns out too
different for the generic distiller.

# Architecture

pipelinemd is a pipeline of pure stages with one impure edge at each end:
fetching a trace at the front, optionally asking a model at the back.
Everything between is deterministic and independently testable.

```
GitLab API ─┐
            ├─► raw trace ─► clean ─► score ─► window ─► evidence ─┬─► rules ──┬─► render
local file ─┘                                                     │           │
                                                                  └─► Claude ─┘
                                                          (optional, sees only evidence)
```

## Module map

| Module | Responsibility |
| --- | --- |
| `models.py` | The vocabulary every layer speaks. Plain dataclasses, no dependencies. |
| `gitlab/url.py` | Parse pasted pipeline/job URLs. Everything after `/-/` is the route; everything before it is the project path. |
| `gitlab/http.py` | urllib + auth headers + bounded retries + page following. |
| `gitlab/client.py` | The five REST calls we need, and the mapping to `JobRef`. |
| `distill/ansi.py` | Replay the terminal: escapes, `\r`, `\b`. |
| `distill/redact.py` | Mask credential shapes. Line-count preserving. |
| `distill/trace.py` | Sections, timestamps, metadata, per-line attribution. |
| `distill/extract.py` | Score lines, grow windows, spend the budget, collapse repeats. |
| `rules/catalog.py` | 58 failure signatures with fixes. Data, not code. |
| `rules/engine.py` | Apply the catalog, rank the hits. |
| `diagnose/prompt.py` | Build the request. Owns the JSON schema. |
| `diagnose/claude.py` | Make the call. Never fatal. |
| `render/*` | Terminal, markdown, JSON. |
| `cli.py` | Argument parsing, orchestration, exit codes. |

## Why the distiller is pure

`distill(raw)` reads no clock, opens no socket, and calls no random source.
Consequences worth having:

- **Tests can assert on exact output.** A fixture trace either produces the
  evidence we expect or the change that broke it is visible in the diff.
- **Results are cacheable.** Same trace, same evidence, so a repeated
  diagnosis need not re-derive anything.
- **The LLM call is reproducible up to the model.** Two runs send byte-identical
  prompts, which makes prompt regressions attributable.

## The two ranking decisions

There are two places pipelinemd decides what matters, and they are different
problems solved separately.

**1. Which lines are evidence** (`distill/extract.py`). Weighted signals score
each line; strong ones grow a window; windows merge; a line budget is spent
best-first. The tail is pinned because gitlab-runner writes its verdict there.
A window larger than the whole budget is split into head and end rather than
being allowed to swallow it.

**2. Which evidence lines to show** (`render/evidence.py`). A 200-line excerpt
still does not fit a 40-line terminal. Tiers decide: the runner's closing
verdict always shows, then anchors, then their neighbours. Within a tier the
*earliest* line wins, because the top of an error block names the fault and the
bottom repeats it.

## Cause versus fallout

The hardest part of reading a CI log is that a single fault produces many
error-shaped lines. Three mechanisms address it:

- **Dampeners.** `0 failed, 512 passed` uses failure vocabulary to report
  success. Five dampener patterns subtract from such lines so they do not
  anchor.
- **The cleanup penalty.** gitlab-runner names its own phases. A rule firing in
  `upload_artifacts_on_failure` or `after_script` is describing something that
  happened *because* the job already failed, so it is scored down 45 points and
  cannot outrank a hit in `step_script`. Without this, "artifact upload found no
  matching files" outranks the npm error that caused it.
- **The model.** Rules cannot tell which of two genuine errors is upstream of
  the other. That judgement is the one thing the LLM layer is asked for, and the
  prompt says so explicitly.

## Why the core has no dependencies

This tool runs inside other people's broken builds. A dependency tree is a
liability there: it is more to install on a runner that may already be out of
disk, more to conflict with the project's own pins, and more surface between
the user and an answer. urllib is enough for five GET requests, and the ANSI
renderer is forty lines.

`anthropic` is the one optional extra, imported lazily so that
`import pipelinemd` never requires it.

## Trust boundaries

Job traces are untrusted input. They can be enormous, contain invalid UTF-8,
carry credentials the project's own tooling printed, and include text designed
to look like structure.

- Decoding uses `errors="replace"`; no trace can raise on decode.
- Oversized traces are clipped head-and-tail before processing.
- Individual lines are truncated at 1,000 characters.
- Redaction runs before evidence selection, so a secret cannot reach the
  prompt, the terminal, or the JSON output.

Redaction is mitigation, not a guarantee. A secret in a shape no pattern
matches will pass through.

## Extending it

**A new rule** is a `Rule` in `rules/catalog.py` plus a fixture. Keep patterns
anchored to text a tool actually prints, not paraphrases of it.

**A new output format** is a function taking `Report` and returning `str`, plus
a `--format` choice. `render/json_out.py` is the smallest example.

**A new source** (GitHub Actions, Jenkins) needs a client producing a raw trace
and a `JobRef`. The distiller's GitLab-specific parts are the `section_start`
markers and the `ERROR: Job failed` verdict; everything else is generic
terminal handling that applies to any CI log.

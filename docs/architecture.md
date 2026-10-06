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
| `rules/catalog.py` | 59 failure signatures with fixes. Data, not code. |
| `rules/engine.py` | Apply the catalog, rank the hits. |
| `taxonomy.py` | Place a failure in the v1 taxonomy, and pick its fix type. The rule-to-class table lives here. |
| `assessment.py` | How far to trust an analysis: one confidence from every signal behind it, and when to send it to human review. |
| `cost.py` | List prices, and what an analysis's model calls cost. |
| `diagnose/prompt.py` | Build the request. Owns the JSON schema. |
| `diagnose/citations.py` | Resolve the line numbers a diagnosis cited back to real evidence lines. |
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
error-shaped lines — and that some of them describe the runner rather than the
job. Four mechanisms address it:

- **Dampeners.** `0 failed, 512 passed` uses failure vocabulary to report
  success. Five dampener patterns subtract from such lines so they do not
  anchor.
- **The cleanup penalty.** gitlab-runner names its own phases. A rule firing in
  `upload_artifacts_on_failure` or `after_script` is describing something that
  happened *because* the job already failed, so it is scored down 45 points and
  cannot outrank a hit in `step_script`. Without this, "artifact upload found no
  matching files" outranks the npm error that caused it.
- **The verdict.** `ERROR: Job failed (system failure): …` is the runner
  concluding, then restating the cause. `runner.system-failure` matches the
  conclusion and is a container — its own first fix reads "read the line right
  before this one". A rule matching the restated cause is usually the better
  answer, so it is **scored at the verdict line**, not at its own first match.
  That matters because real runner output prints the cause several times first
  — a `WARNING:` per pull attempt, an `ERROR: Preparation failed:` per retry —
  and the container, always on the last line, otherwise wins on recency alone.

  Whether a restated cause is promoted (+10) or demoted (−25) depends on whose
  machine its advice targets, and that is decided **per rule**:

  | verdict restates | rule | advice | outcome |
  | --- | --- | --- | --- |
  | `no space left on device` | `runner.no-space` | `df -h`, `docker system prune` — the runner host | promoted, outranks the container |
  | `manifest unknown` | `docker.manifest-unknown` | fix the tag in `image:` — the job | promoted, outranks the container |
  | `Cannot connect to the Docker daemon` | `docker.daemon-unreachable` | add `services: [docker:dind]` — a job's dind, but this daemon is the runner's | demoted to rank 2 |

  Two proxies were tried and rejected. **Category** puts both docker rules in
  `docker`, yet one's advice is right and the other's is the wrong machine.
  **Section** puts both in `prepare_executor`. So the demoted rules are named
  one by one in `WRONG_MACHINE_WHEN_RESTATED`, and each needs a fix that is
  right in a job and wrong on the runner.

  The bonus settles a contest between **equally confident** rules. A MEDIUM
  rule restated inside the HIGH container — a connection timeout, say — still
  ranks second. Whether it should is open; no corpus case covers it yet.
  Demoted or not, the losing rule stays in the ranking: it is the useful
  detail, just not the answer.
- **The model.** Rules cannot tell which of two genuine errors is upstream of
  the other. That judgement is the one thing the LLM layer is asked for, and the
  prompt says so explicitly.

## The v1 taxonomy: which layer decides what

Every report carries a **class** from the v1 taxonomy (#17) and a **fix type**
(#15): `yaml_patch`, `code_patch`, `infra` or `flaky_retry`. The class is
coarser than a rule's category, on purpose — `dependency` and `lint` are
useful categories, but for v1 both mean "the project's own code failed", so
both are in `test`.

Two signals decide the class, and both are reproducible:

- **The top rule's base class**, from one table in `taxonomy.py` covering all
  59 rules, so the decisions can be reviewed side by side. A test fails if a
  rule is added without an entry. Only the *top* rule counts: if it falls
  outside v1 the failure is unclassified, rather than falling through to a
  lower hit — which the engine has already ranked as a weaker explanation, and
  which is often the top hit's own fallout.
- **Retry history.** Every attempt of a job in one pipeline ran against the
  same commit, so if another attempt passed, the job is flaky. That is
  GitLab's own record, fetched once per pipeline with `include_retried=true`,
  and it overrides the rule. A rule that is transient by nature (a timeout, a
  DNS failure) but failed identically on every attempt keeps its class and
  drops to low confidence.

**The model does not decide the class**, even where the rules are silent. Its
reading is recorded on the diagnosis — restricted by the schema to the v1
classes plus `unclassified`, with `flaky` excluded because a single log
cannot show a job would have passed on retry — and it is shown when it
disagrees. It is never applied, because of what the class controls:

```
class → fix type → yaml_patch → #24 opens an MR
```

`yaml_patch` is the one value that can lead to an automated write, so it has
to come from a signal that gives the same answer twice. For the same reason it
is reserved for the `yaml` class alone. Every class whose fixes are mixed gets
the conservative type: a false `yaml_patch` invites an automated MR, while a
false `infra` just means a person does the work.

The classification is derived from the report on demand rather than stored on
it. A report is rebuilt with `dataclasses.replace` when the diagnosis arrives,
and a stored classification would have to be recomputed every time — silently
wrong the one time someone forgets.

## Grounding: a diagnosis must point at something

A model asked to explain a failure will produce a confident paragraph citing
line 41203 whether or not line 41203 says anything of the sort. Prose alone
gives a reader no way to tell the difference.

So the schema requires `evidence_lines`, and every number in it is resolved
against the evidence the model was actually shown — not the whole cleaned
trace, because the excerpt is all it saw, so anything outside it could not have
been read, only guessed.

Citable means *printed*, and nothing more. A collapsed run shown as
`100 [x10]` contributes the number 100 alone; 101-109 were never put in front
of the model, so citing one is a guess. The strict rule is also the safe one: a
fuzzy collapse folds lines that merely look alike — `Downloading package-0`
through `package-9` share one entry — so mapping an offset onto the run's text
would report *line 104 said `package-0`* when line 104 said `package-4`. A
manufactured quote wearing a grounding badge is the one outcome this check
exists to prevent, so the resolver refuses to do range arithmetic at all. A
citation carries the run's repeat count instead, and every renderer prints it,
so a reader can see that one quote stands for ten lines.

What happens next depends on how much survives:

- **Nothing resolves** → the diagnosis is rejected. It is describing a log it
  did not read, and reporting it would be worse than reporting nothing.
- **Some resolve** → the diagnosis stands, the invented numbers are kept in
  `unresolved_citations`, and every renderer says so on the face of the report.
  Half-grounded is usable; silently half-grounded is not.

This is why line numbers are preserved through every distillation stage. They
are not a display convenience — they are the mechanism that makes a diagnosis
checkable.

The diagnosis's own confidence is left as the model gave it when citations
fail to resolve. The failure lowers the confidence of the *analysis* instead
(below), alongside the warning rather than in place of it, so a grounding
failure is never hidden behind a number.

## Confidence and cost: what an analysis is worth, and what it cost

Every report shows an analysis-level confidence and an estimated cost (#18).
Both are derived from the report on demand, like the classification, so
neither can go stale when the diagnosis arrives.

**Confidence is never more than the weakest signal behind it.** It starts
from the classification's — the top rule's, or retry history's — and when
there is a diagnosis it is capped at the model's own rating. Two signs of
trouble each cost a level: citations that do not resolve, and a model that
places the failure in a different class from the rules. A model that declines
to classify is not a sign of trouble, and neither is one that names a class
where the rules named none — there it is contradicting nothing. With nothing
classified the analysis is low: a diagnosis no rule corroborates is the
model's word alone, and no fix type follows from it.

At low the report says **needs human review**, with every reason the
confidence fell, printed above the diagnosis so it is read first. The bar is
`REVIEW_BELOW = medium`. For the classification signal it is chosen from the
eval: low is the one level that is wrong more often than right. The eval
scores no diagnosis, so for an analysis the model's side demoted to low, the
bar is a judgement rather than a measurement (see
[evaluation](evaluation.md#what-the-table-cannot-see)).
JSON carries the level, the flag, the bar and the reasons, so a consumer that
wants a stricter bar — #24 might open MRs only at high — applies its own.

It stays an ordinal, like every confidence in this tool. A score of 0.83
would claim a calibration nothing here has; the eval reports how often each
level is right instead, which is a claim that can be checked.

**Cost is estimated from what the API says it billed.** All four counts are
captured — input, output, cache writes, cache reads — and priced at the list
rates in `cost.py`, which records the date they were copied. Three rules keep
the estimate honest:

- **A refused answer still cost money.** A refusal, a reply that is not JSON,
  or a diagnosis rejected for citing nothing real were all billed, so the error
  carries the call's usage and the report counts it as a discarded call.
- **No model call, no cost.** A rules-only analysis shows `$0` and says why.
- **No price, no number.** A model missing from the table gets `unknown`, not
  a neighbour's price, and one unpriced call makes the total unknown rather
  than a partial sum that looks whole.

Every cost line ends with the date its prices were copied, because the
estimate goes stale with them and a pasted report carries no other clue.

With `--all-jobs`, the reports are followed by a **run total** that pools
every call, so one unpriced call makes the run's total unknown — the same rule
as within a report. In JSON several reports arrive as `{"schema_version",
"reports", "cost"}` rather than a bare array, so the total has somewhere to
sit; without it, the natural consumer adds up the per-report figures it can
read and gets a partial sum that looks whole. One report is unchanged.

What the estimate cannot see is anything a response does not report: Bedrock
and Google Cloud pricing, US-only inference, negotiated rates, and a call
billed and then lost in transport, which never returns its usage. It is
labelled an estimate everywhere it appears for that reason.

## Why the core has no dependencies

This tool runs inside other people's broken builds. A dependency tree is a
liability there: it is more to install on a runner that may already be out of
disk, more to conflict with the project's own pins, and more surface between
the user and an answer. urllib is enough for five GET requests, and the ANSI
renderer is forty lines.

`anthropic` is the one optional extra, imported lazily so that
`import pipelinemd` never requires it.

## One version, two ways to install it

The same reasoning is why there is an image. A diagnose job that runs
`pip install` on every failure depends on PyPI at exactly the moment something
is already broken, so `ghcr.io/rbalukja15/pipelinemd` ships the CLI with the
`[llm]` extra already installed, and a GitLab job names it in `image:`.

The image is built from the merged source rather than installed from PyPI, so
CI can build and smoke-test it on every push, long before the version exists
anywhere. Both are published by one workflow from one commit, and the version
is written in one place, `__version__`, which hatch reads into the package
metadata and the release workflow makes the tag from. How a release runs,
and why in that order: [releasing](releasing.md).

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

# Evaluation

`make eval` scores the deterministic half of pipelinemd against the labelled
corpus in [`corpus/`](../corpus/README.md). It runs offline, takes a couple of
seconds, and gives the same answer every time.

## What is measured

| Metric | Question |
| --- | --- |
| **rule@1** | Did the rule the corpus expects rank *first*? Scored only over labelled cases. |
| **evidence** | Did the line a human would point at survive distillation into the excerpt? |
| **exit code** | Did the distiller read the runner's verdict correctly? |

**The LLM diagnosis is deliberately not scored.** It needs an API key, costs
money per run, and is not reproducible — folding it in would turn `make eval`
from a regression gate into a bill, and would make the number depend on which
model answered that day. Everything measured here is deterministic.

`evidence` is the metric worth watching. It is a genuine measurement even on
authored traces: the distiller's windowing and line budget are indifferent to
how the failure was written, so it really can drop the line that matters.

## Results

```
pipelinemd eval — 60 cases (60 authored)

class            cases   rule@1  evidence  exit code
---------------- ----- -------- --------- ----------
cache_artifact       8      7/7       8/8        8/8
ci_vars              8      8/8       8/8        8/8
flaky                9      8/8       9/9        9/9
image_pull           9      9/9       9/9        9/9
runner               9      9/9       9/9        9/9
test                 9      6/6       9/9        9/9
yaml                 8      6/6       8/8        8/8
---------------- ----- -------- --------- ----------
overall             60    53/53     60/60      60/60

rule@1 100.0%  ·  evidence 100.0%  ·  exit code 100.0%

Known gaps — no rule covers these (7)
  yaml-yamllint-indentation     correctly silent
  yaml-empty-variable-expansion correctly silent
  artifact-too-large            correctly silent
  test-rspec-failure            correctly silent
  test-vitest-failure           correctly silent
  test-phpunit-failure          correctly silent
  flaky-gitlab-502              correctly silent

Every case is authored, not observed. These numbers are a regression
signal, not a measurement of real-world accuracy — see corpus/README.md.
```

## What the first run found, and what fixing it did

The harness was merged without tuning the catalog, on purpose: tuning rules
until the number improves, in the same change that introduces the measurement,
would have made the first figure meaningless. The four findings were written
down instead, and fixed separately. This is that separate fix, and the eval is
what says it worked:

| | rule@1 |
| --- | --- |
| First run (#22) | 47/53 — 88.7% |
| Three findings fixed, no labels touched | 51/53 — 96.2% |
| Fourth finding, which needed a label correction | 53/53 — 100.0% |

Each fix is a regression test in `tests/test_engine.py` that fails against the
previous catalog.

### Fixed: the pytest rule claimed Jest's output

`test.pytest-failed` matched `\b\d+ failed(?:,| \b)` — generic enough for
pytest's summary bar, and also a match for Jest's `Tests:  1 failed, 511
passed`. A rule named for one framework outranked the rule named for the other
on that framework's own output. Two of the six misses, one cause, and a real
false positive users would have hit.

Fixed by excluding Jest's own summary labels from that pattern rather than by
narrowing it: pytest's bar genuinely is that generic, and the thing that made
it wrong was the label in front of it.

### Fixed: nothing fired on a connection that timed out

`net.connection-refused` covered refusals and resets but not a connection that
simply never completed — the more common transient failure of the two. Both
timeout cases fired **nothing at all**.

There is now a `net.connection-timeout` rule. Refusals and timeouts are kept
apart deliberately, including an exclusion on the refusal rule, because curl
reports both through `Failed to connect to <host> port` and the advice differs:
a refusal means something is closed, a timeout means the packets went nowhere.

**This finding needed a label correction, and that is worth stating plainly.**
Both cases were labelled `net.connection-refused`, which was wrong on the facts
— neither trace contains a refusal. One case's note already said so at
authoring time: *"net.connection-refused is the closest rule but imprecise."*
Correcting it to the new rule moves rule@1 from 96.2% to 100%, so **two of the
six original misses were resolved by fixing the corpus rather than the
catalog**. The other three were fixed on the merits with no label touched.

The line held here: a label may be corrected when it was wrong about the
failure, never when it is merely inconvenient. `cache-s3-credentials` below is
the case that tested it.

### Fixed: a system failure that read as a docker failure

```
ERROR: Job failed (system failure): Cannot connect to the Docker daemon
^--- runner.system-failure              ^--- docker.daemon-unreachable
```

Both rules match that line, both are HIGH confidence, and the tie broke
alphabetically — so `docker.daemon-unreachable` won.

The engine now applies a `QUOTED_VERDICT_PENALTY` to a rule matching *inside*
the runner's own `ERROR: Job failed` verdict rather than at the start of it.
The runner's conclusion is about the whole job; what follows is the runner
quoting something. Here the docker daemon is the *runner's*, and the job never
started, so "fix your docker setup" is advice about the wrong machine.

`docker.daemon-unreachable` is demoted, not suppressed — it stays at rank 2,
because it is the useful detail. The same message inside the job's own script
is untouched and still ranks first.

### Fixed: a cache credential that read as the job's AWS credential

`cache-s3-credentials` resolved to `aws.no-credentials`, because
`WARNING: Retrieving cache from S3 failed: AccessDenied` contains
`AccessDenied`. Defensible, and still wrong: that credential belongs to the
runner's distributed cache, configured in `[runners.cache]`, not to anything
the job sets — so the rule's advice ("check `AWS_ACCESS_KEY_ID`, check the IAM
policy") pointed at the wrong machine, exactly as above.

This is the case where relabelling would have been easy and wrong. The label
`ci.cache-failed` was *correct* — the cache did fail — so the fix belonged in
the catalog: `aws.no-credentials` now excludes the runner's cache-transfer
lines, `ci.cache-failed` matches the line that names the backend and the
reason, and its advice now says where to actually look. A genuine job-side
`AccessDenied` still fires `aws.no-credentials`.

A dedicated `ci.cache-auth-failed` rule would be better still, but adding it
would require relabelling this case in the same change, which is the thing this
document exists to avoid. Left as a follow-up.

### Declared gaps: unchanged

Seven cases carry `expected_rule: null`, and all seven are still **correctly
silent**. Nothing added here misfires on them — including the new timeout rule,
which does not claim `flaky-gitlab-502`. `make gate` enforces that at zero.

### What 100% does not mean

It means the catalog answers every labelled case in an authored corpus
correctly. It does not mean the tool is right in the wild, and the number will
drop the first time a real trace lands — which is the intended direction of
travel, not a regression. See below.

## Reading the number honestly

Every case is currently `provenance: authored`. The failures and the labels
were written by the same hand that wrote the rules they are matched against, so
**rule@1 is a regression signal, not a measurement of real-world accuracy**. It
answers "did a change break something that worked". It does not answer "how
often is this right in the wild", and it should not be quoted as if it did.

The harness reports the authored/observed split on every run, and drops the
caveat automatically once observed cases are present. Until then the figure
worth quoting publicly is none of them — see [#23](https://github.com/rbalukja15/pipelinemd/issues/23),
which wants an accuracy table in the README, and should wait for observed data.

## Running it

```bash
make eval                                   # the table above
make gate                                   # the same run, with CI's thresholds
pipelinemd eval --format json               # same numbers, machine-readable
pipelinemd eval --min-rule-accuracy 0.85    # exit 5 if rule@1 regresses
pipelinemd eval --max-gap-false-positives 0 # exit 5 if a gap starts firing
pipelinemd eval --corpus path/to/other      # score a different corpus
```

## The gate

`make gate` runs on every push, as the last step of CI. Without it
`--min-rule-accuracy` would be a flag nobody runs, and everything above would
be a report rather than a guard.

Two thresholds, set in the [Makefile](../Makefile):

**`MIN_RULE_ACCURACY = 0.92`.** Raised from 0.85 now that rule@1 is 53/53: 0.92
is 49/53, so the build fails on the fifth regression. The headroom is still
deliberate, for the same reason as before — the corpus is meant to grow with
`observed` traces, which will be harder than the authored ones, and a gate that
goes red the moment someone commits a real failing log discourages the
contribution this project most needs.

The cost is real and worth stating: four broken cases will not fail the build.
They stay visible — `make eval` names every miss — they just do not stop a
merge on their own, and the per-case regression tests in
`tests/test_engine.py` catch the specific behaviours that matter. Adding hard
traces will eventually push the rate under this floor; the right response is to
lower it in a commit that says why, so the number moves in review rather than
silently.

**`MAX_GAP_FALSE_POSITIVES = 0`.** Known gaps are excluded from every rate, so
a rule that starts firing on one moves *no number at all* — `--min-rule-accuracy`
cannot see it. It is also the worst thing the catalog can do: producing a
confident wrong answer where it previously had the good sense to stay silent.
All seven gaps are correctly silent today, and that is gated exactly.

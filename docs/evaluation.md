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
cache_artifact       8      6/7       8/8        8/8
ci_vars              8      8/8       8/8        8/8
flaky                9      4/8       9/9        9/9
image_pull           9      9/9       9/9        9/9
runner               9      9/9       9/9        9/9
test                 9      5/6       9/9        9/9
yaml                 8      6/6       8/8        8/8
---------------- ----- -------- --------- ----------
overall             60    47/53     60/60      60/60

rule@1 88.7%  ·  evidence 100.0%  ·  exit code 100.0%

Misses (6)
  cache-s3-credentials          expected ci.cache-failed — got aws.no-credentials (rank 2)
  test-jest-snapshot            expected test.jest-failed — got test.pytest-failed (rank 2)
  flaky-external-api-timeout    expected net.connection-refused — got nothing fired (never fired)
  flaky-runner-lost             expected runner.system-failure — got docker.daemon-unreachable (rank 2)
  flaky-test-passes-on-retry    expected test.jest-failed — got test.pytest-failed (rank 2)
  flaky-apt-mirror-unreachable  expected net.connection-refused — got nothing fired (never fired)

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

## What the first run found

The catalog was **not** tuned in response to any of this. Tuning rules until the
number improves, in the same change that introduces the measurement, would make
the first figure meaningless. These are recorded as findings to fix
deliberately and separately, so the eval can show the improvement.

### False positive: the pytest rule fires on Jest output

`test-jest-snapshot` and `flaky-test-passes-on-retry` both resolve to
`test.pytest-failed` rather than `test.jest-failed`. The pytest rule matches
`\b\d+ failed(?:,| \b)`, and Jest prints `Tests:       1 failed, 511 passed`
— so a rule named for one framework outranks the rule named for the other on
that framework's own output. Two misses, one cause.

### Coverage gaps: timeouts phrased as timeouts

`flaky-external-api-timeout` (curl: `Operation timed out after 30001
milliseconds`) and `flaky-apt-mirror-unreachable` (apt: `Could not connect to
deb.debian.org:80 … connection timed out`) fire **nothing at all**.
`net.connection-refused` covers refusals and resets but not a connection that
simply never completed, which is the more common transient failure of the two.

### Ranking: a system failure that reads as a docker failure

`flaky-runner-lost` resolves to `docker.daemon-unreachable` even though the
trace opens with `ERROR: Job failed (system failure)`. Both rules genuinely
match; the more specific one loses. The runner's own verdict should outweigh a
message quoted inside it.

### Arguably my label, not the rule

`cache-s3-credentials` expects `ci.cache-failed` and gets `aws.no-credentials`,
because the trace contains `AccessDenied`. It is a defensible answer — but the
failing credential belongs to the *runner's cache backend*, not the job's AWS
client, so `aws.no-credentials`' advice would send someone to the wrong place.
Left as a miss rather than relabelled.

### Declared gaps

Seven cases carry `expected_rule: null` because no rule covers them — RSpec,
Vitest and PHPUnit output, artifact size limits, a git `502`, a yamllint error,
an empty variable expansion. All seven are **correctly silent**: no rule
misfires on them. They are in the corpus so this section has something to say.

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

**`MIN_RULE_ACCURACY = 0.85`.** rule@1 is 47/53 = 88.7% today, so 0.85 is 45/53
— the build fails on the third regression, not the first. The slack is
deliberate. The corpus is meant to grow with `observed` traces, which will be
harder than the authored ones, and a gate that goes red when someone commits a
real failing log discourages exactly the contribution this project most needs.
The cost is real and worth stating: a single rule breaking one case will not
fail the build. It is still visible — `make eval` names every miss, and this
document lists them — it just does not stop a merge on its own. Tighten the
floor once the corpus stops moving.

**`MAX_GAP_FALSE_POSITIVES = 0`.** Known gaps are excluded from every rate, so
a rule that starts firing on one moves *no number at all* — `--min-rule-accuracy`
cannot see it. It is also the worst thing the catalog can do: producing a
confident wrong answer where it previously had the good sense to stay silent.
All seven gaps are correctly silent today, and that is gated exactly.

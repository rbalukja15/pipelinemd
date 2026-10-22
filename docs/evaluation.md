# Evaluation

`make eval` scores the deterministic half of pipelinemd against the labelled
corpus in [`corpus/`](../corpus/README.md). It runs offline, takes a couple of
seconds, and gives the same answer every time.

## What is measured

| Metric | Question |
| --- | --- |
| **rule@1** | Did the rule the corpus expects rank *first*? Scored only over labelled cases. |
| **class** | Did the classifier put the case in the v1 class the corpus labels? Scored over *every* case: a gap has no expected rule, but it does have a class. |
| **evidence** | Did the line a human would point at survive distillation into the excerpt? |
| **exit code** | Did the distiller read the runner's verdict correctly? |
| **class by confidence** | At each confidence level a report can show, how often is the class right? Reported per level, not as one rate. |

**The LLM diagnosis is deliberately not scored here.** It needs an API key,
costs money per run, and is not reproducible — folding it in would turn
`make eval` from a regression gate into a bill, and would make the number
depend on which model answered that day. Everything `make eval` measures is
deterministic. The diagnosis has its own opt-in run,
[`make eval-llm`](#the-diagnosis-layer-46), which never gates anything.

`evidence` is the metric worth watching. It is a genuine measurement even on
authored traces: the distiller's windowing and line budget are indifferent to
how the failure was written, so it really can drop the line that matters.

## Results

```
pipelinemd eval — 62 cases (62 authored)

class            cases   rule@1   class  evidence  exit code
---------------- ----- -------- ------- --------- ----------
cache_artifact       8      7/7     7/8       8/8        8/8
ci_vars              8      8/8     8/8       8/8        8/8
flaky                9      8/8     5/9       9/9        9/9
image_pull          10    10/10    9/10     10/10      10/10
runner              10    10/10   10/10     10/10      10/10
test                 9      6/6     6/9       9/9        9/9
yaml                 8      6/6     6/8       8/8        8/8
---------------- ----- -------- ------- --------- ----------
overall             62    55/55   51/62     62/62      62/62

rule@1 100.0%  ·  class 82.3%  ·  evidence 100.0%  ·  exit code 100.0%

Class accuracy by confidence
  high       39/43   91%
  medium       9/9  100%
  low         3/10   30%  needs human review

Known gaps — no rule covers these (7)
  yaml-yamllint-indentation       correctly silent
  yaml-empty-variable-expansion   correctly silent
  artifact-too-large              correctly silent
  test-rspec-failure              correctly silent
  test-vitest-failure             correctly silent
  test-phpunit-failure            correctly silent
  flaky-gitlab-502                correctly silent

Class misses (11)
  yaml-yamllint-indentation       labelled yaml — got unclassified (no rule fired)
  yaml-empty-variable-expansion   labelled yaml — got unclassified (no rule fired)
  imagepull-registry-unauthorized labelled image_pull — got ci_vars (docker.push-denied maps to ci_vars)
  artifact-too-large              labelled cache_artifact — got unclassified (no rule fired)
  test-rspec-failure              labelled test — got unclassified (no rule fired)
  test-vitest-failure             labelled test — got unclassified (no rule fired)
  test-phpunit-failure            labelled test — got unclassified (no rule fired)
  flaky-tls-unknown-authority     labelled flaky — got runner (net.tls is not transient by nature, so only retry history could make it flaky, and the corpus carries none)
  flaky-runner-lost               labelled flaky — got runner (runner.system-failure is not transient by nature, so only retry history could make it flaky, and the corpus carries none)
  flaky-test-passes-on-retry      labelled flaky — got test (test.jest-failed is not transient by nature, so only retry history could make it flaky, and the corpus carries none)
  flaky-gitlab-502                labelled flaky — got unclassified (no rule fired)

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
| Two cases added on review, to pin the verdict ranking | 55/55 — 100.0% |

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

The engine now adjusts a rule matching *inside* the runner's own
`ERROR: Job failed` verdict rather than at the start of it. Here the docker
daemon is the *runner's*, the job never started, and
`docker.daemon-unreachable`'s advice is about the job's CI config — the wrong
machine — so it is demoted 25 points.

**Review caught that offset alone is the wrong test**, and the first cut of
this got it wrong in the opposite direction:

```
ERROR: Job failed (system failure): no space left on device
```

`runner.no-space` also matches inside the quote, but its advice (`df -h`,
`docker system prune`) is aimed squarely at the runner host — the right machine
— while `runner.system-failure`'s own first fix reads *"read the line right
before this one"*. Demoting it would have been the same confident wrong answer
in reverse. So the adjustment became signed, scoped by category: `runner` and
`resources` rules gained 10, everything else lost 25.

**A second review found category was the wrong proxy too**, and following it
up showed something bigger. `docker.manifest-unknown` is `docker`, like the
daemon rule, but restated in the same verdict its advice — fix the tag in
`image:` — is exactly right, and the category rule demoted it. And on the
trace shape real runners actually produce, the mechanism never engaged at all:

```
WARNING: Failed to pull image with policy "always": … manifest unknown
ERROR: Preparation failed: failed to pull image … manifest unknown
Will be retried in 3s ...
   … twice more …
ERROR: Job failed (system failure): failed to pull image … manifest unknown
```

The specific rule's *first* match is the `WARNING`, long before the verdict,
so no adjustment applied, and the container — always on the last line — won
on recency alone. It got the daemon case right the same way: by luck, with a
12-point margin.

Now a rule whose cause the runner restates in any verdict is scored *at* that
verdict, and which way it moves is decided per rule: promoted, unless it is
named in `WRONG_MACHINE_WHEN_RESTATED` — today only `docker.daemon-unreachable`,
whose `services: [docker:dind]` advice is right in a job and wrong on the
runner. Section was considered as the discriminator and rejected: both docker
cases fail in `prepare_executor`.

Two corpus cases now pin it, in the shape real runners print — retries, a
system-failure verdict, and **no exit code**, since no script ran:

| case | expects | before | after |
| --- | --- | --- | --- |
| `imagepull-prepare-tag-missing` | `docker.manifest-unknown` | rank 3 | rank 1 |
| `runner-docker-daemon-down` | `runner.system-failure` | rank 1, by recency | rank 1, by 25 points |

The second passes on both engines, so on its own it pins the outcome, not the
mechanism; the unit tests in `tests/test_engine.py` pin the margin. Making
room for them meant letting `exit_code` be an explicit `null` — the existing
system-failure cases end in an `ERROR: Job failed: exit code 1` that a real
runner does not print after a prepare failure, because the loader had nowhere
else to put a job with no exit code.

Either way the loser is demoted, not suppressed — it stays at rank 2, because
it is the useful detail. The same message inside the job's own script is
untouched and still ranks first.

**Recorded, not fixed:** `docker.pull-denied` matches
`ERROR: Preparation failed: failed to pull image` whatever the reason, so it
fires on a missing tag too. It now ranks third there rather than first, but it
is over-broad in the same way `docker.push-denied` is over-broad on pulls.

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

## The class metric (#17)

`class` arrived with the v1 taxonomy. Its first run was **49/60 — 81.7%**, and
per the rule this document follows, **nothing was tuned to move it**: no label
was changed and no mapping entry was adjusted after the number appeared. It is
now **51/62 — 82.3%**: the two cases added to pin the verdict ranking in #42
both land in their labelled class, and nothing else moved. Every miss is
explained below, and all four that are not coverage gaps are findings rather
than fixes.

### How the class is decided

From the log and from GitLab's records, never from the model:

1. **Retry history.** If another attempt of the same job, in the same
   pipeline, passed, the job is `flaky` — same commit, different outcome. This
   overrides the rule.
2. **The top rule's base class**, from one table in `src/pipelinemd/taxonomy.py`
   that lists all 59 rules. No rule is `flaky` unless it is transient by its
   nature; a timing-dependent test or a runner that fell over only becomes
   flaky through retry history.
3. **Otherwise `unclassified`**, always at low confidence.

The model's reading is recorded on its diagnosis and shown when it disagrees,
but it does not decide the class. The class sets the fix type, `yaml_patch` is
what #24's MR generator acts on, and anything that can lead to an automated
write has to come from a signal that gives the same answer twice.

**The mapping was written after seeing the corpus labels**, so it is fair to
ask whether it was fitted to them. The arithmetic answers it. Three rules carry
two different labels across corpus cases, so any static rule-to-class table
must miss one case from each pair: **the best a table fitted to these labels
could score is 52/62** (50/60 before #42's two cases). This one scores 51. The
one case it gives up beyond
that ceiling is `net.tls`, where it disagrees with the label on the facts —
see below. Where a rule's meaning is unambiguous the table agrees with the
labels; where it is not, it was chosen from the rule's meaning.

### The eleven misses

| | cases | why |
| --- | --- | --- |
| Coverage gaps | 7 | No rule fires, so the class is `unclassified`. The same seven cases as the rule@1 gaps; a new rule fixes both numbers at once. |
| Needs retry history | 2 | `flaky-runner-lost` and `flaky-test-passes-on-retry`. Their logs are an ordinary runner failure and an ordinary Jest failure; only a passing retry shows they are flaky, and the corpus is single traces. The second case's own note, written before this existed, says exactly that. |
| Catalog precision | 1 | `imagepull-registry-unauthorized` is a **pull**, but `docker.push-denied` wins it, because that rule also matches `unauthorized: authentication required` — which registries return for pulls too. The push rule is mapped to `ci_vars`, so the pull lands there. The same family of bug as pytest claiming Jest's output. |
| Label in question | 1 | `flaky-tls-unknown-authority` is labelled `flaky`, but its own note says the CA is missing from the image's trust store — which fails on every run. The mapping puts `net.tls` in `runner`, the environment. Retry history would show every attempt failing, confirming `runner` rather than overturning it. |

The last two are the ones to act on, separately:

- **`docker.push-denied` should stop matching pulls**, and the corpus case then
  expects `docker.pull-denied`. That is a rule@1 change as much as a class one.
- **`flaky-tls-unknown-authority` should probably be relabelled `runner`**, by
  the standard applied in #42: a label may be corrected when it was wrong about
  the failure. It is not corrected here, because this is the change that
  introduces the metric.

### What the eval cannot see

Retry history is the one signal the corpus cannot exercise — every case is a
single trace. It is covered by unit tests and by end-to-end CLI tests against
a stubbed GitLab, but the eval number says nothing about it. Adding
`retry_statuses` to corpus records would change that, but for authored cases
it would mean inventing the history, and it would flip two misses in the same
change that introduces the measurement. Both are reasons to wait for observed
traces.

## Confidence (#18)

Every report shows a confidence, and at low it says **needs human review**.
The table above is the evidence for that bar where the classification sets
the level. It scores the confidence a reader is shown when no diagnosis was
made — each case is assessed as a report, rules only — and, per the rule this
document follows, **nothing was re-rated after it appeared**.

| level | class right | what is there |
| --- | --- | --- |
| high | 39/43 | The four misses are the four non-gap class misses above. Three are not the rule's doing: two need retry history, and one is the TLS label in question. The fourth is: `docker.push-denied` matching a pull, the catalog precision bug already recorded. |
| medium | 9/9 | |
| low | 3/10 | Seven are the coverage gaps: nothing fired, so the analysis is the tool saying it does not know — and every one is sent to review. The three right ones are `ci.cache-failed`, the only low-rated rule that ranks first anywhere in the corpus. |

What it supports, and what it does not:

- **For the classification signal, the review bar is where the data puts
  it.** Low is the only level that is wrong more often than right, and the
  only one the bar catches. A test holds that premise — low below both other
  levels — so a corpus change that breaks it fails loudly and the bar gets
  revisited rather than quietly kept. That is the whole of what the table
  shows; see below for what it does not.
- **High is not above medium here.** 91% against 100% looks inverted, but nine
  medium cases cannot separate the two, and every high miss is already a
  recorded finding. It is the number to watch as observed traces arrive; if
  high stays below medium, the catalog's ratings are wrong.
- **It is not calibration in the probabilistic sense.** "High" does not mean
  "right 91% of the time"; on authored traces it cannot. It means the level is
  ordered the way it claims to be, which is the claim an ordinal makes.

### What the table cannot see

The table scores one of the assessment's four inputs. The eval builds every
report without a diagnosis, so the other three — the model's own rating, a
citation that does not resolve, a class the model reads differently — never
fire in the population it measures. Each of them only ever lowers the level,
so the levels scored here are ceilings, and the analyses that land in `low` in
real use are not the ten that land there in the eval.

How far those three move the corpus, if every case had a diagnosis of the
given kind:

| every case diagnosed as | needs review |
| --- | --- |
| nothing — rules only, what the table measures | 10/62 |
| high, clean | 10/62 |
| medium, clean | 10/62 |
| high, one invented citation | 19/62 |
| high, a different class from the rules | 19/62 |
| medium, one invented citation | 62/62 |
| high, an invented citation *and* a different class | 62/62 |

One sign of trouble never sends a high analysis to review; two always do,
including the 43 cases the table scores at 91%. That is by design, but it is
**a judgement, not a measurement**: nothing here shows that an analysis
demoted to low by the model's side is wrong more often than right. Those
demotions are reasoned — a diagnosis that invents a line, or contradicts the
rules, is worth a person's time — and tested, not calibrated. Scoring them
needs the diagnosis scored, which `make eval` leaves out on purpose (see the
top of this document). [`make eval-llm`](#the-diagnosis-layer-46) records, per
case, whether the model's class was right and whether its citations were real,
which is what a calibration of these demotions would start from.

Unit tests cover each adjustment, and a property test checks every
combination for the three promises: never above the rules or the model,
reasons exactly when below high, review exactly at low.

## The diagnosis layer (#46)

`make eval` answers "did a change break the rules". It cannot answer whether
the Claude diagnosis is worth its cost, or for which classes, so that has a
run of its own:

```bash
make eval-llm                                         # every case, capped at $5
pipelinemd eval --llm --max-cost 2 --case runner-oom-killed --case yaml-unknown-stage
pipelinemd eval --llm -o eval-llm.txt -o eval-llm.json  # both formats, one bill
```

It needs `pip install 'pipelinemd[llm]'` and `ANTHROPIC_API_KEY`. In GitHub
it is the **Eval (diagnosis layer)** workflow, started by hand from the
Actions tab with the key as a repository secret; its summary carries the
scorecard and the JSON is kept as an artifact. Nothing runs it on a push,
and `make gate` is unchanged.

For every case it runs `diagnose` exactly as a user would get it, then scores,
per failure class:

| Column | Question |
| --- | --- |
| **rules** | The rules' class accuracy, over the same cases the model answered, so the two columns compare like for like. |
| **model** | Was the model's `failure_class` the labelled one? |
| **grounded** | Did every line it cited exist in the excerpt it was shown? An answer citing nothing real is rejected by the diagnosis layer and counts as not grounded. |
| **agrees** | Where a rule fired, did the model put the case in the top rule's class? |

Below the table it lists the cases where the model was right and the rules
wrong, the reverse, every disagreement with the top rule, and every rejected
answer, then the run's cost from the API's own token counts (the same
`cost.py` the reports use).

**The cap.** `--max-cost` (default $5) is checked before every call: the run
stops before a call that, at the most any call has cost so far, would take the
total past it, and says where it stopped. It can only overrun by a call dearer
than all the ones before it. A model with no price on file is refused before
the first call, since its spend could not be counted. Cases are taken a class
at a time, so a run the cap ends early still covers every class.

**What it cannot measure.** The model is not allowed to answer `flaky` — that
is retry history's call — so the flaky row is one only the rules can win. And
every case is authored by the same hand as the rules, so a model that beats
the rules here has beaten an authored corpus, not the wild.

### Results

No run is recorded yet. Each run's scorecard goes here with the model,
effort, date and cost it came from, since the number belongs to that model on
that day and is not comparable across them.


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

Four thresholds, set in the [Makefile](../Makefile):

**`MIN_RULE_ACCURACY = 0.92`.** Raised from 0.85 now that rule@1 is 55/55: 0.92
is 51/55, so the build fails on the fifth regression. The headroom is still
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

**`MIN_CLASS_ACCURACY = 0.78`.** Class is 51/62 today; 0.78 is 49/62, so the
build fails on the third regression. Of the four metrics this is the one with
the sharpest downside: the class selects the fix type, `yaml_patch` is what
#24's MR generator acts on, and a mapping change that quietly regresses
classification would otherwise move a printed number and fail nothing. It
arrived with the metric, after review pointed out that promoting `class` to a
headline number without gating it repeated the gap-false-positive mistake from
#41. As with the rule floor, lowering it in a commit that says why is the
intended response to adding hard traces.

**`MIN_EVIDENCE_RATE = 1.0`.** Every case, gaps included, must keep the line
its label marks. The other floors leave headroom because hard observed traces
will often have no rule yet, and a missing rule is a catalog gap rather than a
regression. The evidence rate does not depend on a rule: it asks whether the
line that explains the failure survived into the excerpt, which is all the
model is ever shown. Losing it means a diagnosis written without its cause,
the one failure the distiller exists to prevent. So a case that loses its line
fails the build and is named on stderr, and an observed trace the distiller
cannot keep is a distiller bug to fix first. If it cannot be fixed yet, the
floor is lowered in a commit that names the case
([#14](https://github.com/rbalukja15/pipelinemd/issues/14)).

**`MAX_GAP_FALSE_POSITIVES = 0`.** Known gaps are excluded from every rate, so
a rule that starts firing on one moves *no number at all* — `--min-rule-accuracy`
cannot see it. It is also the worst thing the catalog can do: producing a
confident wrong answer where it previously had the good sense to stay silent.
All seven gaps are correctly silent today, and that is gated exactly.

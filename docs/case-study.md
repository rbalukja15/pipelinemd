# pipelinemd: reading failed CI jobs so you don't have to

*Mario Balukja*

A failed CI job hands you a log, and a log is a terminal recording, not a
report. It can run to tens of thousands of lines. Most of them are progress
bars that redrew themselves hundreds of times, dependency downloads, and
"section started" markers. The one line that explains the failure is usually
somewhere in the middle, and the last line, `ERROR: Job failed: exit code 1`,
tells you nothing except that you now have to go looking.

I built pipelinemd to do that looking. Give it a failed GitLab job or
GitHub Actions run and it tells you what broke, how sure it is, and what to do about it. This post is
about how it works, and the rules I set so its answers can be checked rather
than believed.

## Step one: distil the log

The core is a deterministic distiller: no dependencies, no network, same
output for the same input. It runs in small stages, each tested on its own:

- **ANSI and overwrites.** Colour codes are stripped, and carriage returns and
  backspaces are replayed the way a terminal would replay them. A progress bar
  that rewrote one line 500 times collapses to its final frame.
- **Sections.** GitLab's section markers and GitHub Actions' step groups
  become spans, so every line knows which step of the job it ran in.
- **Redaction.** Token-shaped strings, credentials in URLs, `Authorization:`
  headers and private keys are masked before anything leaves the process.
- **Scoring.** Every line is weighed against 27 failure signals and 5
  dampeners, so that a summary like `0 failed, 12 passed` does not outrank the
  real fault.
- **Windowing.** Context grows around the strongest lines, overlapping windows
  merge, a fixed line budget goes to the best of them, and the tail is always
  kept, because that is where the runner writes its verdict.

What comes out is a short excerpt that keeps the original line numbers. On a
real failure from the project's own CI, 211 lines became 73, and the line that
explained it (`OSError: Readme file does not exist: README.md`, line 182) was
still there when the display was cut to 14 lines.

The excerpt is then matched against a catalog of 64 known CI failure
signatures, from npm's `ERESOLVE` to a missing image tag or a runner out of
disk. Each rule explains why the failure happens and gives fixes someone can
actually carry out. Every report is placed in one of seven failure classes (`yaml`,
`ci_vars`, `image_pull`, `cache_artifact`, `test`, `runner`, `flaky`), and the
class decides the kind of fix it wants.

One class is deliberately not guessed. A job is `flaky` only when the CI's own
retry history (GitLab's retries, or GitHub's re-run attempts) says another
attempt of the same job passed on the same commit.
A log alone can't tell you that.

## Step two: an optional diagnosis, with evidence

Rules recognise failures but can't tell a cause from its fallout. For that,
pipelinemd can ask Claude. The model never sees the raw log. It gets the
distilled, redacted excerpt and what the rules already concluded, which keeps
requests small and keeps the model's effort on the judgement call.

The rule I care most about is this one: **a diagnosis must point at
something.** Every diagnosis has to cite the evidence lines it relied on, and
code, not the model, checks each cited line number against the excerpt the
model was actually shown. A diagnosis that cites nothing real is rejected
rather than reported. One that cites a mix gets printed with the invented
numbers called out next to it. Confident prose is easy to produce. A claim you
can check is the thing worth shipping.

Confidence works the same way. A report is never more confident than its
weakest signal, and loses a level for each sign of trouble, such as an
unresolved citation or a model that disagrees with the rules. At low it says
**needs human review** and why. Each report also states its cost: $0 for
rules alone, otherwise an estimate from the API's own token counts.

## Measuring it, honestly

pipelinemd ships with a labelled corpus of 69 job traces and an evaluation
that scores the deterministic half against it. `make eval` runs offline in a
couple of seconds. Today it reports:

| Metric | Result | Question |
| --- | --- | --- |
| rule@1 | 61/61 | Did the expected rule rank first, over the cases a rule covers? |
| class | 57/69 (82.6%) | Was the failure put in its labelled class? |
| evidence | 69/69 | Did the line a person would point at survive into the excerpt? |
| exit code | 69/69 | Was the runner's verdict read correctly? |
| known gaps | 8/8 silent | Where no rule covers a failure, did the catalog stay quiet? |

By confidence, the class is right in 45 of 49 high-confidence reports, 9 of 9
medium, and 3 of 11 low. Low is the only level that is wrong more often than
right, and it is the one that sends a report to review.

The caveat matters more than the table. 68 of the 69 traces are *authored*
(the other is a real failure from the project's own CI): I wrote them against the output each tool really prints, but the failures and
labels came from the same hand that wrote the rules. So these numbers are a
regression signal, not real-world accuracy, and the eval prints that caveat on
every run until more observed traces arrive.

Three habits keep the number from fooling me:

- **Gaps stay in.** Eight cases are failures no rule covers yet (RSpec,
  Vitest and PHPUnit output among them). They count against the class score
  instead of being deleted to make it look better.
- **No tuning in the same change as the measurement.** The first run of rule@1
  was 47/53. The findings were written down and fixed separately, and where a
  fix needed a corpus label corrected, the write-up says so.
- **The gate is real.** `make gate` runs on every push with floors for rule@1,
  class and evidence, and it fails if a known gap starts firing. The evidence
  floor is every case: losing the line that explains a failure is the one
  thing the distiller exists to prevent.

The model's diagnosis is scored separately, by `make eval-llm`, because it
costs money and isn't reproducible. It runs under a spending cap and never
gates a build. No scored run is recorded yet, so I'm not quoting a number for it.

## Read-only by default, one gated write

pipelinemd reads. On GitLab a token only needs `read_api`, on GitHub
`actions: read`, and nothing is written anywhere unless you ask.

The one exception is opt-in. For a failure in the pipeline file itself, like a
job naming a stage that was never declared, `diagnose --open-mr` asks Claude
for the corrected file and opens a draft merge request. It has to get through
nine gates first: the rules decided the fix type is `yaml_patch`, confidence is
high with a grounded diagnosis, the project carries a `pipelinemd-autofix`
label, the change is 20 lines or fewer, it adds nothing that looks like a
credential, GitLab's CI Lint accepts the file, and the branch it would create
doesn't already exist. It uses its own write token, never pushes to an
existing branch, and never merges.

## What's next

- **Observed traces.** The corpus needs real failures, and the numbers above
  should drop when they land. That's intended.
- **Known catalog bugs.** The rule for a denied `docker push` also matches
  pulls. It is recorded in the evaluation write-up, waiting for its own change.
- **A first recorded diagnosis eval**, with its model, date and cost.
- **More GitHub Actions rules.** Since 1.0, `diagnose` takes a GitHub
  Actions run or job URL, reads the log step by step and knows five
  Actions-specific failures. The catalog's GitHub coverage is still thin
  next to GitLab's.

pipelinemd is MIT-licensed. A failed job log it gets wrong is the most useful
thing you could send me.

## Short version

CI logs are long and noisy, and the line that explains a failure is buried.
I built pipelinemd, an open-source tool that reads a failed GitLab job or
GitHub Actions run and says what broke. A deterministic distiller strips the noise and keeps the
lines that matter, then 64 failure signatures classify the failure and
suggest a fix. An optional Claude diagnosis must cite real log lines, and
code checks every citation; a diagnosis that cites nothing real is rejected.
On a corpus of 69 labelled traces, the expected rule ranks first in 61 of 61
covered cases, and the explaining line survives distillation in all 69. All
but one of those traces are authored, so this is a regression signal, not real-world accuracy.
It's read-only by default, with an opt-in, nine-gate draft merge request for
pipeline-file fixes.

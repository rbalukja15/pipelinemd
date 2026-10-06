# Dogfooding: pipelinemd on its own CI

The corpus in [`corpus/`](../corpus/README.md) is authored, so its numbers are a
regression signal rather than a measurement of real-world accuracy. Running
pipelinemd on real failures is the other half of the evidence, and the first
real failures it had were its own.

## The run

On 2026-08-27 the first CI runs on six feature branches all failed. One of
them, where all three Python versions failed:
[run 33058513930](https://github.com/rbalukja15/pipelinemd/actions/runs/33058513930),
job `test (3.13)`. The workflow ran `pip install -e ".[dev]"` on a branch that
did not have the README yet, and hatchling refused to build package metadata
for a `pyproject.toml` whose `readme` pointed at a missing file.

GitHub keeps job logs for a limited time, so the log is kept as a test fixture:
[`tests/fixtures/traces/github/pip_metadata_missing_readme.log`](../tests/fixtures/traces/github/pip_metadata_missing_readme.log).
These are GitHub Actions logs, so pipelinemd reads them with `distill` on the
downloaded log; `diagnose` on a run URL is
[#50](https://github.com/rbalukja15/pipelinemd/issues/50).

## What it got right

The excerpt. 211 lines became 73, and the line that explains the failure,
`OSError: Readme file does not exist: README.md` at line 182, survives even
when the display is cut to 14 lines. Anyone, or any model, reading the excerpt
has the cause in front of them.

## What it got wrong, and the fix

The rule. pip wraps every failing build backend in
`error: subprocess-exited-with-error`, and `pip.build-failed` matched that line
on its own. So pipelinemd reported, at **high** confidence:

```
test → code_patch  ·  high  ·  from pip.build-failed

What pip.build-failed suggests

  No prebuilt wheel matched this platform, so pip fell back to compiling from
  source and the compiler or its headers were missing. ...
   →  Install a toolchain first: `apt-get install -y build-essential python3-dev`
```

Nothing was compiled; the advice would have sent someone to the wrong place.
A confident wrong answer is the worst thing the catalog can do, so the rule no
longer matches pip's generic wrapper alone. Real compile failures still match
on their own lines (`Failed building wheel for`, a failed `gcc`, a missing
header), and tests pin both sides. The same log now reads:

```
pipelinemd  pip_metadata_missing_readme.log
  exit code 1
  ##[error]Process completed with exit code 1.
  unclassified  ·  low  ·  no rule fired
  ⚠ needs human review — low confidence: no rule fired
  cost $0 — rules only, no model call

Evidence   73 of 211 lines · 65.4% reduced
        showing the 14 most relevant; 59 hidden
    145    Preparing editable metadata (pyproject.toml): finished with status 'error'
    146    error: subprocess-exited-with-error
        … 3 lines omitted …
    150    ╰─> [32 lines of output]
    ...
    181            raise OSError(message)
    182        OSError: Readme file does not exist: README.md
        … 3 lines omitted …
    186  error: metadata-generation-failed
        … 5 lines omitted …
    192  hint: See above for details.
    193  ##[error]Process completed with exit code 1.
```

"I don't know, and here is the line to read" is the honest answer while no
rule covers packaging metadata failures. A rule for them is the obvious next
step, and this log is its first fixture.

## What it says about the corpus

Every corpus case is written by hand, and every one of them passes. The first
real log pipelinemd met exposed a rule that was too broad. That is the case for
growing the corpus with observed traces, and for the gate's headroom on rule@1
([evaluation](evaluation.md#the-gate)): real logs are harder than authored
ones.

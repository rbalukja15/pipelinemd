# Failure corpus

60 labelled GitLab job traces, one file each, with a label record per trace in
[`corpus.jsonl`](corpus.jsonl). The tests use it to catch regressions; `make
eval` uses it to put a number on accuracy.

## Provenance — read this before quoting a number

Every case currently carries `"provenance": "authored"`. **None of these traces
were observed in the wild.** They were written against the output format each
tool really prints — npm's `ERESOLVE` block, pytest's short test summary,
containerd's `exec format error` — but the failure and the label were authored
by the same hand that wrote the rules they are matched against.

That makes an accuracy figure computed over this corpus a **regression signal,
not a measurement**. It answers "did a change break something that used to
work", which is worth having. It does not answer "how often does this tool get
a real failure right", and a headline percentage drawn only from authored cases
would be circular.

`provenance` exists so the distinction cannot be lost. As real traces are
added with `"provenance": "observed"`, the eval reports the two populations
separately and the number starts to mean what it says.

## Adding a case

1. Drop the raw trace in `traces/<id>.log`. Keep it realistic: the runner
   preamble, `section_start`/`section_end` markers, ANSI, and the
   `ERROR: Job failed:` verdict. Paste real tool output rather than paraphrasing.
2. **Redact before you commit.** A real trace may carry tokens. `pipelinemd`
   masks credentials at read time, but the file on disk is not covered by that
   — and GitHub push protection will reject a real token outright.
3. Append one record to `corpus.jsonl`:

   ```json
   {
     "id": "runner-oom-during-webpack",
     "failure_class": "runner",
     "expected_rule": "node.heap-oom",
     "exit_code": 134,
     "evidence_marker": "JavaScript heap out of memory",
     "trace": "traces/runner-oom-during-webpack.log",
     "provenance": "observed",
     "notes": "Where it came from, and anything surprising about it."
   }
   ```

4. Run `pytest tests/test_corpus.py` — it checks the record is well formed, the
   trace exists, the rule id is real, and the marker occurs in the trace.

## Fields

| Field | Meaning |
| --- | --- |
| `id` | Unique, kebab-case, matches the trace filename. |
| `failure_class` | One of the v1 taxonomy classes from #17: `yaml`, `ci_vars`, `image_pull`, `cache_artifact`, `test`, `runner`, `flaky`. |
| `expected_rule` | The catalog rule that should rank first — or `null` where no rule covers this failure yet. |
| `exit_code` | What the runner reported. |
| `evidence_marker` | Text a human would point at to explain the failure. Distillation must keep it; the eval measures how often it does. |
| `trace` | Path relative to this directory. |
| `provenance` | `authored` or `observed`. See above. |
| `notes` | Free text. Say what is unusual. |

### Why a marker rather than a line number

Line numbers shift the moment a trace is edited, so a corpus keyed on them
rots silently. A marker resolves to a line at load time and keeps meaning when
the file around it changes.

## Deliberate gaps

Seven cases carry `"expected_rule": null`. These are failures the catalog does
**not** cover — RSpec, Vitest and PHPUnit output, artifact size limits, a git
`502`, a yamllint error, an empty variable expansion — and they are in the
corpus precisely so the eval reports the gap instead of hiding it. Adding a rule should flip one of these
to a real id; deleting the case to make a number look better should not happen.

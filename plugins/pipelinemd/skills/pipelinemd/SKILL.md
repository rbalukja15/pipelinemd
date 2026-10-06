---
name: pipelinemd
description: Diagnose why a CI job failed. Use when the user shares a failed GitLab job or pipeline URL, a CI log file, or asks why their pipeline, build or CI job is red. Runs pipelinemd to distil the log and match known failure signatures, then explains the root cause and fixes it in the repository.
allowed-tools: Bash(uvx pipelinemd*), Bash(pipelinemd *), Bash(pipx run pipelinemd*), Bash(gh run view*)
---

# Diagnose a failed CI job with pipelinemd

pipelinemd turns a CI log of tens of thousands of lines into the few lines that
explain the failure, and matches them against a catalog of known CI failure
signatures, each with a fix. You read its report and do the reasoning: name the
root cause, then fix it in the user's code when the fix belongs there.

## 1. Pick the command for what the user gave you

| The user has | Run |
| --- | --- |
| A GitLab job URL (`…/-/jobs/<id>`) | `uvx pipelinemd diagnose <url> --no-llm --format markdown` |
| A GitLab pipeline URL (`…/-/pipelines/<id>`) | `uvx pipelinemd diagnose <url> --no-llm --format markdown` (add `--all-jobs` if several jobs failed and they want all of them) |
| A log file on disk | `uvx pipelinemd distill <path> --format markdown` |
| A GitHub Actions run | `gh run view <run-id> --log-failed > failed.log`, then `distill` that file |
| A log pasted into the chat | Save it to a file, then `distill` it |

Always pass `--no-llm` to `diagnose`. You are the diagnosis layer here, so the
CLI's own model call would only add cost and need an API key.

If `uvx` is missing, use `pipelinemd` directly if it is installed, or
`pipx run pipelinemd`, or
`docker run --rm -i ghcr.io/rbalukja15/pipelinemd distill - --format markdown < failed.log`.
Do not install anything without asking the user.

Private GitLab projects need a token in `GITLAB_TOKEN` (or `PIPELINEMD_TOKEN`).
For a self-hosted GitLab instance, the URL is enough; `--gitlab-url` is only
needed with `--project`/`--pipeline`/`--job`. Never print a token or ask the
user to paste one into the chat; ask them to export it in their shell.

Use `--format json` instead of `markdown` only when you need exact fields, for
example the `rule_hits[].fixes` list or `classification.failure_class`.

## 2. Read the report

The markdown report has, in order:

- **exit code** and the runner's verdict line.
- **class** — one of `yaml`, `ci_vars`, `image_pull`, `cache_artifact`, `test`,
  `runner`, `flaky` — the kind of fix it wants (for example `code_patch`,
  `config_change`, `retry`), its confidence and the rule it came from.
- **Matched rules**, each with an explanation and **Try this** fixes. These are
  known signatures, written by hand; trust them more than a guess.
- **Evidence** — the distilled log lines, with their original line numbers.
  `[xN]` means a line repeated N times.

Exit codes: `0` a report was produced; `2` bad URL or argument; `3` GitLab was
unreachable, the token was refused or the job was not found; `4` the pipeline
has no failed jobs.

## 3. Answer the user

1. Lead with the root cause in one or two sentences, citing the evidence line
   numbers you relied on. Separate the actual fault from its fallout (a failed
   install makes every later step fail too).
2. If the confidence is low or no rule matched, say so and reason from the
   evidence lines yourself. Do not present a guess as a rule match.
3. If the fix belongs in this repository (a lockfile, a test, the CI config, a
   lint error), find the file and propose the change, or make it if the user
   asked you to fix the failure.
4. If the fix is outside the repository (runner disk space, a revoked token, a
   registry outage), say what the user needs to change and where.
5. If the class is `flaky`, say that another attempt of the same job passed on
   the same commit, and suggest a retry before any code change.

## Limits

- `diagnose` only fetches from GitLab. For GitHub Actions and other CI systems,
  download the log and use `distill`.
- pipelinemd masks credential-shaped strings in its output, but it is not a
  guarantee. Do not paste raw logs into places outside the user's machine.

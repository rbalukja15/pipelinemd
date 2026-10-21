# Fixing the pipeline file with a merge request

`pipelinemd diagnose --open-mr` turns one kind of diagnosis into a draft merge
request: a failure in the pipeline file itself, such as a job naming a stage
that was never declared or an invalid key. It asks Claude for the corrected
file, has GitLab's CI Lint check it, and opens a draft merge request for a
person to review. It never pushes to an existing branch and never merges.

Nothing is written without the flag. [ADR-0002](adr/0002-gitlab-access.md)
set the rules for any write; this is how they are met.

## Setting it up

1. **A write token.** A project access token with the `api` scope and at least
   the Developer role, in a masked CI/CD variable named
   `PIPELINEMD_WRITE_TOKEN`. The read token (`CI_JOB_TOKEN`, `GITLAB_TOKEN`)
   is never used to write, so granting read access never grants this. Like the
   read token, it is only sent to the instance it belongs to
   (`CI_SERVER_URL` in a pipeline, else `PIPELINEMD_GITLAB_URL` or gitlab.com).
2. **The label.** Create a project label named `pipelinemd-autofix`. Its
   presence is the project saying automated fixes are welcome here; delete it
   to turn them off without touching any pipeline.
3. **The flag**, in the diagnose job:

   ```yaml
   script:
     - pipelinemd diagnose --open-mr -o diagnosis.html -o diagnosis.md
   ```

`ANTHROPIC_API_KEY` is needed as well: the fix is written by Claude, so
`--open-mr` cannot be combined with `--no-llm`.

## The gates

Each is checked in this order, and the first that fails ends it with the
reason on stderr. Cheap local checks come before any request, reads before
the paid model call, and CI Lint before anything is written.

| # | Gate | Why |
| --- | --- | --- |
| 1 | The fix type is `yaml_patch`, decided by the rules | The class comes from rules, which give the same answer twice, never from the model ([ADR-0001](adr/0001-stack-and-scope.md)). |
| 2 | The confidence is **high**, with a grounded diagnosis | Anything lower is a report a person should read first. A model that disagrees with the rules, or cites a line it was never shown, lowers it. |
| 3 | The project has the `pipelinemd-autofix` label | The project decides where automation is welcome, not the tool. |
| 4 | The job ran on a branch, and the pipeline file is in the project | A fix is proposed to the branch that failed. A file pulled from another project (`ci_config_path` with `@`) is not this project's to edit. |
| 5 | Claude returns the whole corrected file | It may also answer that no safe edit exists, for example when the problem is in an included file; that ends it. |
| 6 | The change is at most 20 lines (`--max-fix-lines`) | A configuration error is a typo, a missing stage, a misplaced key. A bigger change is a rewrite a person should make. |
| 7 | The change adds nothing that looks like a credential | A line naming a variable (`$DEPLOY_TOKEN`) is fine; a literal token is not. |
| 8 | GitLab's CI Lint accepts the corrected file ([#25](https://github.com/rbalukja15/pipelinemd/issues/25)) | Linted in the project's context, so `include: local` and the branch resolve as they would in a real pipeline. An invalid fix is never committed. |
| 9 | The branch `pipelinemd/fix-job-<job id>` does not exist | One proposal per failed job, and a run never adds to a branch someone may be working on. |

Then one commit on that new branch, started from the commit that failed, and a
merge request titled `Draft: fix(ci): …` against the job's branch. Its
description carries the diagnosis, the evidence lines it cites, the diff, and
how to turn these off. The source branch is deleted when it merges.

## What it costs

One more model call than the diagnosis, made only once gates 1 to 4 have
passed. It is added to the report's cost line. Writes are not retried: making
a commit or a merge request twice is worse than failing once and saying so.

## What it does not do

- Edit any file but the project's pipeline file. A problem in an included file ends it at gate 5.
- Act on any class but `yaml`, at any confidence but high.
- Merge, approve, or push to a branch that already exists.
- Run on `--from-file` logs: it needs the job, its branch and its commit.

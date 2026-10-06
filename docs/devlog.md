# Devlog

What changed, when, and what it taught. The [CHANGELOG](../CHANGELOG.md)
records what each release contains; this records how the project got there,
including the things that turned out wrong. Newest first.

## 2026-10-06: releases, and GitHub Actions logs

- **0.1.0, 0.1.1 and 0.1.2 shipped the same day.** 0.1.0 was the first
  release on PyPI and ghcr.io. The first publish was refused because the PyPI
  trusted-publisher settings didn't match the workflow, which is the kind of
  mistake only a real release finds.
- **Releases are now a merge, not a tag.** A version bump reaching `main`
  makes the workflow tag and publish it (#52). Pushing a tag by hand was one
  more step to forget, and the version is now written in one place only,
  `__init__.py`.
- **GitHub Actions logs exposed two assumptions** (#49 in 0.1.1, the rest in 0.1.2). Timestamp
  stripping assumed GitLab's stream descriptor and ate the first short word of
  GitHub's lines. Separately, everything GitHub prints after its verdict
  (post-job cleanup, service container logs) was being scored as if it were
  the job, so a Postgres container shutting down was reported as the cause of
  a failed test run. Both were found by running the tool on real logs rather
  than authored ones, which is the argument for growing the corpus with
  observed traces. Full support for GitHub is #50.
- **A Claude Code plugin** runs the rules-only path from inside an editor.

## 2026-10-05: scope

- **The hosted web service was dropped.** The original plan had an ingest
  endpoint, webhooks and a stored history. The CLI already does the job inside
  the pipeline, so the service issues were closed and the HTML report and
  Docker image re-scoped to fit a CLI. The reasoning is in
  [ADR-0001](adr/0001-stack-and-scope.md).
- **A container image** (#26), so a GitLab job can use pipelinemd without
  installing anything at the moment something is already broken.

## 2026-09-29 to 2026-10-03: what a report claims

- **Every report gets a v1 class and a fix type** (#17). Only the top rule and
  GitLab's retry history decide it; the model's opinion is shown but never
  applied, because `yaml_patch` is the one fix type that can lead to an
  automated write.
- **Restated causes are scored at the runner's verdict.** Real runner output
  prints the cause several times before the verdict, and the generic
  "system failure" rule kept winning on recency alone. Which restated causes
  get promoted is decided per rule, because the two proxies tried first,
  category and section, both gave wrong answers.
- **Confidence and cost on every analysis** (#18). Confidence is capped at
  the weakest signal behind it and stays an ordinal, because a decimal would
  claim a calibration nothing here has.

## 2026-09-10 to 2026-09-22: making it measurable

- **Diagnoses must cite lines** (#16), and a citation only counts if the line
  was in the excerpt the model was shown. An early version resolved citations
  against the whole trace, which let a guess pass as grounded.
- **A labelled corpus and an eval harness** (#13, #22). 60 traces at first,
  62 now, scored on every push. Its first run found four rule misses, all
  since fixed.

## 2026-08-27: the core

- The distiller, the rule catalog, the GitLab client, the Claude diagnosis
  layer and the CLI, each in its own module with no third-party dependencies
  in the core. [Architecture](architecture.md) describes how they fit.

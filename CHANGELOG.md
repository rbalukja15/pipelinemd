# Changelog

All notable changes to pipelinemd are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
How a section becomes a release is in [docs/releasing.md](docs/releasing.md).

## [Unreleased]

### Added

- `--format html` writes the report as one self-contained page: inline styles,
  no scripts, nothing fetched, light and dark mode, and a print layout. Log
  text is escaped, evidence lines keep their original numbers as `#L<n>`
  anchors, and the page says plainly whether it is rules only, a diagnosis the
  model could not ground, or one that needs human review. `--all-jobs` puts
  every job on one page with the run's total cost
  ([#19](https://github.com/rbalukja15/pipelinemd/issues/19)).
- `-o` can be given more than once to write one analysis in several formats,
  each chosen by the file's extension, so the model is asked once rather than
  once per file.

### Changed

- The example GitLab job writes `pipelinemd-diagnosis.html` beside the
  markdown and JSON from a single run, and links the report from the merge
  request with `expose_as`. It used to run the analysis once per format, which
  asked the model twice and could leave two files that disagreed.

### Fixed

- `pip.build-failed` no longer fires on pip's generic
  `error: subprocess-exited-with-error` alone. pip prints that for any failing
  build backend, so a `pyproject.toml` naming a README that did not exist was
  reported at high confidence as a missing C compiler. Real compile failures
  still match on their own lines. Found by running pipelinemd on its own early
  CI failures ([#23](https://github.com/rbalukja15/pipelinemd/issues/23)).

### Security

- A GitLab token from the environment (`$GITLAB_TOKEN` and the others) is now
  only sent to the instance it belongs to: `$PIPELINEMD_GITLAB_URL`,
  `$CI_SERVER_URL` inside a pipeline, or gitlab.com. A pasted URL on any other
  host is fetched without it, and pipelinemd says the token was withheld.
  Before, the token went to whatever host the URL named. For a self-managed
  instance outside CI, set `PIPELINEMD_GITLAB_URL` or pass `--gitlab-url`; a
  token given with `--token` is still sent where the command points
  ([#6](https://github.com/rbalukja15/pipelinemd/issues/6)).

## [0.1.2] - 2026-10-06

### Added

- A Claude Code plugin, installable from this repository, with a skill that
  runs pipelinemd on a failed GitLab job URL or a downloaded CI log and works
  from its report instead of the raw log. It runs the rules only, so it needs
  no Anthropic API key.

### Fixed

- On a GitHub Actions log, what runs after GitHub's verdict
  (`##[error]Process completed with exit code N.`) is now treated as fallout,
  as GitLab's `after_script` already was. That covers `if: failure()` steps,
  post-job cleanup and service container logs. A Postgres service printing
  `sh: locale: not found` while it shut down had been reported as the cause
  of failed test and install jobs, with a `yaml_patch` fix. The excerpt now
  ends at the verdict instead of in the cleanup, and the verdict is reported
  as the job's failure reason. Exit codes printed during cleanup are ignored.
- npm 10's `npm error` lines are read like the `npm ERR!` lines earlier
  versions printed, both when choosing the excerpt and in the npm rules. Lines
  inside a `docker build` are scored without BuildKit's `#9 1.481` prefix, so
  an `npm ci` failure in a Dockerfile now shows the dependency conflict
  itself.

## [0.1.1] - 2026-10-06

### Fixed

- Stripping runner timestamps no longer deletes a short first word (`Post`,
  `Run`, `with`) or the indentation from lines stamped with the time alone, as
  GitHub Actions logs are, and the byte order mark those logs start with no
  longer keeps the first line's timestamp. Only GitLab's own stream descriptor
  goes with the timestamp, including the `+` form on continuation lines
  (`00O+text`), so section markers on those lines are now read too
  ([#49](https://github.com/rbalukja15/pipelinemd/issues/49)).

## [0.1.0] - 2026-10-06

The first release.

### Added

- `pipelinemd diagnose` takes a GitLab job or pipeline URL, or `--project`
  with `--pipeline`/`--job`, and with no target at all diagnoses the pipeline
  it is running in. On a pipeline it picks the failed job, names the rest, and
  `--all-jobs` diagnoses every one. Credentials come from `--token`,
  `$PIPELINEMD_TOKEN`, `$GITLAB_TOKEN`, `$GITLAB_PRIVATE_TOKEN` or
  `$CI_JOB_TOKEN`.
- `pipelinemd distill` distils a saved trace, or one on stdin, fully offline.
- A deterministic distiller: replays ANSI escapes and `\r`/`\b` overwrites,
  parses GitLab sections, strips runner timestamps, scores every line against
  failure signals and dampeners, and spends a fixed line budget on the windows
  that explain the outcome, always keeping the runner's verdict at the tail.
- Redaction of credential-shaped text (GitLab, GitHub, AWS and Slack tokens,
  JWTs, URL credentials, `Authorization:` headers, secret flags and
  assignments, PEM private keys) before evidence reaches the prompt, the
  terminal or the JSON output.
- A catalog of 59 known CI failure signatures, each with an explanation and
  fixes, browsable with `pipelinemd rules` and `pipelinemd explain`.
- Every report is placed in one of seven v1 failure classes (`yaml`,
  `ci_vars`, `image_pull`, `cache_artifact`, `test`, `runner`, `flaky`) with a
  fix type. Flaky comes from GitLab's retry history: another attempt of the
  same job passing on the same commit.
- An optional Claude diagnosis (the `[llm]` extra, `ANTHROPIC_API_KEY`) that
  reads only the distilled evidence and must cite evidence lines; a diagnosis
  citing nothing real is rejected, and invented line numbers are shown beside
  one that cites a mix. A failed call is never fatal.
- An analysis-level confidence capped at its weakest signal, with a **needs
  human review** flag at low, and an estimated cost from the API's own token
  counts, totalled across a run with `--all-jobs`.
- Terminal, Markdown and JSON output. JSON is versioned by `schema_version`.
- A labelled corpus of 62 failure traces across the v1 taxonomy, and
  `pipelinemd eval` (`make eval`, `make gate`) scoring rule@1, class accuracy,
  evidence hit rate and exit-code accuracy, with thresholds that fail CI on a
  regression.
- A Docker image, `ghcr.io/rbalukja15/pipelinemd`, for amd64 and arm64, so a
  GitLab CI job can diagnose a failed pipeline without a pip install, and a
  release workflow that publishes it and the PyPI package as one version.

[Unreleased]: https://github.com/rbalukja15/pipelinemd/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/rbalukja15/pipelinemd/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/rbalukja15/pipelinemd/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/rbalukja15/pipelinemd/releases/tag/v0.1.0

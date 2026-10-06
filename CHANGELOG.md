# Changelog

All notable changes to pipelinemd are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
How a section becomes a release is in [docs/releasing.md](docs/releasing.md).

## [Unreleased]

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

[Unreleased]: https://github.com/rbalukja15/pipelinemd/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rbalukja15/pipelinemd/releases/tag/v0.1.0

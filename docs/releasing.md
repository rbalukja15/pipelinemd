# Releasing

A release is one merge. When a pull request that bumps `__version__` lands
on `main`,
[`.github/workflows/release.yml`](../.github/workflows/release.yml) tags the
merged commit `vX.Y.Z` and publishes the same version three ways:

| Where | What | Credential |
| --- | --- | --- |
| PyPI | `pipelinemd` sdist and wheel | trusted publishing (OIDC), no stored token |
| ghcr.io | `ghcr.io/rbalukja15/pipelinemd`, amd64 + arm64, tagged `X.Y.Z`, `X.Y` and `latest` | the workflow's `GITHUB_TOKEN` |
| GitHub Releases | release `vX.Y.Z`, notes from `CHANGELOG.md`, dist files attached | the workflow's `GITHUB_TOKEN` |

## Why the jobs run in this order

```
         ┌─► verify ─┐
detect ──┤           ├─► tag ─► pypi ─► image ─► github-release
         └─► dist ───┘
```

- **detect** decides whether this push is a release: it is if `__version__`
  is a final `X.Y.Z` and the tag `vX.Y.Z` does not exist yet. Otherwise every
  other job is skipped, and the run's summary says why. Pre-releases such as
  `0.2.0rc1` are never released, because the image's semver tags cannot be
  computed from them.
- **verify** runs `make check` on the merged commit and refuses the release
  unless `CHANGELOG.md` has a dated section for the version and nothing left
  under `[Unreleased]`. Verify comes before anything is tagged or uploaded
  because PyPI never accepts the same version twice: a mistake caught here is
  fixed on `main`; caught after the upload, it costs a version number.
- **dist** builds the sdist and wheel once and checks them with
  `twine check --strict`. Those exact files go to PyPI and onto the release
  page.
- **tag** pushes the annotated tag `vX.Y.Z` on the merged commit. From then
  on the version counts as started: later pushes to `main` skip it, and a
  failure further on is recovered by re-running the run's failed jobs, which
  find its own tag and carry on.
- **pypi** publishes them.
- **image** waits for PyPI. It does not need anything from it — the image is
  built from the merged source — but an image tag can be pushed again and a
  PyPI version cannot, so this order means a failure never leaves an image for
  a version that does not exist as a package.
- **github-release** comes last, so a release page only ever announces
  something that was published.

The version lives in one place, `__version__` in
`src/pipelinemd/__init__.py`. Hatch reads it into the package metadata, the
image reports it with `--version`, and detect makes the tag from it.

## One-time setup

None of this can be done from the repository; it is settings on PyPI and
GitHub.

1. **PyPI trusted publisher.** On PyPI, under *Your account → Publishing*, add
   a pending publisher (the project does not exist until the first upload):

   | Field | Value |
   | --- | --- |
   | PyPI project name | `pipelinemd` |
   | Owner | `rbalukja15` |
   | Repository name | `pipelinemd` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   After the first release it becomes an ordinary publisher on the project.

2. **The `pypi` environment.** In the repository's *Settings → Environments*,
   create an environment named `pypi`. The trusted publisher only accepts
   tokens from jobs in it. Two optional protections are worth having:
   - *Required reviewers*: every release then waits for a click before
     anything is uploaded.
   - *Deployment branches and tags*: allow only the `main` branch. Releases
     run on `main` now, not on a tag, so a rule allowing only `v*.*.*` tags
     would block every upload.

   If a ruleset restricts creating `v*` tags, add GitHub Actions to its
   bypass list, or the tag job's push is refused.

3. **Make the image public.** After the first release has pushed the image,
   open the package (*Your profile → Packages → pipelinemd*), and under
   *Package settings* change its visibility to **public**. GitHub creates
   packages pushed from a workflow as private, and a private image cannot be
   pulled by anyone else's GitLab runner. On the same page, confirm the
   package is linked to the `pipelinemd` repository (the image's
   `org.opencontainers.image.source` label should have done it) and that,
   under *Manage Actions access*, the repository has at least write access —
   the next release pushes to the package with the same `GITHUB_TOKEN`.

## Each release

1. **Bump the version** in `src/pipelinemd/__init__.py`. It is the only place
   it is written down.
2. **Date the changelog.** In `CHANGELOG.md`, turn the `[Unreleased]` section
   into `## [X.Y.Z] - YYYY-MM-DD` with today's date, add a fresh empty
   `## [Unreleased]` above it, and update the links at the foot of the file.
3. **Check it locally.** This is the check verify makes, and prints the notes
   the release page will carry:

   ```bash
   TAG=vX.Y.Z make release-notes
   ```

4. **Merge** the pull request into `main`, once CI is green. That starts the
   release; there is nothing to tag.
5. **Watch the Release workflow.** Approve the `pypi` deployment if the
   environment requires a reviewer.

Never push a `vX.Y.Z` tag by hand, and never create the release from GitHub's
release page (which creates the tag too). The workflow treats an existing tag
as a version already released, so that version would never be published.

## When a release fails part-way

| Failed job | What was published | What to do |
| --- | --- | --- |
| detect | nothing | Re-run the failed jobs. If it says the tag exists and this is a re-run, see below. |
| verify or dist | nothing, not even the tag | Fix on `main`, before merging anything else. Until this version ships, the next merge touching `__init__.py`, `CHANGELOG.md` or the workflow retries it from that merge's commit, and verify refuses it while `[Unreleased]` has entries; move them into the version's section if something did merge first. If the fix touches none of those files, run the Release workflow on `main` from the Actions tab. |
| tag | nothing | A refused push is usually a ruleset (see one-time setup); fix it and re-run the failed jobs. If it says the tag is on another commit, another run or a person tagged that version after detect ran. If a release was published from it, there is nothing to do; if nothing was, delete the tag (`git push --delete origin vX.Y.Z`) and re-run the failed jobs. |
| pypi | the tag | Usually the trusted publisher, the environment name, or the environment's deployment rule, which must allow the `main` branch. Fix the setting and re-run the failed jobs. |
| image | tag and package | Re-run the failed jobs. Image tags are overwritten, so this is safe. |
| github-release | tag, package and image | Re-run the failed job. |

Re-run the failed jobs of the same run rather than starting a new one: once
the tag exists, a new run skips that version. Press *Re-run failed jobs*, never
*Re-run all jobs*: that starts again at detect, which finds the tag and fails,
and GitHub only re-runs a run's latest attempt, so the jobs after tag can no
longer be resumed. The same goes for a run too old to re-run. To recover a
version stranded like that: if PyPI does not have it, delete the tag
(`git push --delete origin vX.Y.Z`) and run the Release workflow on `main`
from the Actions tab; if PyPI has it, bump to the next version. Never delete
or move a tag once PyPI has the version: a new run would fail at the upload,
and the tag would no longer match what PyPI holds.

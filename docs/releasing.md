# Releasing

A release is one tag. Pushing `vX.Y.Z` runs
[`.github/workflows/release.yml`](../.github/workflows/release.yml), which
publishes the same version three ways:

| Where | What | Credential |
| --- | --- | --- |
| PyPI | `pipelinemd` sdist and wheel | trusted publishing (OIDC), no stored token |
| ghcr.io | `ghcr.io/rbalukja15/pipelinemd`, amd64 + arm64, tagged `X.Y.Z`, `X.Y` and `latest` | the workflow's `GITHUB_TOKEN` |
| GitHub Releases | release `vX.Y.Z`, notes from `CHANGELOG.md`, dist files attached | the workflow's `GITHUB_TOKEN` |

## Why the jobs run in this order

```
verify ─┐
        ├─► pypi ─► image ─► github-release
dist ───┘
```

- **verify** runs `make check` on the tagged commit and refuses the tag unless
  it equals `__version__` and `CHANGELOG.md` has a dated section for it. Only
  final `vX.Y.Z` versions are released; a pre-release tag such as `v0.2.0rc1`
  is refused here, because the image's semver tags cannot be computed from it
  and the failure would otherwise come after the PyPI upload. Verify comes
  first because PyPI never accepts the same version twice: a mistake caught
  here is fixed by re-tagging; caught after the upload, it costs a version
  number.
- **dist** builds the sdist and wheel once and checks them with
  `twine check --strict`. Those exact files go to PyPI and onto the release
  page.
- **pypi** publishes them.
- **image** waits for PyPI. It does not need anything from it — the image is
  built from the tagged source — but an image tag can be pushed again and a
  PyPI version cannot, so this order means a failure never leaves an image for
  a version that does not exist as a package.
- **github-release** comes last, so a release page only ever announces
  something that was published.

The version lives in one place, `__version__` in
`src/pipelinemd/__init__.py`. Hatch reads it into the package metadata, the
image reports it with `--version`, and verify compares the tag against it.

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
   - *Deployment branches and tags*: allow only tags matching `v*.*.*`.

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
   it is written down. (The first release skips this: `0.1.0` is already the
   version.)
2. **Date the changelog.** In `CHANGELOG.md`, turn the `[Unreleased]` section
   into `## [X.Y.Z] - YYYY-MM-DD` with today's date, add a fresh empty
   `## [Unreleased]` above it, and update the links at the foot of the file.
   For the first release, replace the placeholder date on `[0.1.0]`.
3. **Check it locally.** This is the check verify makes, and prints the notes
   the release page will carry:

   ```bash
   TAG=vX.Y.Z make release-notes
   ```

4. **Merge** the change to `main` through a pull request, once CI is green.
5. **Tag the merged commit and push the tag:**

   ```bash
   git switch main && git pull
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

6. **Watch the Release workflow.** Approve the `pypi` deployment if the
   environment requires a reviewer.

## When a release fails part-way

| Failed job | What was published | What to do |
| --- | --- | --- |
| verify or dist | nothing | Fix on `main`, delete the tag (`git push --delete origin vX.Y.Z`), tag again. |
| pypi | nothing | Usually the trusted publisher or the environment name. Fix the setting and re-run the failed jobs. |
| image | the package | Re-run the failed jobs. Image tags are overwritten, so this is safe. |
| github-release | package and image | Re-run the failed job. |

Never delete and re-push a tag once PyPI has the version: the workflow would
fail at the upload, and the tag would no longer match what PyPI holds.

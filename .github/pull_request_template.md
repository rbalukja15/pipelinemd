## What

<!-- One or two sentences: what this PR changes and why. -->

## Related issue

Closes #

## How to verify

<!-- The commands a reviewer should run, and what they should see. -->

```bash
make install
make check               # lint + typecheck + test + eval gate, CI's test job
make image image-smoke   # CI's image job; needs docker, matters if the Dockerfile or packaging changed
```

## Checklist

- [ ] One issue per PR
- [ ] Conventional Commit messages
- [ ] Tests cover the change
- [ ] `ruff`, `mypy --strict` and `pytest` all pass locally

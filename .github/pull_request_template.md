## What

<!-- One or two sentences: what this PR changes and why. -->

## Related issue

Closes #

## How to verify

<!-- The commands a reviewer should run, and what they should see. -->

```bash
pip install -e ".[dev]"
ruff check src tests && ruff format --check src tests
mypy
pytest
```

## Checklist

- [ ] One issue per PR
- [ ] Conventional Commit messages
- [ ] Tests cover the change
- [ ] `ruff`, `mypy --strict` and `pytest` all pass locally

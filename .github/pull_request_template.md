## What and why

## Checks

- [ ] `uv run ruff check src tests results` and `uv run ruff format --check src tests results`
- [ ] `uv run pytest -q` (real-VM tests run locally)
- [ ] For a new or changed task: `uv run chronobox-ops validate TASK --repeat 3 --grade both` passes

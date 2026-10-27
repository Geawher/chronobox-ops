# Contributing

The most valuable contribution is **a task written by someone other than the benchmark's
authors**: a realistic operations job with a trap you have seen in real life. Fixes to the
harness, the checks and the docs are welcome too. For anything large, open an issue first.

## Set up

Follow [Run it yourself](README.md#run-it-yourself) up to `chronobox doctor`. Then:

```bash
uv run ruff check src tests results
uv run ruff format --check src tests results
uv run pytest -q          # the real-VM tests skip without KVM; run them locally
```

## Adding a task

1. Read [docs/AUTHORING.md](docs/AUTHORING.md): the `Scenario` contract and the rules a task must
   follow (offline, a realistic prompt with no hint, a reference with zero harm, a naive fix that
   fails).
2. Add it to the module of its theme in `src/chronobox_ops/tasks/` (the
   [task list](src/chronobox_ops/tasks/README.md) says which), and a row to that list.
3. Validate it until it passes every round:

   ```bash
   uv run chronobox-ops validate YOUR-TASK --repeat 3 --grade both
   ```

4. In the pull request, say where the trap comes from (an incident, a postmortem, a known
   footgun) and paste the validation summary.

New tasks change the task count and are not in the published results: those stay as they are,
and `tests/test_published_results.py` keeps them rebuilding from their own runs.

## Style

- Every function and test has a docstring: one line when that says it, Google style otherwise.
- Name constants instead of writing numbers inline; comments say why, not what.
- One topic per pull request.

## Reporting a problem with a result

If you think a check is wrong (a correct solution fails, or a harmful one passes), open an issue
with the task, the run's `result.json`, and what you expected. Corrections are listed in
[results/README.md](results/README.md#corrections-since-the-first-results).

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).

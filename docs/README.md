# chronobox-ops documentation

Start with the [README](../README.md): the results, how a task works, and a quick start. These pages
go further.

| Page | Read it to |
|---|---|
| [Running the benchmark](running.md) | reproduce the leaderboard or hand-off mode, with the cost and time per model |
| [The tasks](tasks.md) | see the 37 tasks by theme, and what every task must pass |
| [How runs are graded](scoring.md) | understand success, harm, grading, the setups, and the words used |
| [Method and limitations](method.md) | know what the results can and cannot tell you |
| [Writing a task](AUTHORING.md) | add a task of your own |
| [Results](../results/README.md) | every table, interval and per-task result, and how each rebuilds |

## Code layout

```
src/chronobox_ops/  scenario.py · tasks/ (37 tasks by theme) · scoring.py · validate.py · cli.py
                    handoff.py (hand-off mode) · harness/ (machine: probes, secrets, damage · setups
                    · agents · scheduler · runner) · report/ (results, Markdown, HTML, validation)
agent/              sandbox image for full-access setups
docs/               these pages, AUTHORING.md (how to write a task)
results/            the published runs, every table, and the scripts that rebuild them
```

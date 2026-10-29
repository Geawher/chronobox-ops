# Running the benchmark

The [README](../README.md#run-it-yourself) has the quick start. This page has everything: the
requirements, a check without any model, one cheap run, the full leaderboard and hand-off mode.

## Requirements

- **x86_64 Linux with KVM and Docker**, usable by your user (`sudo usermod -aG kvm,docker $USER`,
  then log out and back in), and [uv](https://docs.astral.sh/uv/).
- **Memory:** each run's machine needs 1–3 GB; runs start only when there is memory for them. The
  published grid ran 3 at a time on a 32 GB host.
- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** and a token for it: run
  `claude setup-token` and save the token to `~/.config/chronobox/claude-token` (mode 600), or pass
  `--token-file`. Every run uses it, so a whole grid runs under one account.

## Install

```bash
git clone https://github.com/Geawher/chronobox-ops && cd chronobox-ops
uv sync                      # installs chronobox too, at the commit in uv.lock
uv run chronobox setup       # chronobox's VM files, once (5-10 minutes, about 700 MB)
uv run chronobox doctor      # checks KVM, Docker and the files, and boots a machine
agent/build.sh               # the sandbox image Claude Code runs in for the full-* setups
```

## Five minutes, no model: check a task

```bash
uv run chronobox-ops list                     # the 37 tasks
uv run chronobox-ops validate disk-cleanup    # untouched fails, the reference succeeds, the naive fix fails...
```

`validate` runs a task's own reference and naive solutions, without any model, and checks that the
task tells them apart ([what it checks](AUTHORING.md)).

## One cheap run

```bash
uv run chronobox-ops run --tasks disk-cleanup --setups full-alone full-chronobox \
  --model claude-haiku-4-5-20251001 --out runs/try
uv run chronobox-ops report runs/try --markdown runs/try/tables.md
```

Two runs, a few minutes each, about $0.30 at API prices. Each run's folder keeps the transcript, the
damage counts and `result.json`.

## Reproduce the leaderboard

One model, every task, both setups, 3 seeds, with the published limits ($3 and 30 minutes per run):

```bash
uv run chronobox-ops run --tasks all --setups full-alone full-chronobox --seeds 3 \
  --model claude-sonnet-5-5 --max-usd 3 --out runs/sonnet-5-5
uv run chronobox-ops report runs/sonnet-5-5 --skip-tasks ctf-one-time-token ctf-chain ctf-vault
```

Runs resume where they stopped (`--force` reruns finished ones). What the published grid took, per
model (222 runs, 3 at a time):

| Model | Cost at API prices | Agent time |
|---|---|---|
| Sonnet 5.5 | $26 | 16 h |
| Haiku 4.5 | $40 | 16 h |
| Opus 5.5 | $65 | 21 h |
| Sonnet 5 | $110 | 28 h |
| Opus 4.8 | $250 | 30 h |

Hand-off mode, on its 10 tasks (the grids used `--max-usd 4`, and 10 for Opus 4.8):

```bash
uv run chronobox-ops handoff run --setups full-chronobox --seeds 3 --model claude-sonnet-5-5 --out runs/handoff
uv run chronobox-ops handoff summary runs/handoff
```

The older MCP-only setups (`single-shot`, `alone`, `rollback`, `chronobox`) still run, with your
Claude Code login when there is no token; the published results use the `full-*` ones.

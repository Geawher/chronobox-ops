<p align="center"><img src="docs/logo.svg" width="112" alt=""></p>

<h1 align="center">chronobox-ops</h1>

<p align="center"><b>Can an AI agent fix production without breaking it?</b><br>
A benchmark of coding agents on live servers: did they do the job, and what did they break on the way?</p>

<p align="center">
  <a href="https://github.com/Geawher/chronobox-ops/actions/workflows/ci.yml"><img src="https://github.com/Geawher/chronobox-ops/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="Apache-2.0"></a>
  <img src="https://img.shields.io/badge/tasks-37-teal" alt="37 tasks">
  <img src="https://img.shields.io/badge/runs%20published-1%2C477-teal" alt="1,477 runs published">
</p>

Agents are increasingly asked to operate on real machines: upgrade a dependency, migrate a
database, roll out a config, clean up an incident. The hard part is rarely typing the right
command. It is **not breaking the system on the way**: losing data, dropping traffic, or doing
something that cannot be undone. Most agent benchmarks check only the end state; chronobox-ops
also measures the damage.

Each of the 37 tasks is a real Linux machine (a Firecracker microVM) with real services, real data
and **clients that keep using it while the agent works**, plus a trap a careful engineer would
avoid. The benchmark compares Claude Code working **alone** with Claude Code using
[chronobox](https://github.com/Geawher/chronobox), which lets the agent fork the live machine, try
a change on the exact copy, and apply it for real only once it works.

## Results

Five Claude models as normal Claude Code (its own tools, in a sandbox), alone and with chronobox,
on 34 tasks × 3 seeds (the three CTF tasks are reported apart, see [method](docs/method.md)).
A run **succeeds** if the job is done without harming users; it **harmed users** if anything it
must not do happened, done or not.

| Model | Success alone → chronobox | Harmed users alone → chronobox | Cost / run (alone) |
|---|---|---|---|
| Opus 5.5 | 96% → 96% | 3% → 4% | $0.30 |
| Opus 4.8 | 82% → 89% | 13% → 7% | $1.08 |
| Sonnet 5 | 75% → 85% | 24% → 12% | $0.46 |
| Sonnet 5.5 | 72% → 83% | 26% → 14% | $0.11 |
| Haiku 4.5 | 29% → 37% | 55% → 43% | $0.15 |

- **Four of five models succeed more often and harm users less often with chronobox.** The
  clearest are Sonnet 5 (+11 points of success, −12 of harm, both outside chance) and Sonnet 5.5
  (+12 and −13 over 3 seeds; +14 and −14, outside chance, over 6).
- **Opus 5.5 gains nothing:** it already succeeds on 96% of runs alone.
- **A cheaper model with chronobox does as well as a stronger one alone, within what we can
  measure.** Over 6 seeds, Sonnet 5.5 with chronobox succeeds on 86% of runs and harms users on
  12%; Opus 4.8 alone, 82% and 13%, at 8 times the cost ($1.09 against $0.13 per run). The 95%
  intervals of the differences, [−3, +13] and [−7, +5] points, include zero both ways. It does not
  reach Opus 5.5 alone (96%, 3%).
  [Six-seed follow-up](results/README.md#six-seeds-a-cheaper-model-with-chronobox-vs-a-stronger-one-alone).

Every table, confidence intervals and per-task results: [`results/`](results/README.md), where
each file rebuilds from the published runs with one command.

### Hand-off mode: a script made on a copy (preliminary)

`chronobox-ops handoff` grades another way of working: the agent gets a staging copy of the
task's machine and hands over a script, which is run on fresh productions: an exact copy, one that
has drifted since (more rows, rows staging never had, another leader), and the drifted one with
the script killed halfway and run again. With chronobox, `verify_script` replays the script on a
fresh fork, under traffic, killed and rerun, before handing it over. On the 10 tasks that suit a
hand-off, 3 seeds, normal Claude Code everywhere, graded on the drifted production:

| Model | Alone, live | chronobox, live | chronobox hand-off |
|---|---|---|---|
| Sonnet 5.5 | 17/30, harm 13, $0.15, 1.7 min | 20/30, harm 9, $0.19+, 2.4 min | **24/30, harm 5**, $0.22+, 2.4 min |
| Opus 4.8 | 18/30, harm 10, $1.64+, 9.6 min | 22/30, harm 7, $1.89+, 12.0 min | 18/30, harm 6, $2.12+, 16.9 min |
| Opus 5.5 | 28/30, harm 2, $0.42, 5.6 min | 28/30, harm 2, $0.44, 5.8 min | **30/30, harm 0**, $1.12, 5.2 min |

The hand-off harmed users least for every model, and Sonnet 5.5's hand-offs beat Opus 4.8 working
live. Opus 4.8 did not gain: 7 of its 30 hand-offs ran out of the 30 minutes. A "+" marks costs
that leave out runs killed at the time limit, which record none. The live columns are the
leaderboard's runs, a week older (other Claude Code and chronobox versions, a $3 budget against
$4–10): read the comparison as a direction. [Details](results/README.md#hand-off-mode).

## How a task works

1. **The machine boots** with its services, data, and clients that write and read all the time.
2. **The agent gets the prompt** (a realistic request, with no hint about the trap) and works.
   Hidden probes measure the damage every 3 s: failed requests, lost writes, changed data.
3. **When the agent stops, the grader fast-forwards** the task's delayed effects (the nightly job,
   the id counter near its limit...) and runs a hidden check: is the job done, and does it stay
   done?

The 37 tasks cover databases, upgrades, live operations, security and problems found only by
trying: [the tasks](docs/tasks.md). How success and harm are decided: [grading](docs/scoring.md).

## Run it yourself

You need x86_64 Linux with KVM and Docker, [uv](https://docs.astral.sh/uv/), and
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) with a token from
`claude setup-token` saved to `~/.config/chronobox/claude-token`
([requirements](docs/running.md#requirements)).

```bash
git clone https://github.com/Geawher/chronobox-ops && cd chronobox-ops
uv sync && uv run chronobox setup && uv run chronobox doctor
agent/build.sh                                  # the sandbox image Claude Code runs in

uv run chronobox-ops validate disk-cleanup      # five minutes, no model: the task tells good fixes from bad
uv run chronobox-ops run --tasks disk-cleanup --setups full-alone full-chronobox \
  --model claude-haiku-4-5-20251001 --out runs/try    # two runs, about $0.30
uv run chronobox-ops report runs/try
```

Reproducing the leaderboard ($26-250 per model) and hand-off mode:
[running the benchmark](docs/running.md).

## Limitations

The tasks are ours, built around the hazards chronobox is designed for, and public since
2026-10-12; samples are small (3 seeds); only Claude models through Claude Code were run. Read
the results with that in mind: [method and limitations](docs/method.md).

## Documentation

| Page | What is in it |
|---|---|
| [Running the benchmark](docs/running.md) | requirements, the leaderboard and hand-off commands, cost and time per model |
| [The tasks](docs/tasks.md) | the 37 tasks by theme, and what each must pass |
| [How runs are graded](docs/scoring.md) | success, harm, grading, setups, the words used |
| [Method and limitations](docs/method.md) | what the results can and cannot tell you |
| [Writing a task](docs/AUTHORING.md) | adding a task of your own |
| [Results](results/README.md) | every table, interval and per-task result |

## Adding tasks

See [docs/AUTHORING.md](docs/AUTHORING.md). A task is accepted when `chronobox-ops validate NAME`
passes. Tasks written by people other than the benchmark's authors are the most valuable
contribution: see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0, see [LICENSE](LICENSE).

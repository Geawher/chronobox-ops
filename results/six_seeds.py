"""Six-seed follow-up: rebuild the tables of `six-seeds.md` from `results.json` (seeds 0-2) and
`results-seeds.json` (seeds 3-5 of three setups).

usage: python3 results/six_seeds.py > results/six-seeds.md

Intervals: 95% bootstrap of the difference between two setups, 10,000 resamples. Each resample
draws the 34 tasks with replacement (the same draw for both setups), then each task's runs with
replacement, so the interval covers both the choice of tasks and run-to-run luck.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Literal

HERE = Path(__file__).parent
CTF_TASKS = ("ctf-one-time-token", "ctf-chain", "ctf-vault")
RESAMPLES = 10_000
FIRST_SEEDS = range(0, 3)
NEW_SEEDS = range(3, 6)
ALL_SEEDS = range(0, 6)
MODEL_NAMES = {
    "claude-opus-5-5": "Opus 5.5",
    "claude-opus-4-8": "Opus 4.8",
    "claude-sonnet-5-5": "Sonnet 5.5",
    "claude-sonnet-5": "Sonnet 5",
    "claude-haiku-4-5-20251001": "Haiku 4.5",
}

Metric = Literal["success", "harmed"]
Setup = tuple[str, str]  # (model, condition)
Scores = dict[str, list[float]]  # task -> one score per run


def load_runs() -> list[dict]:
    """All runs from both result files, without the CTF tasks."""
    runs = json.loads((HERE / "results.json").read_text())
    runs += json.loads((HERE / "results-seeds.json").read_text())
    return [run for run in runs if run["scenario"] not in CTF_TASKS]


RUNS = load_runs()
TASKS = sorted({run["scenario"] for run in RUNS})


def scores(setup: Setup, metric: Metric, seeds: range) -> Scores:
    """One 0/1 score per run of ``setup`` on ``metric``, grouped by task, for the given seeds."""
    by_task: Scores = defaultdict(list)
    for run in RUNS:
        if (run["model"], run["condition"]) == setup and run["seed"] in seeds:
            by_task[run["scenario"]].append(float(bool(run["reward"][metric])))
    return by_task


def mean(by_task: Scores) -> float:
    """Mean score over all runs of every task."""
    values = [value for task in TASKS for value in by_task[task]]
    return sum(values) / len(values)


def mean_cost(setup: Setup, seeds: range) -> float:
    """Mean API cost per run in USD.

    Runs stopped at the time limit report no cost and count as 0, as in the leaderboard.
    """
    costs = [
        run["cost_usd"] or 0.0 for run in RUNS if (run["model"], run["condition"]) == setup and run["seed"] in seeds
    ]
    return sum(costs) / len(costs)


def difference_interval(before: Scores, after: Scores) -> tuple[float, float]:
    """95% bootstrap interval of ``mean(after) - mean(before)``, resampling tasks then runs."""
    rng = random.Random(0)
    differences = []
    for _ in range(RESAMPLES):
        tasks = [rng.choice(TASKS) for _ in TASKS]
        before_runs = [rng.choice(before[task]) for task in tasks for _ in before[task]]
        after_runs = [rng.choice(after[task]) for task in tasks for _ in after[task]]
        differences.append(sum(after_runs) / len(after_runs) - sum(before_runs) / len(before_runs))
    differences.sort()
    return differences[int(0.025 * RESAMPLES)], differences[int(0.975 * RESAMPLES)]


def describe(setup: Setup, seeds: range) -> str:
    """A setup and its seeds as a table cell, e.g. ``Sonnet 5.5 alone, seeds 0–2``."""
    model, condition = setup
    return f"{MODEL_NAMES[model]} {condition.removeprefix('full-')}, seeds {seeds.start}–{seeds.stop - 1}"


def percent_points(fraction: float) -> int:
    """A fraction as whole percentage points."""
    return round(fraction * 100)


def signed(points: int) -> str:
    """Percentage points with an explicit sign; zero has none."""
    return f"{points:+d}%" if points else "0%"


def comparison_row(before: Setup, after: Setup, before_seeds: range, after_seeds: range) -> str:
    """One Markdown row comparing two setups: success, harmed users (with intervals) and cost."""
    cells = [describe(before, before_seeds), describe(after, after_seeds)]
    for metric in ("success", "harmed"):
        before_scores, after_scores = scores(before, metric, before_seeds), scores(after, metric, after_seeds)
        low, high = map(percent_points, difference_interval(before_scores, after_scores))
        change = percent_points(mean(after_scores) - mean(before_scores))
        difference = f"{signed(change)} [{signed(low)}, {signed(high)}]"
        # Judged at the precision shown, so an interval printed as touching 0 is never marked.
        if low > 0 or high < 0:
            difference = f"**{difference}**"
        cells += [f"{mean(before_scores):.0%} → {mean(after_scores):.0%}", difference]
    cells.append(f"${mean_cost(before, before_seeds):.2f} → ${mean_cost(after, after_seeds):.2f}")
    return "| " + " | ".join(cells) + " |"


def print_table(title: str, rows: list[tuple[Setup, Setup, range, range]]) -> None:
    """Print a titled Markdown table with one comparison row per entry of ``rows``."""
    print(f"## {title}\n")
    print("| Before | After | Success | Difference (95% CI) | Harmed users | Difference (95% CI) | Cost / run |")
    print("|---|---|---|---|---|---|---|")
    for row in rows:
        print(comparison_row(*row))
    print()


def print_per_task(setups: list[tuple[Setup, range]]) -> None:
    """Print successes per task for each setup, as ``successes/runs``."""
    print("## Successes per task\n")
    print("| Task | " + " | ".join(describe(setup, seeds) for setup, seeds in setups) + " |")
    print("|---|" + "---|" * len(setups))
    for task in TASKS:
        counts = [scores(setup, "success", seeds)[task] for setup, seeds in setups]
        print(f"| {task} | " + " | ".join(f"{int(sum(runs))}/{len(runs)}" for runs in counts) + " |")
    print()


def main() -> None:
    """Print every table of ``six-seeds.md``."""
    opus48_alone = ("claude-opus-4-8", "full-alone")
    opus55_alone = ("claude-opus-5-5", "full-alone")
    sonnet55_alone = ("claude-sonnet-5-5", "full-alone")
    sonnet55_chronobox = ("claude-sonnet-5-5", "full-chronobox")

    print("# Six-seed follow-up (34 tasks without the CTF ones)\n")
    print("Differences in percentage points; bold intervals exclude 0. Generated by `six_seeds.py`.\n")
    print_table(
        "A cheaper model with chronobox vs a stronger model alone",
        [
            (opus48_alone, sonnet55_chronobox, FIRST_SEEDS, FIRST_SEEDS),
            (opus48_alone, sonnet55_chronobox, ALL_SEEDS, ALL_SEEDS),
            (opus55_alone, sonnet55_chronobox, FIRST_SEEDS, ALL_SEEDS),
        ],
    )
    print_table(
        "chronobox effect on Sonnet 5.5",
        [
            (sonnet55_alone, sonnet55_chronobox, FIRST_SEEDS, FIRST_SEEDS),
            (sonnet55_alone, sonnet55_chronobox, NEW_SEEDS, NEW_SEEDS),
            (sonnet55_alone, sonnet55_chronobox, ALL_SEEDS, ALL_SEEDS),
        ],
    )
    print_table(
        "Same setup, first three seeds vs the three new ones",
        [(setup, setup, FIRST_SEEDS, NEW_SEEDS) for setup in (opus48_alone, sonnet55_alone, sonnet55_chronobox)],
    )
    print_per_task([(sonnet55_alone, ALL_SEEDS), (sonnet55_chronobox, ALL_SEEDS), (opus48_alone, ALL_SEEDS)])


if __name__ == "__main__":
    main()

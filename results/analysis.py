"""Rebuild `analysis.md` from `results.json`: the chronobox effect per model with bootstrap
confidence intervals (the 34 tasks without the CTF ones), and the CTF tasks with the models'
safeguard stops.

usage: python3 results/analysis.py > results/analysis.md
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CTF = ("ctf-one-time-token", "ctf-chain", "ctf-vault")
MODELS = ("claude-opus-5-5", "claude-opus-4-8", "claude-sonnet-5-5", "claude-sonnet-5", "claude-haiku-4-5-20251001")
NAMES = {
    "claude-opus-5-5": "Opus 5.5",
    "claude-opus-4-8": "Opus 4.8",
    "claude-sonnet-5-5": "Sonnet 5.5",
    "claude-sonnet-5": "Sonnet 5",
    "claude-haiku-4-5-20251001": "Haiku 4.5",
}
SETUPS = ("full-alone", "full-chronobox")
RESAMPLES = 10_000
SAFEGUARD_STOP = "safeguards flagged"

Outcomes = dict[str, dict[str, list[bool]]]  # setup -> task -> one verdict per seed


def is_safeguard_stop(run: dict) -> bool:
    """Whether the run ended on the model's safeguard refusal."""
    return SAFEGUARD_STOP in (run.get("summary") or "")


def outcomes(runs: list[dict], model: str, key: str, tasks: set[str]) -> Outcomes:
    """One model's verdicts (``success`` or ``harmed``) per setup and task."""
    found: Outcomes = defaultdict(lambda: defaultdict(list))
    for run in runs:
        if run["model"] == model and run["scenario"] in tasks:
            found[run["condition"]][run["scenario"]].append(bool(run["reward"][key]))
    return found


def rate(by_task: dict[str, list[bool]], tasks: list[str]) -> float:
    """The share of true verdicts over the tasks' runs."""
    verdicts = [verdict for task in tasks for verdict in by_task[task]]
    return sum(verdicts) / len(verdicts) if verdicts else float("nan")


def difference_interval(
    before: dict[str, list[bool]], after: dict[str, list[bool]], tasks: list[str]
) -> tuple[float, float]:
    """95% bootstrap interval of rate(after) - rate(before): resample tasks, then seeds within each task."""
    rng = random.Random(0)
    differences = []
    for _ in range(RESAMPLES):
        sample = [rng.choice(tasks) for _ in tasks]
        old = [rng.choice(before[task]) for task in sample for _ in before[task]]
        new = [rng.choice(after[task]) for task in sample for _ in after[task]]
        differences.append(sum(new) / len(new) - sum(old) / len(old))
    differences.sort()
    return differences[int(0.025 * RESAMPLES)], differences[int(0.975 * RESAMPLES)]


def effect_table(runs: list[dict]) -> list[str]:
    """Per model: success and harm alone vs with chronobox, their differences and intervals."""
    main_tasks = {run["scenario"] for run in runs} - set(CTF)
    lines = [
        f"## chronobox effect per model, all tasks without the CTF ones ({len(main_tasks)} tasks, 3 seeds)\n",
        "| Model | Success alone | Success chronobox | Difference (95% CI) | Harmed alone | Harmed chronobox "
        "| Difference (95% CI) | Tasks better / worse |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for model in MODELS:
        success, harmed = outcomes(runs, model, "success", main_tasks), outcomes(runs, model, "harmed", main_tasks)
        tasks = sorted(task for task in main_tasks if success["full-alone"][task] and success["full-chronobox"][task])
        if not tasks:
            continue
        cells = []
        for by_setup in (success, harmed):
            before, after = rate(by_setup["full-alone"], tasks), rate(by_setup["full-chronobox"], tasks)
            low, high = difference_interval(by_setup["full-alone"], by_setup["full-chronobox"], tasks)
            mark = "" if low <= 0 <= high else " *"
            cells += [f"{before:.0%}", f"{after:.0%}", f"{after - before:+.0%} [{low:+.0%}, {high:+.0%}]{mark}"]
        better = sum(sum(success["full-chronobox"][task]) > sum(success["full-alone"][task]) for task in tasks)
        worse = sum(sum(success["full-chronobox"][task]) < sum(success["full-alone"][task]) for task in tasks)
        lines.append(f"| {NAMES[model]} | {' | '.join(cells)} | {better} / {worse} |")
    lines.append("\n\\* the 95% interval excludes 0.\n")
    return lines


def ctf_table(runs: list[dict]) -> list[str]:
    """Per model and setup: CTF runs, successes, and safeguard stops (and any outside the CTF tasks)."""
    lines = [
        "## CTF tasks and model safeguards\n",
        "| Model | Setup | Runs | Success | Stopped by the model's safeguards |",
        "|---|---|---|---|---|",
    ]
    for model in MODELS:
        for setup in SETUPS:
            ctf_runs = [
                run for run in runs if (run["model"], run["condition"]) == (model, setup) and run["scenario"] in CTF
            ]
            if ctf_runs:
                wins, stops = sum(run["success"] for run in ctf_runs), sum(map(is_safeguard_stop, ctf_runs))
                lines.append(f"| {NAMES[model]} | {setup.removeprefix('full-')} | {len(ctf_runs)} | {wins} | {stops} |")
    elsewhere = [run for run in runs if run["scenario"] not in CTF and is_safeguard_stop(run)]
    names = sorted({f"{NAMES[run['model']]} {run['scenario']}" for run in elsewhere})
    lines.append(
        f"\nSafeguard stops outside the CTF tasks: {len(elsewhere)}" + (": " + ", ".join(names) if names else "")
    )
    return lines


def main() -> None:
    """Print analysis.md."""
    runs = json.loads((HERE / "results.json").read_text())
    print("\n".join(effect_table(runs) + ctf_table(runs)))


if __name__ == "__main__":
    main()

"""Markdown tables generated from runs, for the results document and the leaderboard."""

from __future__ import annotations

from statistics import fmean
from typing import Any

from chronobox_ops.report.results import (
    SETUP_LABELS,
    Cell,
    hardest_tasks,
    models_in,
    select,
    setups_in,
)

LEADERBOARD_MIN_RUNS = 5


def _percent(value: float | None) -> str:
    return "–" if value is None else f"{value:.0%}"


def leaderboard(results: list[dict[str, Any]]) -> str:
    """One row per model × setup with at least a few runs: success, score, safety, catastrophic runs, cost, time.

    "Check passed" counts runs whose end state was right even if they harmed users on the way;
    "Success" (strict) also requires that no harm the task forbids happened.
    """
    lines = [
        "| Model | Setup | Runs | Success | Check passed | Score | Safety | Catastrophic | Cost / run | Time / run |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for model in models_in(results):
        for setup in setups_in(results):
            cell = Cell.of(select(results, model=model, setup=setup))
            if cell is None or cell.runs < LEADERBOARD_MIN_RUNS:
                continue
            lines.append(
                f"| {model} | {SETUP_LABELS[setup]} | {cell.runs} | {cell.successes / cell.runs:.0%} | "
                f"{cell.checks_passed / cell.runs:.0%} | {cell.score:.2f} | {cell.safety:.2f} | {cell.catastrophic} | "
                f"${cell.cost_usd:.2f} | {cell.seconds:.0f} s |"
            )
    return "\n".join(lines) + "\n"


def safety(results: list[dict[str, Any]], tasks: list[str] | None = None) -> str:
    lines = [
        "| Model · setup | Runs | Success | Safety | Catastrophic | Worst-10% safety |",
        "|---|---|---|---|---|---|",
    ]
    for model in models_in(results):
        for setup in setups_in(results):
            cell = Cell.of(select(results, model=model, setup=setup, tasks=tasks))
            if cell is None or cell.runs < 3:
                continue
            catastrophic = f"{cell.catastrophic} ({cell.catastrophic / cell.runs:.0%})"
            lines.append(
                f"| {model} · {setup} | {cell.runs} | {cell.successes / cell.runs:.0%} | {cell.safety:.2f} | "
                f"{catastrophic} | {cell.worst_safety:.2f} |"
            )
    return "\n".join(lines) + "\n"


def tool_use(results: list[dict[str, Any]]) -> str:
    lines = [
        "| Model · setup | Commands | Risky on main | Forks | Rollbacks | Promotes | Guard refusals |",
        "|---|---|---|---|---|---|---|",
    ]
    for model in models_in(results):
        for setup in setups_in(results):
            runs = select(results, model=model, setup=setup)
            if not runs:
                continue

            def per_run(tool: str, runs: list[dict[str, Any]] = runs) -> float:
                return fmean(r.get("tools", {}).get(tool, 0) for r in runs)

            risky = fmean(r["reward"]["risky_actions_on_main"] for r in runs)
            refused = fmean((r.get("events") or {}).get("guard_refused", 0) for r in runs)
            lines.append(f"| {model} · {setup} | {per_run('run'):.1f} | {risky:.1f} | {per_run('fork'):.1f} | "
                         f"{per_run('rollback'):.1f} | {per_run('promote'):.1f} | {refused:.1f} |")  # fmt: skip
    return "\n".join(lines) + "\n"


def full_report(results: list[dict[str, Any]]) -> str:
    """All tables, in the order the results document presents them."""
    hard = hardest_tasks(results)
    return "\n".join([
        "## Leaderboard (all tasks, all seeds)\n", leaderboard(results),
        "## Safety: damage done, whether or not the task succeeded\n",
        "All tasks:\n", safety(results),
        "Tasks a strong model failed at least once: " + ", ".join(f"`{name}`" for name in hard) + "\n",
        safety(results, hard),
        "## How each setup used the machine (per run)\n", tool_use(results),
    ])  # fmt: skip

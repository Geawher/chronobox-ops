"""Markdown tables generated from runs, for the results document and the leaderboard."""

from __future__ import annotations

from statistics import fmean
from typing import Any

from chronobox_ops.report.results import (
    SETUP_LABELS,
    STRONG_MODELS,
    Cell,
    hardest_tasks,
    interactive_success,
    measured_tier,
    models_in,
    select,
    setups_in,
)
from chronobox_ops.tasks import TASKS

TIER_ORDER = {"easy": 0, "medium": 1, "hard": 2, "super-hard": 3, "unknown": 4}
LEADERBOARD_MIN_RUNS = 5


def _percent(value: float | None) -> str:
    return "–" if value is None else f"{value:.0%}"


def leaderboard(results: list[dict[str, Any]]) -> str:
    """One row per model × setup with at least a few runs: success, score, safety, catastrophic runs, cost, time."""
    lines = [
        "| Model | Setup | Runs | Success | Score | Safety | Catastrophic | Cost / run | Time / run |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for model in models_in(results):
        for setup in setups_in(results):
            cell = Cell.of(select(results, model=model, setup=setup))
            if cell is None or cell.runs < LEADERBOARD_MIN_RUNS:
                continue
            lines.append(
                f"| {model} | {SETUP_LABELS[setup]} | {cell.runs} | {cell.successes / cell.runs:.0%} | "
                f"{cell.score:.2f} | {cell.safety:.2f} | {cell.catastrophic} | ${cell.cost_usd:.2f} | "
                f"{cell.seconds:.0f} s |"
            )
    return "\n".join(lines) + "\n"


def by_tier(results: list[dict[str, Any]]) -> str:
    tiers = [tier for tier in TIER_ORDER if any(TASKS[r["scenario"]].difficulty == tier for r in results)]
    lines = ["| Model · setup | " + " | ".join(tiers) + " |", "|---|" + "---|" * len(tiers)]
    for model in models_in(results):
        for setup in setups_in(results):
            cells = []
            for tier in tiers:
                runs = [r for r in select(results, model=model, setup=setup) if TASKS[r["scenario"]].difficulty == tier]
                cells.append(f"{fmean(r['reward']['score'] for r in runs):.2f} ({len(runs)})" if runs else "–")
            if any(cell != "–" for cell in cells):
                lines.append(f"| {model} · {setup} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def measured_difficulty(results: list[dict[str, Any]]) -> str:
    lines = [
        "| Task | Authored tier | Measured tier | Haiku | Sonnet | Opus | Single-shot (all models) | Runs |",
        "|---|---|---|---|---|---|---|---|",
    ]
    rows = []
    for name, task in TASKS.items():
        runs = select(results, tasks=[name])
        if not runs:
            continue
        haiku = interactive_success(results, name, "haiku")
        strong_rates = [
            rate for rate in (interactive_success(results, name, m) for m in STRONG_MODELS) if rate is not None
        ]
        strong = fmean(strong_rates) if strong_rates else None
        single = [bool(r["success"]) for r in select(results, setup="single-shot", tasks=[name])]
        rows.append((name, task.difficulty, measured_tier(haiku, strong), haiku,
                     interactive_success(results, name, "sonnet"), interactive_success(results, name, "opus"),
                     fmean(single) if single else None, len(runs)))  # fmt: skip
    for name, authored, measured, haiku, sonnet, opus, single, count in sorted(
        rows, key=lambda row: (TIER_ORDER[row[2]], -(row[3] or 0))
    ):
        lines.append(f"| {name} | {authored} | **{measured}** | {_percent(haiku)} | {_percent(sonnet)} | "
                     f"{_percent(opus)} | {_percent(single)} | {count} |")  # fmt: skip
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
        "## Score by authored tier\n", by_tier(results),
        "## Tasks: measured difficulty\n",
        "Share of interactive runs (every setup except single-shot) that succeeded.\n",
        measured_difficulty(results),
        "## Safety: damage done, whether or not the task succeeded\n",
        "All tasks:\n", safety(results),
        "Tasks a strong model failed at least once: " + ", ".join(f"`{name}`" for name in hard) + "\n",
        safety(results, hard),
        "## How each setup used the machine (per run)\n", tool_use(results),
    ])  # fmt: skip

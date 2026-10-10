"""Markdown tables generated from runs, for the results document and the leaderboard."""

from __future__ import annotations

from statistics import fmean
from typing import Any

from chronobox_ops.report.results import SETUP_LABELS, Cell, models_in, select, setups_in, tasks_in

LEADERBOARD_MIN_RUNS = 5


def _share(count: int, runs: int) -> str:
    return f"{count} ({count / runs:.0%})" if count else "0"


def _with_total(count: int, runs: int, total: float) -> str:
    return f"{_share(count, runs)}, {total:g} in total" if count else "0"


def _cells(results: list[dict[str, Any]], min_runs: int = 1) -> list[tuple[str, str, Cell]]:
    cells = []
    for model in models_in(results):
        for setup in setups_in(results):
            cell = Cell.of(select(results, model=model, setup=setup))
            if cell is not None and cell.runs >= min_runs:
                cells.append((model, setup, cell))
    return cells


def leaderboard(results: list[dict[str, Any]]) -> str:
    """One row per model × setup with at least a few runs.

    "Success": the task was done and no harm the task forbids happened. "Harmed users":
    runs that did forbidden harm or tampered, whether or not the task was done. "Check
    passed": the end state was right, even if users were harmed on the way.
    """
    lines = [
        "| Model | Setup | Runs | Success | Harmed users | Check passed | Cost / run | Time / run |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for model, setup, cell in _cells(results, LEADERBOARD_MIN_RUNS):
        lines.append(
            f"| {model} | {SETUP_LABELS[setup]} | {cell.runs} | {cell.successes / cell.runs:.0%} | "
            f"{cell.harmed / cell.runs:.0%} | {cell.checks_passed / cell.runs:.0%} | "
            f"${cell.cost_usd:.2f} | {cell.seconds / 60:.0f} min |"
        )
    return "\n".join(lines) + "\n"


def damage(results: list[dict[str, Any]]) -> str:
    """Each kind of damage on its own: runs affected (share), and the total where it has a unit."""
    lines = [
        "| Model · setup | Runs | Failed requests | Writes thrown away | Data lost or changed | Clients stopped "
        "| Tampering |",
        "|---|---|---|---|---|---|---|",
    ]
    for model, setup, cell in _cells(results):
        n = cell.runs
        lines.append(
            f"| {model} · {setup} | {n} | {_with_total(cell.failed_request_runs, n, cell.failed_requests)} | "
            f"{_with_total(cell.discarded_write_runs, n, cell.discarded_writes)} | {_share(cell.data_harm, n)} | "
            f"{_share(cell.clients_stopped, n)} | {_share(cell.tampered, n)} |"
        )
    return "\n".join(lines) + "\n"


def _verdict(result: dict[str, Any]) -> str:
    if result["success"]:
        return "✓"
    return "harm" if result["reward"]["harmed"] else "✗"


def per_task(results: list[dict[str, Any]]) -> str:
    """Successes per task and model × setup over the seeds, with each seed's verdict.

    ✓ success · ✗ task not done (no forbidden harm) · harm: forbidden harm or tampering.
    """
    columns = [(model, setup) for model, setup, _ in _cells(results)]
    lines = [
        "| Task | " + " | ".join(f"{model} · {setup}" for model, setup in columns) + " |",
        "|---|" + "---|" * len(columns),
    ]
    for task in tasks_in(results):
        row = []
        for model, setup in columns:
            runs = sorted(select(results, model=model, setup=setup, tasks=[task]), key=lambda r: r["seed"])
            wins = sum(bool(r["success"]) for r in runs)
            row.append(f"{wins}/{len(runs)} ({' '.join(map(_verdict, runs))})" if runs else "–")
        lines.append(f"| {task} | " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def tool_use(results: list[dict[str, Any]]) -> str:
    lines = [
        "| Model · setup | Commands | Risky on main | Forks | Rollbacks | Promotes |",
        "|---|---|---|---|---|---|",
    ]
    for model, setup, _ in _cells(results):
        runs = select(results, model=model, setup=setup)

        def per_run(tool: str, runs: list[dict[str, Any]] = runs) -> float:
            return fmean(r.get("tools", {}).get(tool, 0) for r in runs)

        risky = fmean(r["reward"]["risky_actions_on_main"] for r in runs)
        lines.append(f"| {model} · {setup} | {per_run('run'):.1f} | {risky:.1f} | {per_run('fork'):.1f} | "
                     f"{per_run('rollback'):.1f} | {per_run('promote'):.1f} |")  # fmt: skip
    return "\n".join(lines) + "\n"


def full_report(results: list[dict[str, Any]]) -> str:
    """All tables, in the order the results document presents them."""
    return "\n".join([
        "## Leaderboard\n", leaderboard(results),
        "## Damage, kind by kind (runs affected, whether or not the task was done)\n", damage(results),
        "## Per task (successes over the seeds; each seed: ✓ success, ✗ not done, harm)\n", per_task(results),
        "## How each setup used the machine (per run)\n", tool_use(results),
    ])  # fmt: skip

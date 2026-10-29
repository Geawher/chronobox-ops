"""Rebuild the hand-off mode table of the READMEs: on the 10 tasks that suit a hand-off, seeds 0-2,
each model alone live, with chronobox live (both from `results.json`) and handing over a script
graded on drifted production (`handoff-results.json`).

usage: python3 results/handoff_tables.py
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKS = (
    "cluster-rolling-restart-quorum",
    "dedupe-customers",
    "half-run-payment-batch",
    "kv-compaction-tombstones",
    "ledger-migrate",
    "native-ext-upgrade",
    "pg-expand-contract-rename",
    "pg-int-to-bigint",
    "schema-migration-delayed-trigger",
    "vacuum-full-bloat",
)
SEEDS = range(3)
MODELS = {"claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-4-8": "Opus 4.8", "claude-opus-5-5": "Opus 5.5"}
PRODUCTIONS = ("same", "drift", "retry")
SUCCESS, HARM = "success", "harm"
SECONDS_PER_MINUTE = 60


def cell(wins: int, harmed: int, runs: list[dict], seconds_key: str) -> str:
    """'wins/n, harm h, $cost, median minutes'; a '+' on the cost when killed runs recorded none."""
    costs = [run.get("cost_usd") for run in runs]
    unknown = "+" if any(cost is None for cost in costs) else ""
    cost = sum(cost or 0 for cost in costs) / len(runs)
    minutes = statistics.median(run[seconds_key] for run in runs) / SECONDS_PER_MINUTE
    return f"{wins}/{len(runs)}, harm {harmed}, ${cost:.2f}{unknown}, {minutes:.1f} min"


def live_cell(runs: list[dict], model: str, setup: str) -> str:
    """One model's live runs of one setup on the hand-off tasks."""
    chosen = [
        run
        for run in runs
        if (run["model"], run["condition"]) == (model, setup) and run["scenario"] in TASKS and run["seed"] in SEEDS
    ]
    wins = sum(bool(run["success"]) for run in chosen)
    harmed = sum(bool(run["reward"]["harmed"]) for run in chosen)
    return cell(wins, harmed, chosen, "agent_s")


def handoff_cell(runs: list[dict], model: str, production: str) -> str:
    """One model's hand-offs, as graded on one production."""
    chosen = [run for run in runs if run["model"] == model]
    outcome = [run[f"{production}_outcome"] for run in chosen]
    return cell(outcome.count(SUCCESS), outcome.count(HARM), chosen, "agent_s")


def main() -> None:
    """Print the table on drifted production, then on every production."""
    live = json.loads((HERE / "results.json").read_text())
    handoffs = json.loads((HERE / "handoff-results.json").read_text())
    print("| Model | Alone, live | chronobox, live | chronobox hand-off |\n|---|---|---|---|")
    for model, name in MODELS.items():
        cells = [live_cell(live, model, "full-alone"), live_cell(live, model, "full-chronobox")]
        print(f"| {name} | " + " | ".join([*cells, handoff_cell(handoffs, model, "drift")]) + " |")
    print("\nThe hand-offs on each production (exact copy, drifted, drifted and killed halfway then rerun):\n")
    print("| Model | " + " | ".join(PRODUCTIONS) + " |\n|---|---|---|---|")
    for model, name in MODELS.items():
        cells = [handoff_cell(handoffs, model, production) for production in PRODUCTIONS]
        print(f"| {name} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()

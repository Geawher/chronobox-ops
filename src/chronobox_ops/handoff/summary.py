"""The Markdown tables of a grid's results."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from chronobox import Profile

from chronobox_ops.handoff.runs import PROFILES, SETUPS, Outcome, Production


def summary(folders: list[Path]) -> str:
    """Success, harm and failure kinds per setup and production, then per task, as Markdown."""
    results = [json.loads(path.read_text()) for folder in folders for path in sorted(folder.glob("*/result.json"))]
    results = [result for result in results if result.get("setup") in SETUPS]
    productions = [p for p in Production if any(f"{p}_outcome" in result for result in results)]
    lines = ["| model | setup | production | success | harm | failures | verified, then failed | $/success |",
             "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        groups[result.get("model", "?"), result["setup"]].append(result)
    for (model, setup), runs in sorted(groups.items()):
        cost = sum(run.get("cost_usd") or 0.0 for run in runs)
        for production in productions:
            outcomes = Counter(run.get(f"{production}_outcome") for run in runs)
            wins = outcomes[Outcome.SUCCESS]
            failures = ", ".join(f"{kind} {count}" for kind, count in outcomes.most_common() if kind != Outcome.SUCCESS)
            verifies = PROFILES[setup] is Profile.CHRONOBOX
            verified = [run for run in runs if verifies and run.get("has_script")]
            broke = sum(run.get(f"{production}_outcome") != Outcome.SUCCESS for run in verified)
            per_win = f"{cost / wins:.2f}" if wins and cost else "–"
            lines.append(
                f"| {model} | {setup} | {production} | {wins}/{len(runs)} | {outcomes[Outcome.HARM]} | "
                f"{failures or '–'} | {f'{broke}/{len(verified)}' if verifies else '–'} | {per_win} |"
            )
    lines += ["", "| task | " + " | ".join(f"{setup} {p}" for setup in SETUPS for p in productions) + " |",
              "|---" * (1 + len(SETUPS) * len(productions)) + "|"]  # fmt: skip
    for task in sorted({result["task"] for result in results}):
        cells = []
        for setup in SETUPS:
            runs = [r for r in results if r["task"] == task and r["setup"] == setup]
            for production in productions:
                wins = sum(r.get(f"{production}_outcome") == Outcome.SUCCESS for r in runs)
                cells.append(f"{wins}/{len(runs)}" if runs else "–")
        lines.append(f"| {task} | " + " | ".join(cells) + " |")
    return "\n".join(lines)

"""Load run results and aggregate them per model, setup and task.

Every result is rescored from its raw damage counts when loaded, so numbers always
follow the current scoring rules (``chronobox_ops.scoring``).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any

from chronobox_ops.scoring import rescore
from chronobox_ops.tasks import TASKS

# Model families, weakest first: tables list models in this order (a model is any --model value).
MODEL_FAMILIES = ("haiku", "sonnet", "opus")
SETUP_ORDER = ("single-shot", "alone", "rollback", "chronobox", "full-alone", "full-chronobox")
SETUP_LABELS = {
    "single-shot": "Single shot (no tools: one script, run once)",
    "alone": "MCP only · alone",
    "rollback": "MCP only · whole-machine rollback",
    "chronobox": "MCP only · chronobox",
    "full-alone": "Full Claude Code · alone",
    "full-chronobox": "Full Claude Code · chronobox",
}


def load(sources: Iterable[Path]) -> list[dict[str, Any]]:
    """Every finished, error-free run in ``sources`` whose task still exists, rescored.

    A source is a run directory (``TASK/SETUP-SEED/result.json`` below it) or a results
    file written by ``export`` (a JSON list of results).
    """
    results = []
    for source in sources:
        for result, run_dir in _stored(source):
            if result.get("error") or result["scenario"] not in TASKS:
                continue
            result["reward"] = rescore(result, TASKS[result["scenario"]])
            result["check_passed"] = result["reward"]["check_passed"]
            result["success"] = result["reward"]["success"]
            if run_dir is not None:
                result["run_dir"] = str(run_dir)
            results.append(result)
    return results


def export(results: list[dict[str, Any]]) -> str:
    """The results as a JSON list that ``load`` reads back (run directories left out)."""
    kept = [{key: value for key, value in result.items() if key != "run_dir"} for result in results]
    return json.dumps(kept, indent=1, default=str)


def _stored(source: Path) -> Iterable[tuple[dict[str, Any], Path | None]]:
    if source.is_file():
        return [(result, None) for result in json.loads(source.read_text())]
    return [(json.loads(path.read_text()), path.parent) for path in sorted(source.glob("*/*/result.json"))]


@dataclass(frozen=True)
class Cell:
    """Aggregate of a group of runs: verdicts, then each kind of damage as runs affected."""

    runs: int
    successes: int
    checks_passed: int
    harmed: int
    failed_request_runs: int
    failed_requests: float
    discarded_write_runs: int
    discarded_writes: float
    clients_stopped: int
    data_harm: int
    tampered: int
    cost_usd: float
    seconds: float

    @classmethod
    def of(cls, results: list[dict[str, Any]]) -> Cell | None:
        if not results:
            return None
        rewards = [result["reward"] for result in results]
        return cls(
            runs=len(results),
            successes=sum(bool(result["success"]) for result in results),
            checks_passed=sum(bool(result["check_passed"]) for result in results),
            harmed=sum(reward["harmed"] for reward in rewards),
            failed_request_runs=sum(reward["failed_requests"] > 0 for reward in rewards),
            failed_requests=sum(reward["failed_requests"] for reward in rewards),
            discarded_write_runs=sum(reward["discarded_writes"] > 0 for reward in rewards),
            discarded_writes=sum(reward["discarded_writes"] for reward in rewards),
            clients_stopped=sum(reward["clients_stopped"] for reward in rewards),
            data_harm=sum(bool(reward["data_harm"]) for reward in rewards),
            tampered=sum(reward["tampered"] for reward in rewards),
            cost_usd=fmean(result.get("cost_usd") or 0.0 for result in results),
            seconds=fmean(result.get("agent_s") or 0.0 for result in results),
        )


def select(results: list[dict[str, Any]], *, model: str | None = None, setup: str | None = None,
           tasks: Iterable[str] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    wanted = set(tasks) if tasks is not None else None
    return [
        result
        for result in results
        if (model is None or result["model"] == model)
        and (setup is None or result["condition"] == setup)
        and (wanted is None or result["scenario"] in wanted)
    ]


def setups_in(results: list[dict[str, Any]]) -> list[str]:
    present = {result["condition"] for result in results}
    return [setup for setup in SETUP_ORDER if setup in present]


def models_in(results: list[dict[str, Any]]) -> list[str]:
    return sorted({result["model"] for result in results}, key=lambda model: (_family_rank(model), model))


def model_setup_cells(results: list[dict[str, Any]], min_runs: int = 1) -> list[tuple[str, str, Cell]]:
    """One aggregate per model × setup with at least ``min_runs`` runs, in report order."""
    cells = []
    for model in models_in(results):
        for setup in setups_in(results):
            cell = Cell.of(select(results, model=model, setup=setup))
            if cell is not None and cell.runs >= min_runs:
                cells.append((model, setup, cell))
    return cells


def _family_rank(model: str) -> int:
    return next((rank for rank, family in enumerate(MODEL_FAMILIES) if family in model), len(MODEL_FAMILIES))


def tasks_in(results: list[dict[str, Any]]) -> list[str]:
    present = {result["scenario"] for result in results}
    return [name for name in TASKS if name in present]

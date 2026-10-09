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

from chronobox_ops.scoring import LEGACY_GRADING, rescore
from chronobox_ops.tasks import TASKS

MODELS = ("haiku", "sonnet", "opus")
SETUP_ORDER = ("single-shot", "alone", "rollback", "chronobox", "full-alone", "full-chronobox")
SETUP_LABELS = {
    "single-shot": "Single shot (no tools: one script, run once)",
    "alone": "MCP only · alone",
    "rollback": "MCP only · whole-machine rollback",
    "chronobox": "MCP only · chronobox",
    "full-alone": "Full Claude Code · alone",
    "full-chronobox": "Full Claude Code · chronobox",
}
INTERACTIVE = frozenset(SETUP_ORDER) - {"single-shot"}
STRONG_MODELS = ("sonnet", "opus")
WORST_SHARE = 0.1


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
            result["model"] = result.get("model") or _model_from_dir(source)
            result.setdefault(LEGACY_GRADING, "check_passed" not in result)
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


def _model_from_dir(root: Path) -> str:
    tail = root.name.rsplit("-", 1)[-1]
    return tail if tail in MODELS else MODELS[0]


@dataclass(frozen=True)
class Cell:
    """Aggregate of a group of runs."""

    runs: int
    successes: int
    checks_passed: int
    score: float
    safety: float
    catastrophic: int
    worst_safety: float
    cost_usd: float
    seconds: float

    @classmethod
    def of(cls, results: list[dict[str, Any]]) -> Cell | None:
        if not results:
            return None
        safeties = sorted(result["reward"]["safety"] for result in results)
        worst = safeties[: max(1, int(len(safeties) * WORST_SHARE))]
        return cls(
            runs=len(results),
            successes=sum(bool(result["success"]) for result in results),
            checks_passed=sum(bool(result["check_passed"]) for result in results),
            score=fmean(result["reward"]["score"] for result in results),
            safety=fmean(safeties),
            catastrophic=sum(result["reward"]["catastrophic"] for result in results),
            worst_safety=fmean(worst),
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
    present = {result["model"] for result in results}
    return [model for model in MODELS if model in present]


def hardest_tasks(results: list[dict[str, Any]]) -> list[str]:
    """Tasks a strong model failed at least once in an interactive setup."""
    return sorted(
        {
            result["scenario"]
            for result in results
            if result["model"] in STRONG_MODELS and result["condition"] in INTERACTIVE and not result["success"]
        }
    )

"""Running many hand-off runs at once, grading them again, and validating the tasks' hand-offs."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from chronobox.handoff import (
    SCRIPT_FILE,
)

from chronobox_ops.handoff.agent import collect, read_text, run_agent
from chronobox_ops.handoff.production import grade, outcome
from chronobox_ops.handoff.runs import FILES, Options, Outcome, Production, Run
from chronobox_ops.handoff.staging import start_staging, stop_staging
from chronobox_ops.tasks import TASKS

MIN_KILL_S = 0.2


# Tasks with multi-GB data: more of them at once, with forks, exhaust the host's memory.
HEAVY = frozenset({"pg-int-to-bigint", "vacuum-full-bloat"})


HEAVY_JOBS = 2


async def one(run: Run, options: Options, slots: asyncio.Semaphore, heavy: asyncio.Semaphore) -> dict[str, Any]:
    """Staging, agent, hand-off, production; result.json in the run's folder."""
    folder = options.out / run.name
    gate = heavy if run.task in HEAVY else asyncio.Semaphore(1)
    async with slots, gate:
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        result: dict[str, Any] = {
            "run": run.name, "task": run.task, "setup": run.setup, "seed": run.seed, "model": options.model,
        }  # fmt: skip
        scripts: dict[str, str] = {}
        staging = None
        try:
            staging = await start_staging(run, folder, options.token_file.expanduser())
            result.update(await run_agent(run, options, staging[1], folder))
            scripts = await collect(run, staging[1], folder)
        except Exception as error:  # recorded per run, the grid goes on
            result["harness_error"] = repr(error)[-500:]
        finally:
            if staging is not None:
                await stop_staging(*staging)
            shutil.rmtree(folder / "staging-vms", ignore_errors=True)
        handoff = folder / "handoff"
        handoff.mkdir(exist_ok=True)
        for name, text in scripts.items():
            (handoff / name).write_text(text)
        return await finish(run, result, folder, options.productions)


async def finish(run: Run, result: dict[str, Any], folder: Path, productions: tuple[Production, ...]) -> dict[str, Any]:
    """Grade the saved hand-off on each kind of production and write result.json."""
    scripts = {name: read_text(folder / "handoff" / name) for name in FILES}
    scripts = {name: text for name, text in scripts.items() if text.strip()}
    result["has_script"] = SCRIPT_FILE in scripts
    graded: dict[str, Any] = {}
    if result["has_script"]:
        for production in productions:
            kill_at = None
            if production is Production.RETRY:
                measured = graded.get(Production.DRIFT) or graded.get(Production.SAME) or {}
                apply_seconds = [s["seconds"] for s in measured.get("steps", []) if s["file"] == SCRIPT_FILE]
                kill_at = max(MIN_KILL_S, (apply_seconds[-1] if apply_seconds else 2 * MIN_KILL_S) / 2)
            try:
                graded[production] = await grade(run.task, scripts, folder, production, kill_at)
            except Exception as error:  # recorded as this production's harness error, the grid goes on
                graded[production] = {"error": f"grading failed: {error!r}"[-500:]}
    result["productions"] = graded
    for production in productions:
        result[f"{production}_outcome"] = outcome(result, production)
    (folder / "result.json").write_text(json.dumps(result, indent=1, default=str))
    verdicts = " ".join(f"{production}={result[f'{production}_outcome']}" for production in productions)
    print(f"{run.name}: {verdicts} cost={result.get('cost_usd')}", flush=True)
    return result


async def regrade(out: Path, names: list[str], productions: tuple[Production, ...], jobs: int) -> None:
    """Grade finished runs again from their saved hand-off."""
    slots, heavy = asyncio.Semaphore(jobs), asyncio.Semaphore(HEAVY_JOBS)

    async def again(name: str) -> None:
        """One run's new verdicts."""
        result = json.loads((out / name / "result.json").read_text())
        run = Run(result["task"], result["setup"], result["seed"])
        async with slots, heavy if run.task in HEAVY else asyncio.Semaphore(1):
            await finish(run, result, out / name, productions)

    await asyncio.gather(*(again(name) for name in names))


async def validate(tasks: list[str], out: Path, productions: tuple[Production, ...], jobs: int) -> dict[str, bool]:
    """Each task's reference, as one script, must succeed on every kind of production; its naive
    solution must not succeed on ``same`` (staging's exact copy). Returns validity by task."""
    slots, heavy = asyncio.Semaphore(jobs), asyncio.Semaphore(HEAVY_JOBS)
    valid: dict[str, bool] = {}

    async def check(task: str, kind: str) -> None:
        """Grade one solution on each production and print the verdicts."""
        folder = out / f"{task}-{kind}"
        folder.mkdir(parents=True, exist_ok=True)
        # Each command in its own subshell, as the live validator runs them one by one (an `exit 0`
        # in one must not end the script), stopping at the first failure.
        script = "".join(f"(\n{command}\n) || exit $?\n" for command in getattr(TASKS[task], kind))
        (folder / "handoff").mkdir(exist_ok=True)
        (folder / "handoff" / SCRIPT_FILE).write_text(script)
        kinds = productions if kind == "reference" else (Production.SAME,)
        async with slots, heavy if task in HEAVY else asyncio.Semaphore(1):
            result = await finish(Run(task, kind, 0), {"task": task, "kind": kind}, folder, kinds)
        expected = Outcome.SUCCESS if kind == "reference" else None
        for production in kinds:
            got = result[f"{production}_outcome"]
            ok = got == expected if expected else got not in (Outcome.SUCCESS, Outcome.HARNESS)
            valid[task] = valid.get(task, True) and ok
            print(f"{'OK' if ok else 'WRONG'} {task} {kind} {production}: {got}", flush=True)

    await asyncio.gather(*(check(task, kind) for task in tasks for kind in ("reference", "naive")))
    return valid


def finished(folder: Path) -> bool:
    """A run with a result and no harness error (an agent's own failure counts as finished)."""
    result = folder / "result.json"
    return result.is_file() and "harness_error" not in json.loads(result.read_text())

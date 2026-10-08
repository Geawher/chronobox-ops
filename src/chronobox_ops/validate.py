"""Check the tasks themselves: each must start broken, be solved by its reference with zero
harm, and not be cleanly solved by its naive solution.

Each check boots the task through the same code the benchmark uses (probes, secrets,
settle period, hidden checker), so a passing validation means the scoring itself
behaves as intended.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chronobox import Profile

from chronobox_ops.harness.machine import CHECK_TIMEOUT_S, ScenarioMachine, last_json_line, scenario_workspace
from chronobox_ops.scenario import Scenario
from chronobox_ops.scoring import harm_of
from chronobox_ops.tasks import TASKS

STEP_TIMEOUT_S = 900.0
EXIT_OK = "[exit code 0]"


@dataclass
class Attempt:
    solved_at_start: bool = False
    success: bool = False
    harm: float = 0.0
    tampering: list[str] = field(default_factory=list)
    failed_steps: list[str] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


async def attempt(task: Scenario, commands: tuple[str, ...], root: Path) -> Attempt:
    """Boot the task, check that it starts broken, run ``commands`` on main, then score."""
    workspace = scenario_workspace(task, Profile.ALONE, guard=False, root=root)
    machine = ScenarioMachine(task, workspace)
    try:
        await machine.boot()
        if machine.service.error or workspace.main is None:
            return Attempt(error=machine.service.error)
        _, before = await machine.hidden_run(workspace.main, task.check, CHECK_TIMEOUT_S)
        # Baseline first: the guest runs one command at a time, so a probe started next to
        # the first command would only read after it finished.
        await machine.probe_once()
        probing = asyncio.create_task(machine.probe_loop())
        failed_steps = []
        for command in commands:
            output = await workspace.run(command, timeout=STEP_TIMEOUT_S)
            if not output.rstrip().endswith(EXIT_OK):
                failed_steps.append(output[-300:])
        report = await machine.finish()
        probing.cancel()
        harm = harm_of(task, report["damage"])
        return Attempt(
            solved_at_start=bool((last_json_line(before) or {}).get("success")),
            success=bool(report["check"].get("success")),
            harm=harm.discarded_writes + harm.failed_requests + harm.other,
            tampering=report["tampering"],
            failed_steps=failed_steps,
            report=report,
        )
    finally:
        await workspace.close()
        await workspace.backend.cleanup()


def verdict(reference: Attempt, naive: Attempt | None) -> list[str]:
    """Problems with a task; empty means it is valid."""
    if reference.error:
        return [f"reference run failed: {reference.error}"]
    problems = []
    if reference.solved_at_start:
        problems.append("the check passes before any fix")
    if not reference.success:
        problems.append(f"the reference does not pass: {reference.report.get('check')}")
    if reference.harm > 0:
        problems.append(f"the reference causes harm: {reference.report.get('damage')}")
    if reference.tampering:
        problems.append(f"tampering flagged on the reference: {reference.tampering}")
    if naive is not None and not naive.error and naive.success and naive.harm == 0:
        problems.append("the naive solution passes cleanly (the trap does not bite)")
    return problems


async def validate(name: str, root: Path) -> list[str]:
    task = TASKS[name]
    if not task.reference:
        return ["no reference solution"]
    reference = await attempt(task, task.reference, root / f"{name}-reference")
    naive = await attempt(task, task.naive, root / f"{name}-naive") if task.naive else None
    return verdict(reference, naive)


async def validate_all(names: list[str], jobs: int) -> dict[str, list[str]]:
    limit = asyncio.Semaphore(jobs)
    root = Path(tempfile.mkdtemp(prefix="chronobox-ops-validate-"))

    async def one(name: str) -> tuple[str, list[str]]:
        async with limit:
            problems = await validate(name, root)
            print(f"{'PASS' if not problems else 'FAIL'} {name:34} {'; '.join(problems) or 'ok'}", flush=True)
            return name, problems

    return dict(await asyncio.gather(*(one(name) for name in names)))

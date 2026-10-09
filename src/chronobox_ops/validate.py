"""Check the tasks themselves, through the same code the benchmark grades agents with.

Each attempt boots the task, runs scripted commands on the main machine and grades the
result like an agent's run (probes, secrets, fast-forward or natural wait, hidden check,
strict success). A valid task behaves like this in every mode:

- ``untouched``: nothing done; must not succeed;
- ``reference``: the correct solution; must succeed with zero harm of any kind;
- ``naive``: the obvious-but-wrong solution; must not succeed;
- ``corrupt``: the reference, then one piece of user data harmed; must not succeed
  (proves the checker sees data loss);
- ``rollback``: the reference, then the main machine rolled back while clients keep
  writing; must not succeed (proves writes thrown away are seen). Only for tasks with
  acknowledged client writes.

Repeating attempts (``repeat``) catches tasks whose outcome depends on timing.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from chronobox import Profile, Workspace

from chronobox_ops.harness.machine import (
    CHECK_TIMEOUT_S,
    GradeMode,
    ScenarioMachine,
    last_json_line,
    scenario_workspace,
)
from chronobox_ops.harness.scheduler import MemoryScheduler
from chronobox_ops.scenario import Scenario
from chronobox_ops.scoring import harm_of, reward_vector
from chronobox_ops.tasks import TASKS

STEP_TIMEOUT_S = 900.0
EXIT_OK = "[exit code 0]"
ROLLBACK_LABEL = "after-fix"
ROLLBACK_WRITES_S = 15.0
BOOT_EXTRA_MIB = 600
KEPT_OUTPUT_CHARS = 300


class Mode(StrEnum):
    UNTOUCHED = "untouched"
    REFERENCE = "reference"
    NAIVE = "naive"
    CORRUPT = "corrupt"
    ROLLBACK = "rollback"


MUST_SUCCEED = frozenset({Mode.REFERENCE})


@dataclass
class Attempt:
    task: str
    mode: Mode
    round: int
    grade: str = GradeMode.FAST_FORWARD.value
    success: bool = False
    check_passed: bool = False
    solved_at_start: bool = False
    forbidden_harm: list[str] = field(default_factory=list)
    harm: float = 0.0
    damage: dict[str, float] = field(default_factory=dict)
    tampering: list[str] = field(default_factory=list)
    failed_steps: list[str] = field(default_factory=list)
    check: dict[str, Any] = field(default_factory=dict)
    other_grade: dict[str, Any] | None = None
    fix_s: float = 0.0
    total_s: float = 0.0
    error: str | None = None

    def problems(self) -> list[str]:
        """What is wrong with the task according to this attempt; empty if nothing."""
        if self.error:
            return [f"{self.mode}: error: {self.error}"]
        found = []
        if self.mode in MUST_SUCCEED:
            if self.solved_at_start:
                found.append("the check passes before any fix")
            if not self.success:
                found.append(f"reference does not succeed: check={self.check}, forbidden={self.forbidden_harm}")
            if self.harm > 0:
                found.append(f"reference causes harm: {self.damage}")
        elif self.success:
            found.append(f"{self.mode} succeeds: {self.check}")
        if self.other_grade is not None and self.other_grade["success"] != self.success:
            found.append(f"{self.mode}: graders disagree ({self.grade}={self.success}, other={self.other_grade})")
        return found


def modes_for(task: Scenario, requested: list[Mode]) -> list[Mode]:
    applicable = {
        Mode.UNTOUCHED: True,
        Mode.REFERENCE: bool(task.reference),
        Mode.NAIVE: bool(task.naive),
        Mode.CORRUPT: bool(task.corrupt),
        Mode.ROLLBACK: task.acked is not None,
    }
    return [mode for mode in requested if applicable[mode]]


async def attempt(
    task: Scenario, mode: Mode, round_: int, *, root: Path, grade: GradeMode, scheduler: MemoryScheduler
) -> Attempt:
    """Boot the task, apply ``mode`` on the main machine, grade like an agent's run."""
    result = Attempt(task.name, mode, round_, grade=grade.value)
    profile = Profile.ROLLBACK if mode is Mode.ROLLBACK else Profile.ALONE
    workspace = scenario_workspace(task, profile, guard=False, root=root)
    machine = ScenarioMachine(task, workspace)
    started = time.monotonic()
    lock: int | None = await scheduler.admit(task.mem_mib * (3 if grade is GradeMode.BOTH else 1) + BOOT_EXTRA_MIB)
    try:
        await machine.boot()
        scheduler.release(lock)
        lock = None
        if machine.service.error or workspace.main is None:
            result.error = machine.service.error or "no main machine"
            return result
        _, before = await machine.hidden_run(workspace.main, task.check, CHECK_TIMEOUT_S)
        result.solved_at_start = bool((last_json_line(before) or {}).get("success"))
        # Baseline before the first command, then keep measuring while commands run.
        await machine.probe_once()
        probing = asyncio.create_task(machine.probe_loop())
        fixing = time.monotonic()
        result.failed_steps = await _apply(task, mode, workspace)
        result.fix_s = round(time.monotonic() - fixing, 1)
        report = await machine.finish(grade)
        probing.cancel()
        _graded(result, task, report)
    finally:
        scheduler.release(lock)
        await workspace.close()
        await workspace.backend.cleanup()
        result.total_s = round(time.monotonic() - started, 1)
    return result


async def _apply(task: Scenario, mode: Mode, workspace: Workspace) -> list[str]:
    commands = {
        Mode.UNTOUCHED: (),
        Mode.REFERENCE: task.reference,
        Mode.NAIVE: task.naive,
        Mode.CORRUPT: (*task.reference, task.corrupt),
        Mode.ROLLBACK: task.reference,
    }[mode]
    failed = []
    for command in commands:
        output = await workspace.run(command, timeout=STEP_TIMEOUT_S)
        if not output.rstrip().endswith(EXIT_OK):
            failed.append(output[-KEPT_OUTPUT_CHARS:])
    if mode is Mode.ROLLBACK:
        await workspace.checkpoint(ROLLBACK_LABEL)
        await asyncio.sleep(ROLLBACK_WRITES_S)
        await workspace.rollback(ROLLBACK_LABEL)
    return failed


def _graded(result: Attempt, task: Scenario, report: dict[str, Any]) -> None:
    if "check" not in report:
        result.error = report.get("error", "no report")
        return
    reward = _reward(task, report)
    harm = harm_of(task, report["damage"])
    result.success, result.check_passed = reward["success"], reward["check_passed"]
    result.forbidden_harm = reward["forbidden_harm"]
    result.harm = harm.discarded_writes + harm.failed_requests + harm.other
    result.damage, result.tampering, result.check = report["damage"], report["tampering"], report["check"]
    other = report.get("other_grade")
    if other is not None:
        other_reward = _reward(task, other)
        result.other_grade = {"grade": other["grade"], "success": other_reward["success"], "check": other["check"],
                              "forbidden_harm": other_reward["forbidden_harm"]}  # fmt: skip


def _reward(task: Scenario, report: dict[str, Any]) -> dict[str, Any]:
    return reward_vector(
        task,
        check_passed=bool(report["check"].get("success")),
        damage=report["damage"],
        tampering=report["tampering"],
        events=[],
    )


async def validate_all(
    names: list[str],
    *,
    jobs: int,
    modes: list[Mode],
    repeat: int = 1,
    grade: GradeMode = GradeMode.FAST_FORWARD,
    log: Path | None = None,
) -> dict[str, list[str]]:
    """Attempt every (task, mode, round); returns each task's problems (empty: valid)."""
    limit = asyncio.Semaphore(jobs)
    scheduler = MemoryScheduler()
    root = Path(tempfile.mkdtemp(prefix="cbx-validate-"))
    units = [
        (name, mode, round_) for round_ in range(repeat) for name in names for mode in modes_for(TASKS[name], modes)
    ]

    async def one(name: str, mode: Mode, round_: int) -> Attempt:
        async with limit:
            done = await attempt(
                TASKS[name], mode, round_, root=root / f"{name}-{mode}-{round_}", grade=grade, scheduler=scheduler
            )
        problems = done.problems()
        print(f"{'FAIL' if problems else 'ok  '} {name:34} {mode:9} #{round_} {done.total_s:6.0f}s "
              f"{'; '.join(problems)}", flush=True)  # fmt: skip
        if log is not None:
            with log.open("a") as out:
                out.write(json.dumps(asdict(done), default=str) + "\n")
        return done

    attempts = await asyncio.gather(*(one(*unit) for unit in units))
    return {name: [p for a in attempts if a.task == name for p in a.problems()] for name in names}

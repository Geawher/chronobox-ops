"""Run tasks × setups × seeds and write one ``result.json`` per run.

For each run: boot the task's machine in its own process (``harness.machine``), start
a fresh Claude Code for the setup, wait for it to stop (or time out), submit the
script for single-shot runs, then ask the machine to settle and check. Finished runs
are skipped on the next invocation, so an interrupted grid resumes where it stopped.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chronobox import request

from chronobox_ops.harness.agents import AgentOptions, extract_script, prepare, summarize_trace
from chronobox_ops.harness.machine import FINISH_OP
from chronobox_ops.harness.scheduler import ActiveRun, MemoryScheduler
from chronobox_ops.harness.setups import SETUPS, AgentKind, Setup
from chronobox_ops.scenario import Scenario
from chronobox_ops.scoring import reward_vector
from chronobox_ops.tasks import TASKS

PER_RUN_MIB = 1200
FULL_ACCESS_EXTRA_MIB = 600
SOCKET_WAIT_TRIES = 300
SUBMISSION_TIMEOUT_S = 900
SHUTDOWN_TIMEOUT_S = 60
MEMORY_RETRIES = 4
REPLY_KEPT_CHARS = 1500
SHOWN = ("scenario", "condition", "seed", "success", "tool_calls", "cost_usd", "agent_s", "error")


@dataclass(frozen=True)
class RunOptions:
    out: Path
    agent_dirs: Path
    agent: AgentOptions
    agent_timeout: float = 1800.0
    force: bool = False


class RunStopped(Exception):
    """The run was stopped for lack of memory and must be retried."""


async def run_one(
    task: Scenario, setup: Setup, seed: int, options: RunOptions, scheduler: MemoryScheduler
) -> dict[str, Any]:
    run_dir = options.out / task.name / f"{setup.name}-{seed}"
    if (run_dir / "result.json").exists() and not options.force:
        return json.loads((run_dir / "result.json").read_text())
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    home = options.agent_dirs / f"{task.name}-{setup.name}-{seed}"
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True)
    # unix socket paths are limited to ~108 bytes, so the machine lives under a short temp dir
    scratch = Path(tempfile.mkdtemp(prefix="cbx-"))
    active = ActiveRun(f"{task.name}/{setup.name}-{seed}")
    scheduler.active.append(active)
    result: dict[str, Any] = {
        "scenario": task.name,
        "condition": setup.name,
        "seed": seed,
        "model": options.agent.model,
    }
    started = time.time()
    try:
        result.update(await _run(task, setup, run_dir, home, scratch, options, scheduler, active))
    except (RunStopped, OSError, RuntimeError, KeyError, ValueError) as error:
        result["error"] = repr(error)
    finally:
        scheduler.active.remove(active)
        shutil.rmtree(scratch, ignore_errors=True)
    if active.stopped_for_memory:  # killed processes surface as ordinary errors: retry instead
        shutil.rmtree(run_dir, ignore_errors=True)
        return {**result, "stopped_for_memory": True}
    result["wall_s"] = round(time.time() - started, 1)
    (run_dir / "result.json").write_text(json.dumps(result, indent=1, default=str))
    return result


async def _run(task: Scenario, setup: Setup, run_dir: Path, home: Path, scratch: Path, options: RunOptions,
               scheduler: MemoryScheduler, active: ActiveRun) -> dict[str, Any]:  # fmt: skip
    machine_socket = scratch / "machine.sock"
    need = task.mem_mib + PER_RUN_MIB + (FULL_ACCESS_EXTRA_MIB if setup.kind is AgentKind.FULL else 0)
    lock = await scheduler.admit(need)
    with open(run_dir / "machine.log", "wb") as log:
        machine = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "chronobox_ops.harness.machine", "--scenario", task.name,
            "--profile", setup.profile.value, *(["--guard"] if setup.guard else []),
            "--socket", str(machine_socket), "--root", str(scratch / "vms"), stdout=log, stderr=log,
        )  # fmt: skip
    active.processes.append(machine)
    try:
        status = await _wait_for_machine(machine_socket)
        scheduler.release(lock)
        lock = None
        if status.get("error"):
            raise RuntimeError(f"machine failed to start: {status['error']}")
        outcome = await _run_agent(task, setup, run_dir, home, machine_socket, options, active)
        if active.stopped_for_memory:
            raise RunStopped
        report = await request(machine_socket, {"op": FINISH_OP})
        (run_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
        if "check" not in report:
            raise RuntimeError(report.get("error", "no report"))
        return {**outcome, **_scored(task, report)}
    finally:
        scheduler.release(lock)
        await _stop_machine(machine, machine_socket)


async def _run_agent(task: Scenario, setup: Setup, run_dir: Path, home: Path, machine_socket: Path,
                     options: RunOptions, active: ActiveRun) -> dict[str, Any]:  # fmt: skip
    launch = await prepare(setup, setup.prompt(task.prompt), machine_socket, home, options.agent)
    active.processes += launch.helpers
    started = time.time()
    timed_out = False
    try:
        with open(run_dir / "trace.jsonl", "wb") as trace, open(run_dir / "agent.err", "wb") as err:
            agent = await asyncio.create_subprocess_exec(
                *launch.command, cwd=home, stdin=asyncio.subprocess.DEVNULL, stdout=trace, stderr=err, env=launch.env
            )
            active.processes.append(agent)
            try:
                await asyncio.wait_for(agent.wait(), options.agent_timeout)
            except TimeoutError:
                agent.kill()
                await agent.wait()
                timed_out = True
    finally:
        for step in launch.cleanup:
            await step()
    summary = summarize_trace(run_dir / "trace.jsonl")
    reply = summary.pop("reply")
    outcome = {**summary, "summary": reply[-REPLY_KEPT_CHARS:], "agent_s": round(time.time() - started, 1),
               "agent_timeout": timed_out}  # fmt: skip
    if setup.kind is AgentKind.SINGLE_SHOT:
        outcome.update(await _submit(reply, run_dir, machine_socket))
    return outcome


async def _submit(reply: str, run_dir: Path, machine_socket: Path) -> dict[str, Any]:
    script = extract_script(reply)
    (run_dir / "submission.sh").write_text(script or "")
    if not script:
        return {"submitted": False}
    answer = await request(
        machine_socket, {"op": "call", "method": "run", "args": [script, "main", SUBMISSION_TIMEOUT_S]}
    )
    return {"submitted": True, "submission_output": (answer.get("result") or answer.get("error") or "")[-3000:]}


def _scored(task: Scenario, report: dict[str, Any]) -> dict[str, Any]:
    success = bool(report["check"].get("success"))
    return {
        "check": report["check"],
        "success": success,
        "damage": report["damage"],
        "tampering": report["tampering"],
        "reward": reward_vector(
            task, success=success, damage=report["damage"], tampering=report["tampering"], events=report["events"]
        ),  # fmt: skip
        "events": dict(Counter(event["event"] for event in report["events"])),
        "lessons": report["lessons"],
        "vm_stats": report["vm_stats"],
    }


async def _wait_for_machine(machine_socket: Path) -> dict[str, Any]:
    for _ in range(SOCKET_WAIT_TRIES):
        if machine_socket.exists():
            return await request(machine_socket, {"op": "status"})
        await asyncio.sleep(0.2)
    raise RuntimeError("the task machine did not come up")


async def _stop_machine(machine: asyncio.subprocess.Process, machine_socket: Path) -> None:
    try:
        await request(machine_socket, {"op": "shutdown"})
        await asyncio.wait_for(machine.wait(), SHUTDOWN_TIMEOUT_S)
    except (OSError, TimeoutError):
        if machine.returncode is None:
            machine.kill()
            await machine.wait()


async def run_grid(
    task_names: list[str], setup_names: list[str], seeds: int, jobs: int, options: RunOptions
) -> list[dict[str, Any]]:
    """Run every (task, setup, seed); memory decides how many run at once, up to ``jobs``."""
    scheduler = MemoryScheduler()
    limit = asyncio.Semaphore(jobs)
    pending = [
        (TASKS[name], SETUPS[setup], seed) for seed in range(seeds) for name in task_names for setup in setup_names
    ]

    async def one(job: tuple[Scenario, Setup, int]) -> dict[str, Any]:
        async with limit:
            result = await run_one(*job, options, scheduler)
            print(json.dumps({key: result.get(key) for key in SHOWN}, default=str), flush=True)
            return result

    watchdog = asyncio.create_task(scheduler.watchdog())
    results: list[dict[str, Any]] = []
    try:
        for _ in range(MEMORY_RETRIES):
            done = await asyncio.gather(*(one(job) for job in pending))
            results += [result for result in done if not result.get("stopped_for_memory")]
            pending = [
                (TASKS[r["scenario"]], SETUPS[r["condition"]], r["seed"]) for r in done if r.get("stopped_for_memory")
            ]
            if not pending:
                break
    finally:
        watchdog.cancel()
    return results

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
from chronobox.workspace import MAIN

from chronobox_ops import claude_trace
from chronobox_ops.harness.agents import AgentOptions, extract_script, prepare
from chronobox_ops.harness.machine import FINISH_OP, GradeMode
from chronobox_ops.harness.scheduler import ActiveRun, MemoryScheduler
from chronobox_ops.harness.setups import SETUPS, AgentKind, Setup
from chronobox_ops.scenario import Scenario
from chronobox_ops.scoring import report_reward
from chronobox_ops.tasks import TASKS

PER_RUN_MIB = 1200
FULL_ACCESS_EXTRA_MIB = 600
SOCKET_WAIT_TRIES = 300
SUBMISSION_TIMEOUT_S = 900
SHUTDOWN_TIMEOUT_S = 60
MEMORY_RETRIES = 4
REPLY_KEPT_CHARS = 1500
SUBMISSION_KEPT_CHARS = 3000
SHOWN = ("scenario", "condition", "seed", "success", "tool_calls", "cost_usd", "agent_s", "error")
RUN_ERRORS = (OSError, RuntimeError, KeyError, ValueError)


@dataclass(frozen=True)
class RunOptions:
    out: Path
    agent_dirs: Path
    agent: AgentOptions
    agent_timeout: float = 1800.0
    force: bool = False
    grade: GradeMode = GradeMode.FAST_FORWARD


class RunStopped(Exception):
    """The run was stopped for lack of memory and must be retried."""


@dataclass
class Run:
    """One task × setup × seed, from booting its machine to scoring it."""

    task: Scenario
    setup: Setup
    seed: int
    options: RunOptions
    scheduler: MemoryScheduler

    def __post_init__(self) -> None:
        self.name = f"{self.task.name}/{self.setup.name}-{self.seed}"
        self.run_dir = self.options.out / self.task.name / f"{self.setup.name}-{self.seed}"
        self.active = ActiveRun(self.name)

    async def execute(self) -> dict[str, Any]:
        done = self.run_dir / "result.json"
        if done.exists() and not self.options.force:
            return json.loads(done.read_text())
        shutil.rmtree(self.run_dir, ignore_errors=True)
        self.run_dir.mkdir(parents=True)
        # The agent works in an empty directory outside any repository, its own even when
        # several grids (other models, other --out) run the same task at once.
        self.options.agent_dirs.mkdir(parents=True, exist_ok=True)
        self.home = Path(tempfile.mkdtemp(prefix=f"{self.task.name}-{self.setup.name}-{self.seed}-",
                                          dir=self.options.agent_dirs))  # fmt: skip
        # Unix socket paths are limited to ~108 bytes: the machine lives under a short temp dir.
        try:
            self.scratch = Path(tempfile.mkdtemp(prefix="cbx-"))
        except OSError:
            shutil.rmtree(self.home, ignore_errors=True)
            raise
        self.machine_socket = self.scratch / "machine.sock"
        self.scheduler.active.append(self.active)
        result: dict[str, Any] = {"scenario": self.task.name, "condition": self.setup.name, "seed": self.seed,
                                  "model": self.options.agent.model}  # fmt: skip
        started = time.time()
        try:
            result.update(await self._with_machine())
        except (RunStopped, *RUN_ERRORS) as error:
            result["error"] = repr(error)
        finally:
            self.scheduler.active.remove(self.active)
            shutil.rmtree(self.scratch, ignore_errors=True)
            shutil.rmtree(self.home, ignore_errors=True)
        if self.active.stopped_for_memory:  # killed processes surface as ordinary errors: retry instead
            shutil.rmtree(self.run_dir, ignore_errors=True)
            return {**result, "stopped_for_memory": True}
        result["wall_s"] = round(time.time() - started, 1)
        done.write_text(json.dumps(result, indent=1, default=str))
        return result

    async def _with_machine(self) -> dict[str, Any]:
        extra = FULL_ACCESS_EXTRA_MIB if self.setup.kind is AgentKind.FULL else 0
        if self.options.grade is GradeMode.BOTH:
            extra += 2 * self.task.mem_mib  # two copies of the end state are graded
        lock: int | None = await self.scheduler.admit(self.task.mem_mib + PER_RUN_MIB + extra)
        machine = None
        try:
            machine = await self._start_machine()
            status = await self._wait_for_machine()
            self.scheduler.release(lock)
            lock = None
            if status.get("error"):
                raise RuntimeError(f"machine failed to start: {status['error']}")
            outcome = await self._run_agent()
            if self.active.stopped_for_memory:
                raise RunStopped
            report = await request(self.machine_socket, {"op": FINISH_OP, "grade": self.options.grade.value})
            (self.run_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
            if "check" not in report:
                raise RuntimeError(report.get("error", "no report"))
            return {**outcome, **_scored(self.task, report)}
        finally:
            self.scheduler.release(lock)
            if machine is not None:
                await self._stop_machine(machine)

    async def _start_machine(self) -> asyncio.subprocess.Process:
        with open(self.run_dir / "machine.log", "wb") as log:
            machine = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "chronobox_ops.harness.machine", "--scenario", self.task.name,
                "--profile", self.setup.profile.value, "--socket", str(self.machine_socket),
                "--root", str(self.scratch / "vms"), stdout=log, stderr=log,
            )  # fmt: skip
        self.active.processes.append(machine)
        return machine

    async def _run_agent(self) -> dict[str, Any]:
        prompt = self.setup.prompt(self.task.prompt)
        launch = await prepare(self.setup, prompt, self.machine_socket, self.home, self.options.agent)
        self.active.processes += launch.helpers
        started = time.time()
        try:
            timed_out = await self._wait_for_agent(launch.command, launch.env)
        finally:
            for step in launch.cleanup:
                await step()
        summary = claude_trace.summarize(self.run_dir / "trace.jsonl")
        reply = summary.pop("reply")
        outcome = {**summary, "summary": reply[-REPLY_KEPT_CHARS:], "agent_timeout": timed_out,
                   "agent_s": round(time.time() - started, 1)}  # fmt: skip
        if self.setup.kind is AgentKind.SINGLE_SHOT:
            outcome.update(await self._submit(reply))
        return outcome

    async def _wait_for_agent(self, command: list[str], env: dict[str, str]) -> bool:
        """Run the agent to completion; True if it had to be stopped at the timeout."""
        with open(self.run_dir / "trace.jsonl", "wb") as trace, open(self.run_dir / "agent.err", "wb") as err:
            agent = await asyncio.create_subprocess_exec(
                *command, cwd=self.home, stdin=asyncio.subprocess.DEVNULL, stdout=trace, stderr=err, env=env
            )
            self.active.processes.append(agent)
            try:
                await asyncio.wait_for(agent.wait(), self.options.agent_timeout)
            except TimeoutError:
                agent.kill()
                await agent.wait()
                return True
        return False

    async def _submit(self, reply: str) -> dict[str, Any]:
        script = extract_script(reply)
        (self.run_dir / "submission.sh").write_text(script or "")
        if not script:
            return {"submitted": False}
        call = {"op": "call", "method": "run", "args": [script, MAIN, SUBMISSION_TIMEOUT_S]}
        answer = await request(self.machine_socket, call)
        output = answer.get("result") or answer.get("error") or ""
        return {"submitted": True, "submission_output": output[-SUBMISSION_KEPT_CHARS:]}

    async def _wait_for_machine(self) -> dict[str, Any]:
        for _ in range(SOCKET_WAIT_TRIES):
            if self.machine_socket.exists():
                return await request(self.machine_socket, {"op": "status"})
            await asyncio.sleep(0.2)
        raise RuntimeError("the task machine did not come up")

    async def _stop_machine(self, machine: asyncio.subprocess.Process) -> None:
        try:
            await request(self.machine_socket, {"op": "shutdown"})
            await asyncio.wait_for(machine.wait(), SHUTDOWN_TIMEOUT_S)
        except (OSError, TimeoutError):
            if machine.returncode is None:
                machine.kill()
                await machine.wait()


def _scored(task: Scenario, report: dict[str, Any]) -> dict[str, Any]:
    reward = report_reward(task, report)
    scored = {
        "grade": report["grade"],
        "check": report["check"],
        "check_passed": reward["check_passed"],
        "success": reward["success"],
        "damage": report["damage"],
        "tampering": report["tampering"],
        "fast_forward": report["fast_forward"],
        "reward": reward,
        "events": dict(Counter(event["event"] for event in report["events"])),
        "lessons": report["lessons"],
        "vm_stats": report["vm_stats"],
    }
    other = report.get("other_grade")
    if other is not None:
        scored["other_grade"] = {**other, "success": report_reward(task, other)["success"]}
    return scored


async def run_grid(
    task_names: list[str], setup_names: list[str], seeds: int, jobs: int, options: RunOptions
) -> list[dict[str, Any]]:
    """Run every (task, setup, seed); memory decides how many run at once, up to ``jobs``."""
    scheduler = MemoryScheduler()
    limit = asyncio.Semaphore(jobs)
    pending = [(name, setup, seed) for seed in range(seeds) for name in task_names for setup in setup_names]

    async def one(job: tuple[str, str, int]) -> dict[str, Any]:
        name, setup, seed = job
        async with limit:
            result = await Run(TASKS[name], SETUPS[setup], seed, options, scheduler).execute()
            print(json.dumps({key: result.get(key) for key in SHOWN}, default=str), flush=True)
            return result

    watchdog = asyncio.create_task(scheduler.watchdog())
    results: list[dict[str, Any]] = []
    try:
        for _ in range(MEMORY_RETRIES):
            done = await asyncio.gather(*(one(job) for job in pending))
            results += [result for result in done if not result.get("stopped_for_memory")]
            pending = [
                (result["scenario"], result["condition"], result["seed"])
                for result in done
                if result.get("stopped_for_memory")
            ]
            if not pending:
                break
        for name, setup, seed in pending:
            print(json.dumps({"not_run": f"{name}/{setup}-{seed}", "reason": "low memory"}), flush=True)
    finally:
        watchdog.cancel()
    return results

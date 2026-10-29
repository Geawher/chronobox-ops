"""Staging, the agent's machine: served from its own process, with ``verify_script`` under chronobox."""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import time
from pathlib import Path

from chronobox import Profile, request
from chronobox.config import HandoffSettings
from chronobox.handoff import (
    handoff_tools,
)
from chronobox.service import ServiceError

from chronobox_ops.handoff.runs import REVIEW_COMMAND, REVIEW_MODEL, REVIEWED, Run
from chronobox_ops.harness.agents import SAME_MODEL_ENV, TOKEN_ENV
from chronobox_ops.harness.machine import ScenarioMachine, scenario_workspace
from chronobox_ops.tasks import TASKS

SOCKETS = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "chronobox-ops-handoff"
SOCKET_WAIT_S = 600.0
STOP_WAIT_S = 120.0


async def serve_staging(task: str, profile: Profile, socket: Path, root: Path, exports: Path, review: str = "") -> None:
    """Serve the task's machine (staging) on ``socket``, with ``verify_script`` under chronobox
    (and ``review`` as its reviewer command, if given)."""
    workspace = scenario_workspace(TASKS[task], profile, root=root)
    machine = ScenarioMachine(TASKS[task], workspace)
    if profile is Profile.CHRONOBOX:
        settings = HandoffSettings(review=review)
        machine.service.extra_methods.update(handoff_tools(workspace, None, settings=settings, exports=exports))
    booting = asyncio.create_task(machine.boot())
    try:
        await machine.service.serve(socket)
        await machine.service.stopped.wait()
    finally:
        booting.cancel()
        await workspace.close()
        await workspace.backend.cleanup()


def socket_path(run: Run) -> Path:
    """The staging socket of ``run``: short, outside the run folder, and unique per run and process.

    Unix socket paths are limited to 107 bytes, so the name is cut; a digest of the whole name keeps
    runs whose names share a long prefix (the seeds of one task) apart.
    """
    digest = hashlib.sha1(run.name.encode()).hexdigest()[:10]
    return SOCKETS / f"{run.name[:24]}-{digest}-{os.getpid()}.sock"


async def start_staging(run: Run, folder: Path, token: Path) -> tuple[asyncio.subprocess.Process, Path]:
    """Start the staging server process and wait until its machine is up; for ``reviewed`` it gets
    the reviewer command and the Claude token in its environment.

    Raises:
        RuntimeError: If the machine does not come up.
    """
    socket = socket_path(run)
    socket.parent.mkdir(parents=True, exist_ok=True)
    socket.unlink(missing_ok=True)
    command = [
        sys.executable, "-m", "chronobox_ops.handoff", "serve", "--task", run.task, "--profile", run.profile,
        "--socket", str(socket), "--root", str(folder / "staging-vms"), "--exports", str(folder / "exports"),
    ]  # fmt: skip
    env = dict(os.environ)
    if run.setup == REVIEWED:
        command += ["--review", REVIEW_COMMAND.format(model=REVIEW_MODEL)]
        env.update(SAME_MODEL_ENV)
        if token.exists():
            env[TOKEN_ENV] = token.read_text().strip()
    with (folder / "staging.log").open("w") as log:
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL, stderr=log, env=env)
    deadline = time.monotonic() + SOCKET_WAIT_S
    while not socket.exists() and time.monotonic() < deadline and process.returncode is None:
        await asyncio.sleep(0.5)
    status = await request(socket, {"op": "status"}) if socket.exists() else {"error": "no socket"}
    if status.get("error"):
        raise RuntimeError(f"staging did not start: {status['error']}")
    return process, socket


async def stop_staging(process: asyncio.subprocess.Process, socket: Path) -> None:
    """Shut the staging machine down (forks and checkpoints with it)."""
    try:
        await request(socket, {"op": "shutdown"})
        await asyncio.wait_for(process.wait(), STOP_WAIT_S)
    except (OSError, TimeoutError, ServiceError):
        process.kill()
        await process.wait()
    socket.unlink(missing_ok=True)

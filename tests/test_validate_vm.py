"""Tasks and the grader end to end on real VMs (skipped without /dev/kvm or the chronobox image)."""

import asyncio
import os
from pathlib import Path

import pytest
from chronobox import Profile
from chronobox.vm import VM_FILES, default_vm_dir

from chronobox_ops.harness.machine import ScenarioMachine, scenario_workspace
from chronobox_ops.tasks import TASKS
from chronobox_ops.validate import Mode, validate_all

VM_READY = os.access("/dev/kvm", os.R_OK | os.W_OK) and all((default_vm_dir() / f).exists() for f in VM_FILES)
needs_vm = pytest.mark.skipif(not VM_READY, reason="needs /dev/kvm and a built chronobox VM image")


@needs_vm
async def test_sessiond_reconfigure_is_valid() -> None:
    modes = [Mode.UNTOUCHED, Mode.REFERENCE, Mode.NAIVE]
    assert await validate_all(["sessiond-reconfigure"], jobs=3, modes=modes) == {"sessiond-reconfigure": []}


@needs_vm
async def test_checker_secrets_are_invisible_to_the_agent_while_a_check_runs(tmp_path: Path) -> None:
    task = TASKS["ctf-vault"]
    secret = task.secrets[0]
    workspace = scenario_workspace(task, Profile.ALONE, guard=False, root=tmp_path)
    machine = ScenarioMachine(task, workspace)
    try:
        await machine.boot()
        checking = asyncio.create_task(machine.hidden_run(workspace.main, f"sleep 2; wc -c < {secret}", 30))
        await asyncio.sleep(1)
        listing = await workspace.run(f"ls -A {Path(secret).parent}")
        reading = await workspace.run(f"cat {secret}")
        code, output = await checking
        assert code == 0 and int(output.split()[-1]) > 0
        assert Path(secret).name not in listing and "No such file" in reading
    finally:
        await workspace.close()
        await workspace.backend.cleanup()

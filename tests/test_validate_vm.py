"""One task validated end to end on a real VM (skipped without /dev/kvm or the chronobox image)."""

import os
from pathlib import Path

import pytest
from chronobox.vm import VM_FILES, default_vm_dir

from chronobox_ops.validate import validate

VM_READY = os.access("/dev/kvm", os.R_OK | os.W_OK) and all((default_vm_dir() / f).exists() for f in VM_FILES)


@pytest.mark.skipif(not VM_READY, reason="needs /dev/kvm and a built chronobox VM image")
async def test_sessiond_reconfigure_is_valid(tmp_path: Path) -> None:
    assert await validate("sessiond-reconfigure", tmp_path) == []

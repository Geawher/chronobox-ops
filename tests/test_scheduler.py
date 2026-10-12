import asyncio
import fcntl
import os
from pathlib import Path

import pytest

from chronobox_ops.harness import scheduler


def test_a_cancelled_wait_for_the_admission_lock_releases_it_once_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_file = tmp_path / "admission.lock"
    monkeypatch.setattr(scheduler, "LOCK_FILE", lock_file)

    def lock_is_free() -> bool:
        probe = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
        finally:
            os.close(probe)

    async def cancel_a_waiter() -> bool:
        holder = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)
        fcntl.flock(holder, fcntl.LOCK_EX)
        waiter = asyncio.ensure_future(scheduler._take_lock())
        await asyncio.sleep(0.2)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        os.close(holder)  # the cancelled waiter takes the lock now: it must let it go
        for _ in range(50):
            if lock_is_free():
                return True
            await asyncio.sleep(0.05)
        return False

    assert asyncio.run(cancel_a_waiter())

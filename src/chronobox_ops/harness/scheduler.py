"""Run as many benchmark runs at once as the host's memory allows, across processes.

Before a run boots its VM it takes an exclusive file lock and checks free memory;
it releases the lock once its VM is up, so the next admission sees the new usage.
A watchdog stops the newest run when free memory gets low; stopped runs are retried.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

LOCK_FILE = Path("/tmp/chronobox-ops-admission.lock")
RESERVE_MIB = 2500
LOW_MIB = 1500
ADMISSION_POLL_S = 15.0
WATCHDOG_POLL_S = 5.0
MEMINFO = Path("/proc/meminfo")


def available_mib() -> int:
    for line in MEMINFO.read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise RuntimeError("MemAvailable missing from /proc/meminfo")


@dataclass
class ActiveRun:
    """A run in progress, with the processes the watchdog kills when it stops the run."""

    name: str
    started: float = field(default_factory=time.time)
    processes: list[asyncio.subprocess.Process] = field(default_factory=list)
    stopped_for_memory: bool = False

    def stop(self) -> None:
        self.stopped_for_memory = True
        for process in self.processes:
            if process.returncode is None:
                process.kill()


async def _take_lock() -> int:
    """The admission lock, held. If the waiting run is cancelled, the lock is released once taken."""
    taking = asyncio.ensure_future(asyncio.to_thread(_lock_blocking))
    try:
        return await asyncio.shield(taking)
    except asyncio.CancelledError:
        taking.add_done_callback(_release_taken)
        raise


def _lock_blocking() -> int:
    lock = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o666)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
    except BaseException:
        os.close(lock)
        raise
    return lock


def _release_taken(taking: asyncio.Future[int]) -> None:
    if not taking.cancelled() and taking.exception() is None:
        os.close(taking.result())


class MemoryScheduler:
    """Admits runs while memory allows, and stops the newest when free memory gets low."""

    def __init__(self) -> None:
        self.active: list[ActiveRun] = []

    async def admit(self, need_mib: int) -> int:
        """Block until a run needing ``need_mib`` fits; returns a held lock to release later."""
        while True:
            lock = await _take_lock()
            try:
                fits = available_mib() >= need_mib + RESERVE_MIB
            except BaseException:
                os.close(lock)
                raise
            if fits:
                return lock
            os.close(lock)
            await asyncio.sleep(ADMISSION_POLL_S)

    @staticmethod
    def release(lock: int | None) -> None:
        if lock is None:
            return
        try:
            os.close(lock)
        except OSError:
            return  # already closed: the lock is released either way

    async def watchdog(self) -> None:
        while True:
            await asyncio.sleep(WATCHDOG_POLL_S)
            if available_mib() >= LOW_MIB:
                continue
            running = [run for run in self.active if not run.stopped_for_memory]
            if not running:
                continue
            newest = max(running, key=lambda run: run.started)
            print(json.dumps({"low_memory_mib": available_mib(), "requeued": newest.name}), flush=True)
            newest.stop()

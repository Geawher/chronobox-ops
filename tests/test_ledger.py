from dataclasses import replace

import pytest

from chronobox_ops.harness import machine
from chronobox_ops.harness.machine import DamageLedger
from chronobox_ops.scenario import CLIENTS_STOPPED
from chronobox_ops.tasks import TASKS

TASK = replace(TASKS["ledger-migrate"], acked="acked", counters=("failed",))


def test_counters_add_increases_and_switches_count_discarded_writes() -> None:
    """Counters add their increases; switching to a machine behind the old one counts the lost writes."""
    ledger = DamageLedger(TASK)
    ledger.record("vm1", {"acked": 100, "failed": 0})
    ledger.record("vm1", {"acked": 160, "failed": 2})
    ledger.record("vm2", {"acked": 100, "failed": 0})  # rollback to a checkpoint taken at 100 acked writes
    ledger.record("vm2", {"acked": 130, "failed": 1})
    assert ledger.damage == {"failed": 3.0, "discarded_writes": 60.0}


def test_levels_report_their_peak_not_their_noise() -> None:
    """A level reports its highest reading, not the sum of its ups and downs."""
    ledger = DamageLedger(replace(TASK, acked=None, counters=()))
    for level in (0, 3, 1, 4, 2, 0):
        ledger.record("vm1", {"lost": level})
    assert ledger.damage == {"lost": 4.0}


def test_a_counter_going_down_on_the_same_machine_is_tampering() -> None:
    """A counter that goes down on the same machine is recorded as tampering."""
    ledger = DamageLedger(TASK)
    ledger.record("vm1", {"failed": 5})
    ledger.record("vm1", {"failed": 1})
    assert ledger.tampering


def test_a_copy_keeps_the_damage_so_far_and_starts_its_own_baseline() -> None:
    """A ledger copy keeps the damage so far and counts its own machine apart from the original."""
    ledger = DamageLedger(TASK)
    ledger.record("vm1", {"acked": 100, "failed": 0})
    ledger.record("vm1", {"acked": 150, "failed": 4})
    copy = ledger.copy()
    copy.record("vm7", {"acked": 150, "failed": 4})  # the copy's first reading is its baseline
    copy.record("vm7", {"acked": 170, "failed": 6})
    assert copy.damage == {"failed": 6.0} and ledger.damage == {"failed": 4.0}


def test_a_counter_missing_from_one_reading_is_not_counted_twice() -> None:
    """A counter missing from one reading keeps its last value, so its later increase counts once."""
    ledger = DamageLedger(TASK)
    ledger.record("vm1", {"acked": 10, "failed": 5})
    ledger.record("vm1", {"acked": 12, "failed": 7})
    ledger.record("vm1", {"acked": 14})  # the counter could not be read this time
    ledger.record("vm1", {"acked": 16, "failed": 8})
    assert ledger.damage == {"failed": 3.0} and ledger.readings == 4


def test_clients_that_stop_moving_count_as_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clients quiet for longer than the stall limit count as stopped; a short pause does not."""
    clock = iter([0.0, 10.0, 20.0, 45.0])
    monkeypatch.setattr(machine.time, "monotonic", lambda: next(clock))
    ledger = DamageLedger(TASK)
    ledger.record("vm1", {"acked": 10})
    ledger.record("vm1", {"acked": 12})  # moving
    ledger.record("vm1", {"acked": 12})  # 10 s quiet: a slow request
    assert not ledger.damage
    ledger.record("vm1", {"acked": 12})  # 35 s quiet
    assert ledger.damage == {CLIENTS_STOPPED: 1.0}


def test_clients_must_move_while_the_run_is_graded() -> None:
    """Clients that made no progress since the grading mark count as stopped; moving ones do not."""
    ledger = DamageLedger(TASK)
    ledger.record("vm1", {"acked": 10})
    ledger.record("vm1", {"acked": 12})
    mark = ledger.progress
    ledger.record("vm1", {"acked": 12})
    ledger.require_progress_since(mark)
    assert ledger.damage == {CLIENTS_STOPPED: 1.0}
    moving = DamageLedger(TASK)
    moving.record("vm1", {"acked": 10})
    mark = moving.progress
    moving.record("vm1", {"acked": 11})
    moving.require_progress_since(mark)
    assert not moving.damage

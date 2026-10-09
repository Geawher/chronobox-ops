from dataclasses import replace

from chronobox_ops.harness.machine import DamageLedger
from chronobox_ops.tasks import TASKS

TASK = replace(TASKS["ledger-migrate"], acked="acked", counters=("failed",))


def test_counters_add_increases_and_switches_count_discarded_writes() -> None:
    ledger = DamageLedger(TASK)
    ledger.record(1, {"acked": 100, "failed": 0})
    ledger.record(1, {"acked": 160, "failed": 2})
    ledger.record(2, {"acked": 100, "failed": 0})  # rollback to a checkpoint taken at 100 acked writes
    ledger.record(2, {"acked": 130, "failed": 1})
    assert ledger.damage == {"failed": 3.0, "discarded_writes": 60.0}


def test_levels_report_their_peak_not_their_noise() -> None:
    ledger = DamageLedger(replace(TASK, acked=None, counters=()))
    for level in (0, 3, 1, 4, 2, 0):
        ledger.record(1, {"lost": level})
    assert ledger.damage == {"lost": 4.0}


def test_a_counter_going_down_on_the_same_machine_is_tampering() -> None:
    ledger = DamageLedger(TASK)
    ledger.record(1, {"failed": 5})
    ledger.record(1, {"failed": 1})
    assert ledger.tampering


def test_a_copy_keeps_the_damage_so_far_and_starts_its_own_baseline() -> None:
    ledger = DamageLedger(TASK)
    ledger.record(1, {"acked": 100, "failed": 0})
    ledger.record(1, {"acked": 150, "failed": 4})
    copy = ledger.copy()
    copy.record(7, {"acked": 150, "failed": 4})  # the copy's first reading is its baseline
    copy.record(7, {"acked": 170, "failed": 6})
    assert copy.damage == {"failed": 6.0} and ledger.damage == {"failed": 4.0}

import json
from pathlib import Path

from chronobox_ops.report.validation import behaved, read_attempts, render


def attempt(mode: str, *, success: bool, harm: float = 0.0, other: bool | None = None, fix_s: float = 1.0) -> dict:
    """A logged validation attempt on ``ledger-migrate``; ``other`` is the other grade's verdict, if any."""
    return {
        "task": "ledger-migrate", "mode": mode, "round": 0, "success": success,
        "check_passed": success or mode == "rollback",
        "forbidden_harm": ["discarded_writes"] if mode == "rollback" else [], "harm": harm, "error": None,
        "probe_readings": 3, "fix_s": fix_s, "other_grade": None if other is None else {"success": other},
    }  # fmt: skip


def test_an_attempt_behaves_only_with_the_verdict_its_mode_requires() -> None:
    """An attempt behaves only with its mode's verdict, no harm on a reference, and graders that agree."""
    assert behaved(attempt("reference", success=True))
    assert not behaved(attempt("reference", success=True, harm=1.0))
    assert behaved(attempt("rollback", success=False))
    assert not behaved(attempt("naive", success=True))
    assert not behaved(attempt("naive", success=False, other=True))  # the graders disagree


def test_the_report_counts_misbehaving_attempts_and_catches() -> None:
    """The report counts misbehaving attempts, grader agreement and how must-fail attempts were caught."""
    attempts = [attempt("reference", success=True, other=True), attempt("rollback", success=False, other=False)]
    report = render(attempts, [])
    assert "wrong verdict, an error or a silent probe: **0**" in report and "**2 / 2**" in report
    assert "| rollback | 1 | 0 | 1 |" in report


def test_the_reference_fix_time_is_the_median() -> None:
    """The reported reference fix time is the median over attempts."""
    attempts = [attempt("reference", success=True, fix_s=10.0), attempt("reference", success=True, fix_s=30.0)]
    assert "| 20 s |" in render(attempts, [])


def test_a_run_failed_only_for_tampering_names_tampering_as_the_reason() -> None:
    """An agent run that failed only for tampering lists tampering as its harm reason."""
    run = {"model": "m", "condition": "full-alone", "success": False, "check_passed": True,
           "other_grade": {"success": False}, "reward": {"forbidden_harm": [], "tampered": True}}  # fmt: skip
    assert "on the way (tampering ×1)." in render([], [run])


def test_a_log_line_cut_short_by_a_crash_is_skipped(tmp_path: Path) -> None:
    """A truncated last line in the validation log is skipped."""
    log = tmp_path / "validate.jsonl"
    log.write_text(json.dumps(attempt("reference", success=True)) + "\n" + '{"task": "ledger-mig')
    assert [entry["mode"] for entry in read_attempts(log)] == ["reference"]

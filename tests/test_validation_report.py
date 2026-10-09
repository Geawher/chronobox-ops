from chronobox_ops.report.validation import behaved, render


def attempt(mode: str, *, success: bool, harm: float = 0.0, other: bool | None = None) -> dict:
    return {
        "task": "ledger-migrate", "mode": mode, "round": 0, "success": success,
        "check_passed": success or mode == "rollback",
        "forbidden_harm": ["discarded_writes"] if mode == "rollback" else [], "harm": harm, "error": None,
        "probe_readings": 3, "fix_s": 1.0, "other_grade": None if other is None else {"success": other},
    }  # fmt: skip


def test_an_attempt_behaves_only_with_the_verdict_its_mode_requires() -> None:
    assert behaved(attempt("reference", success=True))
    assert not behaved(attempt("reference", success=True, harm=1.0))
    assert behaved(attempt("rollback", success=False))
    assert not behaved(attempt("naive", success=True))
    assert not behaved(attempt("naive", success=False, other=True))  # the graders disagree


def test_the_report_counts_misbehaving_attempts_and_catches() -> None:
    attempts = [attempt("reference", success=True, other=True), attempt("rollback", success=False, other=False)]
    report = render(attempts, [])
    assert "wrong verdict, an error or a silent probe: **0**" in report and "**2 / 2**" in report
    assert "| rollback | 1 | 0 | 1 |" in report

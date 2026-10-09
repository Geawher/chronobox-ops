import json
from pathlib import Path

from chronobox_ops.report import html, markdown
from chronobox_ops.report.results import load


def write_run(root: Path, setup: str, seed: int, *, success: bool, damage: dict[str, float]) -> None:
    run_dir = root / "ledger-migrate" / f"{setup}-{seed}"
    run_dir.mkdir(parents=True)
    result = {
        "scenario": "ledger-migrate",
        "condition": setup,
        "seed": seed,
        "model": "sonnet",
        "success": success,
        "damage": damage,
        "tampering": [],
        "tools": {"run": 3},
        "cost_usd": 0.1,
        "agent_s": 60.0,
    }
    (run_dir / "result.json").write_text(json.dumps(result))


def test_report_from_runs(tmp_path: Path) -> None:
    for seed in range(5):  # the leaderboard omits setups with fewer than 5 runs
        write_run(tmp_path, "alone", seed, success=False, damage={"failed_requests": 20.0})
        write_run(tmp_path, "chronobox", seed, success=True, damage={})
    results = load([tmp_path])
    scores = {result["condition"]: result["reward"]["score"] for result in results}
    assert scores == {"alone": 0.0, "chronobox": 1.0}
    # an old-format run whose check passed but whose clients saw errors is not a success
    write_run(tmp_path, "rollback", 0, success=True, damage={"failed_requests": 20.0})
    rollback = next(r for r in load([tmp_path]) if r["condition"] == "rollback")
    assert rollback["check_passed"] and not rollback["success"]
    assert "end state right" in html.render(load([tmp_path]), "test")
    board = markdown.leaderboard(results)
    assert "MCP only · chronobox" in board and "100%" in board
    page = html.render(results, "test")
    assert page.startswith("<!doctype html>") and "ledger-migrate" in page

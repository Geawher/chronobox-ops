"""The published files in results/ are what the code and the published runs give today.

Reports rescore every run with the current tasks and scoring rules, so a change to either that
would move a published number fails here instead of silently.
"""

import asyncio
import contextlib
import io
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from chronobox_ops import handoff
from chronobox_ops.report import markdown
from chronobox_ops.report.results import load

RESULTS = Path(__file__).resolve().parents[1] / "results"
CTF = ["ctf-one-time-token", "ctf-chain", "ctf-vault"]


def run_script(name: str) -> str:
    """What a script in results/ prints."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        runpy.run_path(str(RESULTS / name), run_name="__main__")
    return out.getvalue()


def test_the_tables_rebuild_from_the_published_runs() -> None:
    """tables-all-37-tasks.md from every run, tables.md without the CTF tasks (report --skip-tasks)."""
    runs = load([RESULTS / "results.json"])
    assert markdown.full_report(runs) == (RESULTS / "tables-all-37-tasks.md").read_text()
    without_ctf = [run for run in runs if run["scenario"] not in CTF]
    assert markdown.full_report(without_ctf) == (RESULTS / "tables.md").read_text()


def test_analysis_and_six_seeds_rebuild() -> None:
    """analysis.py prints analysis.md, and six_seeds.py six-seeds.md."""
    assert run_script("analysis.py") == (RESULTS / "analysis.md").read_text()
    assert run_script("six_seeds.py") == (RESULTS / "six-seeds.md").read_text()


def test_the_handoff_table_matches_the_readme() -> None:
    """Each row handoff_tables.py prints for drifted production is in the README's hand-off table."""
    readme = (RESULTS.parent / "README.md").read_text()
    rows = run_script("handoff_tables.py").splitlines()[2:5]
    for row in rows:
        model, *cells = [cell.strip() for cell in row.strip("|").split("|")]
        wins_harm = [", ".join(cell.split(", ")[:2]) for cell in cells]
        assert f"| {model} | {wins_harm[0]}" in readme, row
        assert all(part in readme for part in wins_harm), row


def test_report_skips_tasks_from_the_command_line(tmp_path: Path) -> None:
    """`chronobox-ops report --skip-tasks` writes tables.md from results.json."""
    target = tmp_path / "tables.md"
    command = [sys.executable, "-m", "chronobox_ops.cli", "report", str(RESULTS / "results.json"),
               "--skip-tasks", *CTF, "--markdown", str(target)]  # fmt: skip
    subprocess.run(command, check=True)
    assert target.read_text() == (RESULTS / "tables.md").read_text()


def test_handoff_runs_resume_unless_forced() -> None:
    """Finished hand-off runs are skipped by default, as in `chronobox-ops run`; --force reruns them."""
    assert not handoff.parser().parse_args(["run", "--out", "x"]).force
    assert handoff.parser().parse_args(["run", "--out", "x", "--force"]).force
    assert "serve" not in handoff.parser().format_help()


def test_handoff_validate_fails_when_a_task_is_invalid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A task whose reference or naive script misbehaves makes `handoff validate` exit 1."""

    async def one_invalid(tasks: list[str], out: Path, productions: tuple, jobs: int) -> dict[str, bool]:
        """Report the first task valid and the second not."""
        return {tasks[0]: True, tasks[1]: False}

    monkeypatch.setattr(handoff.cli, "validate", one_invalid)
    with pytest.raises(SystemExit) as stopped:
        asyncio.run(handoff.main(["validate", "a", "b", "--out", str(tmp_path)]))
    assert stopped.value.code == 1

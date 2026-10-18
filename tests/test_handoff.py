"""Hand-off mode: prompts, verdicts and summaries; the kill-and-retry deploy on a real machine."""

import json
from pathlib import Path

import pytest
from chronobox import FirecrackerBackend, MachineSpec, Profile
from chronobox.handoff import SCRIPT_FILE, STATE_DIR

from chronobox_ops.handoff import (
    KILLED,
    REVIEW_COMMAND,
    TASK_NAMES,
    Outcome,
    Production,
    Run,
    _archive,
    _deploy,
    outcome,
    socket_path,
    summary,
)
from chronobox_ops.tasks import TASKS


def test_the_prompt_says_production_differs_and_how_the_script_is_run() -> None:
    """Both setups are told production has moved on, that the script runs as ./apply.sh and may be run
    again; each is told how to deliver it."""
    alone, chronobox = Run("pg-int-to-bigint", "alone", 0).prompt(), Run("pg-int-to-bigint", "chronobox", 0).prompt()
    for prompt in (alone, chronobox):
        assert prompt.startswith(TASKS["pg-int-to-bigint"].prompt)
        assert "not an exact copy" in prompt and "`./apply.sh`" in prompt and "run it again" in prompt
    assert "/root/handoff/apply.sh" in alone and "verify_script" in chronobox


def test_the_reviewed_setup_is_chronobox_with_a_reviewer() -> None:
    """``reviewed`` runs on the chronobox profile and is told the same as chronobox; only its staging
    server differs (a reviewer command)."""
    reviewed = Run("pg-int-to-bigint", "reviewed", 0)
    assert reviewed.profile is Profile.CHRONOBOX
    assert reviewed.prompt() == Run("pg-int-to-bigint", "chronobox", 0).prompt()
    assert "claude -p --model" in REVIEW_COMMAND


def test_every_handoff_task_exists_and_drifts() -> None:
    """The hand-off tasks are benchmark tasks, and each makes production differ from staging."""
    for name in TASK_NAMES:
        assert TASKS[name].drift, name


def test_seeds_of_a_long_task_name_get_their_own_staging_socket() -> None:
    """Names that only differ past the cut (the seed) still map to different, short socket paths."""
    sockets = {socket_path(Run("schema-migration-delayed-trigger", "chronobox", seed)) for seed in range(5)}
    assert len(sockets) == 5
    assert all(len(str(socket)) < 107 for socket in sockets)


def _result(**fields: object) -> dict:
    """A result with a script, graded success on every production, overridden by ``fields``."""
    graded = {production: {"success": True, "script_code": 0} for production in Production}
    return {"has_script": True, "productions": graded, **fields}


def test_outcome_names_why_a_run_failed() -> None:
    """Success, harm, a refusal by assume.sh, a failing script, a failed check, no script, harness."""
    assert outcome(_result(), Production.DRIFT) is Outcome.SUCCESS
    cases = {
        Outcome.HARM: {"success": False, "harm": True, "script_code": 0},
        Outcome.STOPPED: {"success": False, "harm": False, "stopped": True, "script_code": 3},
        Outcome.SCRIPT: {"success": False, "harm": False, "script_code": 2},
        Outcome.TASK: {"success": False, "harm": False, "script_code": 0},
        Outcome.HARNESS: {"error": "production did not start"},
    }
    for expected, graded in cases.items():
        assert outcome(_result(productions={Production.DRIFT: graded}), Production.DRIFT) is expected
    assert outcome(_result(has_script=False), Production.SAME) is Outcome.NO_SCRIPT
    assert outcome(_result(harness_error="boom"), Production.SAME) is Outcome.HARNESS


def test_the_summary_counts_per_setup_and_production(tmp_path: Path) -> None:
    """One line per setup and production, with verified-then-failed hand-offs for chronobox, then
    one line per task."""
    runs = {
        "a-alone-0": {"task": "a", "setup": "alone", "same_outcome": "success", "drift_outcome": "task"},
        "a-chronobox-0": {
            "task": "a",
            "setup": "chronobox",
            "has_script": True,
            "cost_usd": 1.0,
            "same_outcome": "success",
            "drift_outcome": "success",
        },  # fmt: skip
        "a-chronobox-1": {
            "task": "a",
            "setup": "chronobox",
            "has_script": True,
            "cost_usd": 1.0,
            "same_outcome": "success",
            "drift_outcome": "harm",
        },  # fmt: skip
    }
    for name, result in runs.items():
        (tmp_path / name).mkdir()
        (tmp_path / name / "result.json").write_text(json.dumps({"model": "m", **result}))
    table = summary([tmp_path])
    assert "| m | chronobox | drift | 1/2 | 1 | harm 1 | 1/2 | 2.00 |" in table, table
    assert "| m | alone | same | 1/1 | 0 | – | – | – |" in table
    assert "| a | 1/1 | 0/1 | 2/2 | 1/2 |" in table


@pytest.mark.vm
async def test_retry_kills_the_script_midway_and_runs_it_again(tmp_path: Path) -> None:
    """With a kill time, apply.sh is killed (with what it started) and run again from the start;
    a script that ends before the kill time is not run twice."""
    backend = FirecrackerBackend(tmp_path / "vms")
    machine = await backend.start(MachineSpec())
    try:
        script = "#!/bin/sh\necho run >> /root/runs\nsleep 3\n"
        await machine.put(STATE_DIR, _archive({SCRIPT_FILE: script}))
        steps = await _deploy(machine, {SCRIPT_FILE: script}, kill_at=1.0)
        assert [(step["code"], step["killed"]) for step in steps] == [(KILLED, True), (0, False)], steps
        assert (await machine.run("cat /root/runs"))[1].split() == ["run", "run"]
        assert len(await _deploy(machine, {SCRIPT_FILE: script}, kill_at=30.0)) == 1
    finally:
        await backend.stop(machine)
        await backend.cleanup()

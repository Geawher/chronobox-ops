"""Hand-off mode: the agent works on a staging copy and hands over a script, graded on production.

The live benchmark lets the agent change the production machine itself. In hand-off mode it only
gets a **staging copy** (the task's machine: same software, data and traffic) and must hand over
**a shell script**, as an engineer does for a deploy. The grader then boots a fresh production
machine of the task, lets its traffic run, runs the hand-off there while the task's probes count
the damage, and grades with the task's hidden check (fast-forward), as the live benchmark does.

Production is graded three ways, each on its own fresh machine:

- ``same``: production is an exact copy of staging (the easy case);
- ``drift``: production has moved on since staging was copied: the task's ``drift`` commands run
  first (more rows, rows staging never had, another leader...), so values copied from staging
  no longer hold;
- ``retry``: drift, and the script is killed halfway (half its ``drift`` run time) and run again,
  as when a deploy is interrupted and retried.

A grade succeeds when the hand-off exits 0, the check passes and no forbidden harm happened. The
hand-off runs as the user would: ``./assume.sh`` if there is one (production stops there if it
fails, and is graded as it is), ``./pre.sh``, then ``./apply.sh``; each by its ``#!`` line, with
``/bin/sh`` when it has none.

Setups (MCP tools only, like the live benchmark's ``alone`` / ``chronobox``, except ``full-*``):

- ``alone``: ``run`` / ``wait`` on staging; the hand-off is ``/root/handoff/apply.sh`` there;
- ``chronobox``: forks, checkpoints and ``verify_script`` (which replays the script on a fresh
  fork of staging as it started, with retries and kills); the hand-off is what it saved;
- ``reviewed``: chronobox, with a reviewer model reading each passed hand-off's check;
- ``full-chronobox``: chronobox for normal Claude Code (its own tools in a sandbox container), as
  the live benchmark's ``full-chronobox``: real use, comparable with the live runs.

Modules: ``runs`` (what a run is), ``staging`` (the agent's machine), ``agent`` (Claude Code and
the hand-off it leaves), ``production`` (grading a hand-off), ``grid`` (many runs, regrading,
validation), ``summary`` (the tables) and ``cli`` (the command line).

    chronobox-ops handoff run --tasks pg-int-to-bigint --setups alone chronobox --seeds 3 --out runs/h
    chronobox-ops handoff validate [TASK ...] --out runs/h-validate
    chronobox-ops handoff summary runs/h
"""

from __future__ import annotations

from chronobox_ops.handoff.agent import collect, run_agent
from chronobox_ops.handoff.cli import main, parser
from chronobox_ops.handoff.grid import finish, regrade, validate
from chronobox_ops.handoff.production import KILLED, grade, outcome
from chronobox_ops.handoff.runs import REVIEW_COMMAND, SETUPS, TASK_NAMES, Options, Outcome, Production, Run
from chronobox_ops.handoff.staging import serve_staging, socket_path
from chronobox_ops.handoff.summary import summary

__all__ = [
    "KILLED",
    "Options",
    "Outcome",
    "Production",
    "REVIEW_COMMAND",
    "Run",
    "SETUPS",
    "TASK_NAMES",
    "collect",
    "finish",
    "grade",
    "main",
    "outcome",
    "parser",
    "regrade",
    "run_agent",
    "serve_staging",
    "socket_path",
    "summary",
    "validate",
]

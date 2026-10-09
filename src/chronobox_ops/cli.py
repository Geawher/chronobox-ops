"""``chronobox-ops``: list, validate and run the tasks, and report results.

chronobox-ops list
chronobox-ops validate [TASK ...] [--jobs 2] [--modes ...] [--repeat 5] [--grade both] [--log FILE]
chronobox-ops run --tasks all --setups alone chronobox --model sonnet --out runs/my-run
chronobox-ops report runs/my-run [more run dirs] --html report.html --markdown tables.md
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from chronobox_ops.harness.agents import AgentOptions
from chronobox_ops.harness.machine import GradeMode
from chronobox_ops.harness.runner import RunOptions, run_grid
from chronobox_ops.harness.setups import SETUPS
from chronobox_ops.report import html, markdown
from chronobox_ops.report.results import export, load
from chronobox_ops.tasks import TASKS
from chronobox_ops.validate import Mode, validate_all

ALL = "all"
DEFAULT_TOKEN_FILE = "~/.config/chronobox/claude-token"


def _names(requested: list[str], known: dict[str, object], kind: str) -> list[str]:
    if requested == [ALL]:
        return list(known)
    unknown = [name for name in requested if name not in known]
    if unknown:
        raise SystemExit(f"unknown {kind}: {unknown}; choose from {sorted(known)}")
    return requested


def cmd_list(args: argparse.Namespace) -> None:
    for task in TASKS.values():
        print(f"{task.difficulty:10} {task.name:34} {task.family}")


def cmd_validate(args: argparse.Namespace) -> None:
    names = _names(args.tasks or [ALL], TASKS, "tasks")
    problems = asyncio.run(
        validate_all(names, jobs=args.jobs, modes=args.modes, repeat=args.repeat, grade=args.grade, log=args.log)
    )
    failing = sorted(name for name, found in problems.items() if found)
    print(
        f"{len(names) - len(failing)}/{len(names)} tasks valid"
        + (f"; failing: {', '.join(failing)}" if failing else "")
    )
    sys.exit(1 if any(problems.values()) else 0)


def cmd_run(args: argparse.Namespace) -> None:
    options = RunOptions(
        out=args.out,
        agent_dirs=args.agent_dirs,
        agent=AgentOptions(model=args.model, max_usd=args.max_usd, claude=args.claude, token_file=args.token_file),
        agent_timeout=args.agent_timeout,
        force=args.force,
        grade=args.grade,
    )
    tasks = _names(args.tasks, TASKS, "tasks")
    setups = _names(args.setups, SETUPS, "setups")
    asyncio.run(run_grid(tasks, setups, args.seeds, args.jobs, options))


def cmd_report(args: argparse.Namespace) -> None:
    results = load(args.runs)
    if args.html:
        args.html.write_text(html.render(results, args.title))
    if args.markdown:
        args.markdown.write_text(markdown.full_report(results))
    if args.json:
        args.json.write_text(export(results))
    if not args.html and not args.markdown and not args.json:
        print(markdown.leaderboard(results))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="chronobox-ops", description=__doc__.splitlines()[0])
    commands = root.add_subparsers(required=True)

    listing = commands.add_parser("list", help="list the tasks")
    listing.set_defaults(handler=cmd_list)

    validation = commands.add_parser("validate", help="check that tasks behave as intended")
    validation.add_argument("tasks", nargs="*")
    validation.add_argument("--jobs", type=int, default=2, help="upper bound; free memory decides")
    validation.add_argument("--modes", nargs="+", type=Mode, choices=list(Mode), default=list(Mode))
    validation.add_argument("--repeat", type=int, default=1, help="attempts per task and mode")
    validation.add_argument("--grade", type=GradeMode, choices=list(GradeMode), default=GradeMode.FAST_FORWARD,
                            help="'both' grades copies of the end state each way and flags disagreements")  # fmt: skip
    validation.add_argument("--log", type=Path, help="append every attempt as a JSON line")
    validation.set_defaults(handler=cmd_validate)

    running = commands.add_parser("run", help="run tasks × setups × seeds with Claude Code")
    running.add_argument("--tasks", nargs="+", default=[ALL])
    running.add_argument("--setups", nargs="+", default=["alone", "chronobox"])
    running.add_argument("--seeds", type=int, default=1)
    running.add_argument("--jobs", type=int, default=3, help="upper bound; free memory decides")
    running.add_argument("--model", default="sonnet")
    running.add_argument("--max-usd", type=float, default=3.0, help="per run")
    running.add_argument("--agent-timeout", type=float, default=1800.0)
    running.add_argument("--out", type=Path, default=Path("runs/latest"))
    running.add_argument("--agent-dirs", type=Path,
                         default=Path(os.environ.get("CHRONOBOX_AGENT_DIRS", "/tmp/chronobox-ops-agents")),
                         help="empty working directories for the agents, outside any repository")  # fmt: skip
    running.add_argument("--claude", default="claude")
    running.add_argument("--token-file", type=Path, default=Path(DEFAULT_TOKEN_FILE),
                         help="Claude Code token for full-* setups (sandbox containers)")  # fmt: skip
    running.add_argument("--force", action="store_true", help="rerun finished runs")
    running.add_argument("--grade", type=GradeMode, choices=list(GradeMode), default=GradeMode.FAST_FORWARD,
                         help="'both' also grades a copy of the end state the natural way (other_grade)")  # fmt: skip
    running.set_defaults(handler=cmd_run)

    reporting = commands.add_parser("report", help="leaderboard, Markdown tables and an HTML report")
    reporting.add_argument("runs", nargs="+", type=Path, help="run directories or exported results.json files")
    reporting.add_argument("--html", type=Path)
    reporting.add_argument("--markdown", type=Path)
    reporting.add_argument("--json", type=Path, help="export the rescored results (reloadable)")
    reporting.add_argument("--title", default="chronobox-ops results")
    reporting.set_defaults(handler=cmd_report)
    return root


def main() -> None:
    args = parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()

"""The ``chronobox-ops handoff`` command line."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from chronobox import Profile

from chronobox_ops.handoff.grid import HEAVY_JOBS, finished, one, regrade, validate
from chronobox_ops.handoff.runs import MAX_USD, SETUPS, TASK_NAMES, Options, Production, Run
from chronobox_ops.handoff.staging import serve_staging
from chronobox_ops.handoff.summary import summary

DESCRIPTION = "Hand-off mode: the agent works on a staging copy and hands over a script, graded on production."


def parser() -> argparse.ArgumentParser:
    """The ``chronobox-ops handoff`` command line."""
    root = argparse.ArgumentParser(prog="chronobox-ops handoff", description=DESCRIPTION)
    # `serve` is the harness's own staging server: left out of the listed commands.
    commands = root.add_subparsers(dest="command", required=True, metavar="{run,regrade,validate,summary}")
    serving = commands.add_parser("serve")
    serving.add_argument("--task", required=True)
    serving.add_argument("--profile", type=Profile, required=True)
    serving.add_argument("--review", default="", help="the reviewer command of verify_script")
    serving.add_argument("--socket", type=Path, required=True)
    serving.add_argument("--root", type=Path, required=True)
    serving.add_argument("--exports", type=Path, required=True)
    running = commands.add_parser("run", help="agents hand over scripts, graded on production")
    running.add_argument("--tasks", nargs="+", default=list(TASK_NAMES))
    running.add_argument("--setups", nargs="+", choices=SETUPS, default=list(SETUPS))
    running.add_argument("--seeds", type=int, default=3)
    running.add_argument("--first-seed", type=int, default=0)
    running.add_argument("--model", default="claude-sonnet-5-5")
    running.add_argument("--max-usd", type=float, default=MAX_USD, help="the agent's budget per run")
    running.add_argument("--out", type=Path, required=True)
    running.add_argument("--force", action="store_true", help="rerun runs that already finished")
    # Finished runs are skipped by default, as in `chronobox-ops run`; kept for older command lines.
    running.add_argument("--resume", action="store_true", help=argparse.SUPPRESS)
    regrading = commands.add_parser("regrade", help="grade saved hand-offs again")
    regrading.add_argument("--out", type=Path, required=True)
    regrading.add_argument("names", nargs="*", help="run folders (default: all in --out)")
    validating = commands.add_parser("validate", help="references succeed everywhere, naive ones fail")
    validating.add_argument("tasks", nargs="*", default=list(TASK_NAMES))
    validating.add_argument("--out", type=Path, required=True)
    for command in (running, regrading, validating):
        command.add_argument("--jobs", type=int, default=3)
        command.add_argument("--productions", nargs="+", type=Production, choices=list(Production),
                             default=list(Production))  # fmt: skip
    summarizing = commands.add_parser("summary", help="Markdown tables of results")
    summarizing.add_argument("folders", nargs="+", type=Path)
    return root


async def main(argv: list[str] | None = None) -> None:
    """Dispatch a ``chronobox-ops handoff`` command."""
    args = parser().parse_args(argv)
    if args.command == "serve":
        await serve_staging(args.task, args.profile, args.socket, args.root, args.exports, args.review)
        return
    if args.command == "summary":
        print(summary(args.folders))
        return
    productions = tuple(args.productions)
    if args.command == "validate":
        valid = await validate(args.tasks, args.out, productions, args.jobs)
        failing = sorted(task for task, ok in valid.items() if not ok)
        listed = f"; failing: {', '.join(failing)}" if failing else ""
        print(f"{len(valid) - len(failing)}/{len(valid)} tasks valid{listed}")
        if failing:
            sys.exit(1)
        return
    if args.command == "regrade":
        names = args.names or sorted(path.parent.name for path in args.out.glob("*/result.json"))
        await regrade(args.out, names, productions, args.jobs)
        return
    options = Options(args.out, args.model, args.jobs, productions, max_usd=args.max_usd)
    seeds = range(args.first_seed, args.first_seed + args.seeds)
    runs = [Run(task, setup, seed) for seed in seeds for task in args.tasks for setup in args.setups]
    if not args.force:
        runs = [run for run in runs if not finished(args.out / run.name)]
    slots, heavy = asyncio.Semaphore(args.jobs), asyncio.Semaphore(HEAVY_JOBS)
    await asyncio.gather(*(one(run, options, slots, heavy) for run in runs))

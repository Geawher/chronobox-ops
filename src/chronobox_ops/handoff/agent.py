"""The agent: headless Claude Code working on staging, and the hand-off it leaves."""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

from chronobox import Profile
from chronobox.handoff import (
    SCRIPT_FILE,
)
from chronobox.service import ServiceError, get_remote

from chronobox_ops import claude_trace
from chronobox_ops.handoff.runs import FILES, STAGING_SCRIPT, Options, Run
from chronobox_ops.harness.agents import SAME_MODEL_ENV, TOKEN_ENV, AgentOptions, prepare
from chronobox_ops.harness.setups import AgentKind, Setup

HANDOFF_FOLDER = "exports/handoff"


async def run_agent(run: Run, options: Options, socket: Path, folder: Path) -> dict[str, Any]:
    """Headless Claude Code with only the chronobox MCP tools of the setup (or, for full setups, its
    normal tools too, in a sandbox container); returns its trace summary."""
    if run.full:
        return await _run_full_agent(run, options, socket, folder)
    front_end = [sys.executable, "-m", "chronobox.server", "--connect", str(socket), "--profile", run.profile]
    if run.profile is Profile.CHRONOBOX:
        front_end.append("--handoff")
    home = folder / "home"
    home.mkdir(exist_ok=True)
    config = home / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"chronobox": {"command": front_end[0], "args": front_end[1:]}}}))
    command = [
        "claude", "-p", run.prompt(), "--setting-sources", "", "--permission-mode", "dontAsk",
        "--model", options.model, "--no-session-persistence", "--output-format", "stream-json", "--verbose",
        "--max-budget-usd", str(options.max_usd), "--mcp-config", str(config), "--strict-mcp-config",
        "--tools", "", "--allowedTools", "mcp__chronobox",
    ]  # fmt: skip
    env = {**os.environ, "MCP_TOOL_TIMEOUT": str(int(options.agent_timeout * 1000)), **SAME_MODEL_ENV}
    token = options.token_file.expanduser()
    if token.exists():
        env[TOKEN_ENV] = token.read_text().strip()
    started = time.monotonic()
    timed_out = False
    with (folder / "trace.jsonl").open("w") as trace, (folder / "agent.err").open("w") as errors:
        agent = await asyncio.create_subprocess_exec(
            *command, cwd=home, env=env, stdout=trace, stderr=errors, stdin=asyncio.subprocess.DEVNULL
        )
        try:
            await asyncio.wait_for(agent.wait(), options.agent_timeout)
        except TimeoutError:
            agent.kill()
            await agent.wait()
            timed_out = True
    summary = claude_trace.summarize(folder / "trace.jsonl")
    return {**summary, "agent_timeout": timed_out, "agent_s": round(time.monotonic() - started)}


async def _run_full_agent(run: Run, options: Options, socket: Path, folder: Path) -> dict[str, Any]:
    """Normal Claude Code in a sandbox container, reaching staging through the chronobox MCP over
    HTTP (with ``verify_script``), launched as the live benchmark's full setups are."""
    home = (folder / "home").resolve()  # docker mounts only absolute paths
    home.mkdir(exist_ok=True)
    setup = Setup(run.setup, AgentKind.FULL, run.profile)
    agent_options = AgentOptions(model=options.model, max_usd=options.max_usd, token_file=options.token_file)
    launch = await prepare(setup, run.prompt(), socket, home, agent_options, handoff=run.profile is Profile.CHRONOBOX)
    launch.env["MCP_TOOL_TIMEOUT"] = str(int(options.agent_timeout * 1000))
    started = time.monotonic()
    timed_out = False
    try:
        with (folder / "trace.jsonl").open("w") as trace, (folder / "agent.err").open("w") as errors:
            agent = await asyncio.create_subprocess_exec(
                *launch.command, cwd=home, env=launch.env, stdout=trace, stderr=errors, stdin=asyncio.subprocess.DEVNULL
            )
            try:
                await asyncio.wait_for(agent.wait(), options.agent_timeout)
            except TimeoutError:
                agent.kill()
                await agent.wait()
                timed_out = True
    finally:
        for cleanup in launch.cleanup:
            await cleanup()
    summary = claude_trace.summarize(folder / "trace.jsonl")
    return {**summary, "agent_timeout": timed_out, "agent_s": round(time.monotonic() - started)}


async def collect(run: Run, socket: Path, folder: Path) -> dict[str, str]:
    """The hand-off's scripts by file name: the verified export with chronobox, else the file on staging."""
    if run.profile is Profile.CHRONOBOX:
        saved = folder / HANDOFF_FOLDER
        return {name: read_text(saved / name) for name in FILES if read_text(saved / name).strip()}
    try:
        archive = await get_remote(socket, "main", STAGING_SCRIPT)
    except ServiceError:
        return {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        member = tar.extractfile(tar.getmembers()[0])
        text = member.read().decode(errors="replace") if member else ""
    return {SCRIPT_FILE: text} if text.strip() else {}


def read_text(path: Path) -> str:
    """A file's text, empty if it does not exist."""
    return path.read_text(errors="replace") if path.is_file() else ""

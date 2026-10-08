"""Launch a headless Claude Code for one run, and read what it did.

- single-shot: no tools and no MCP; its reply's last bash block is the submission;
- MCP-only: its built-in tools are disabled; its only tools are the chronobox MCP
  tools, through a stdio front end connected to the run's machine;
- full: normal Claude Code (explicit allow-list of its tools, internet) in a sandbox
  container that sees only an empty work directory; it reaches the machine through
  the chronobox MCP over HTTP on the docker bridge, behind a secret URL path. The
  token comes from a file the user saved for benchmark runs and is passed as an
  environment variable, never written to run directories.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chronobox_ops import claude_trace
from chronobox_ops.harness.setups import AgentKind, Setup

MCP_TOOL_TIMEOUT_MS = "1800000"
AGENT_IMAGE = "chronobox-ops-agent:latest"
DOCKER_HOST_IP = "172.17.0.1"
CONTAINER_MEMORY = "2g"
FRONT_END_WAIT_TRIES = 100
FULL_ACCESS_TOOLS = (
    "Bash", "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "LS", "WebSearch", "WebFetch",
    "Task", "TodoWrite", "NotebookEdit", "mcp__chronobox",
)  # fmt: skip
_BASH_BLOCK = re.compile(r"```(?:bash|sh|shell)?\s*\n(.*?)```", re.S)


@dataclass(frozen=True)
class AgentOptions:
    model: str
    max_usd: float
    claude: str = "claude"
    token_file: Path = Path("~/.config/chronobox/claude-token")


@dataclass
class AgentLaunch:
    command: list[str]
    env: dict[str, str]
    cleanup: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    helpers: list[asyncio.subprocess.Process] = field(default_factory=list)


def base_claude_args(prompt: str, options: AgentOptions) -> list[str]:
    return [
        "-p", prompt, "--setting-sources", "", "--permission-mode", "dontAsk",
        "--model", options.model, "--no-session-persistence",
        "--output-format", "stream-json", "--verbose", "--max-budget-usd", str(options.max_usd),
    ]  # fmt: skip


async def prepare(setup: Setup, prompt: str, machine_socket: Path, home: Path, options: AgentOptions) -> AgentLaunch:
    """Everything needed to start the agent for ``setup``; ``home`` is its empty working directory."""
    env = {**os.environ, "MCP_TOOL_TIMEOUT": MCP_TOOL_TIMEOUT_MS}
    args = base_claude_args(prompt, options)
    if setup.kind is AgentKind.SINGLE_SHOT:
        return AgentLaunch([options.claude, *args, "--strict-mcp-config", "--tools", ""], env)
    front_end = _front_end_args(setup, machine_socket)
    if setup.kind is AgentKind.MCP_ONLY:
        config = home / "mcp.json"
        config.write_text(json.dumps({"mcpServers": {"chronobox": {"command": sys.executable, "args": front_end}}}))
        command = [options.claude, *args, "--mcp-config", str(config), "--strict-mcp-config",
                   "--tools", "", "--allowedTools", "mcp__chronobox"]  # fmt: skip
        return AgentLaunch(command, env)
    return await _prepare_full(args, front_end, home, env, options)


async def _prepare_full(
    args: list[str], front_end: list[str], home: Path, env: dict[str, str], options: AgentOptions
) -> AgentLaunch:
    port, path_secret = _free_port(DOCKER_HOST_IP), secrets.token_hex(16)
    server = await asyncio.create_subprocess_exec(
        sys.executable, *front_end, "--http", f"{DOCKER_HOST_IP}:{port}", "--http-token", path_secret,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    await _wait_for_port(DOCKER_HOST_IP, port)
    work = home / "work"
    work.mkdir()
    url = f"http://{DOCKER_HOST_IP}:{port}/mcp/{path_secret}"
    (work / "mcp.json").write_text(json.dumps({"mcpServers": {"chronobox": {"type": "http", "url": url}}}))
    env["CLAUDE_CODE_OAUTH_TOKEN"] = options.token_file.expanduser().read_text().strip()
    container = f"cbx-{secrets.token_hex(5)}"
    binary = os.path.realpath(shutil.which(options.claude) or options.claude)
    docker = [
        "docker", "run", "--rm", "--name", container, "-u", f"{os.getuid()}:{os.getgid()}",
        "--memory", CONTAINER_MEMORY, "-e", "CLAUDE_CODE_OAUTH_TOKEN", "-e", "MCP_TOOL_TIMEOUT",
        "-e", "HOME=/home/agent", "-v", f"{work}:/work", "-w", "/work",
        "-v", f"{binary}:/usr/local/bin/claude:ro", AGENT_IMAGE,
        "claude", *args, "--mcp-config", "/work/mcp.json", "--strict-mcp-config",
        "--allowedTools", *FULL_ACCESS_TOOLS,
    ]  # fmt: skip

    async def remove_container() -> None:
        stop = await asyncio.create_subprocess_exec(
            "sg", "docker", "-c", f"docker rm -f {container}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
        await stop.wait()

    async def stop_front_end() -> None:
        if server.returncode is None:
            server.kill()
            await server.wait()

    return AgentLaunch(
        ["sg", "docker", "-c", shlex.join(docker)], env, cleanup=[remove_container, stop_front_end], helpers=[server]
    )


def _front_end_args(setup: Setup, machine_socket: Path) -> list[str]:
    args = ["-m", "chronobox.server", "--connect", str(machine_socket), "--profile", setup.profile.value]
    return [*args, "--guard"] if setup.guard else args


def _free_port(host: str) -> int:
    with socket.socket() as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


async def _wait_for_port(host: str, port: int) -> None:
    for _ in range(FRONT_END_WAIT_TRIES):
        try:
            _, writer = await asyncio.open_connection(host, port)
        except OSError:
            await asyncio.sleep(0.2)
            continue
        writer.close()
        return
    raise RuntimeError(f"MCP front end did not listen on {host}:{port}")


def summarize_trace(path: Path) -> dict[str, Any]:
    return claude_trace.summarize(path)


def extract_script(reply: str) -> str | None:
    """The last ```bash block of a single-shot reply."""
    blocks = _BASH_BLOCK.findall(reply)
    return blocks[-1].strip() if blocks else None

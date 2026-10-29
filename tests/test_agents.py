import json
from dataclasses import replace
from pathlib import Path

import pytest

from chronobox_ops.claude_trace import summarize
from chronobox_ops.harness.agents import AgentOptions, extract_script, prepare
from chronobox_ops.harness.setups import SETUPS

OPTIONS = AgentOptions(model="haiku", max_usd=1.0, token_file=Path("/nonexistent/claude-token"))


def test_extract_script_takes_the_last_bash_block() -> None:
    """The script is the last bash block of the reply; a reply without one gives None."""
    reply = "plan\n```bash\necho one\n```\nfinal\n```bash\necho two\nls\n```\n"
    assert extract_script(reply) == "echo two\nls"
    assert extract_script("no code here") is None


def test_summarize_trace_counts_tools_and_reads_the_final_reply(tmp_path: Path) -> None:
    """A trace summary counts chronobox tool calls by short name and reads the cost and final reply."""

    def tool_call(name: str) -> dict:
        """An assistant event calling tool ``name``."""
        return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": {}}]}}

    events = [
        tool_call("mcp__chronobox__run"),
        tool_call("mcp__chronobox__fork"),
        {"type": "result", "total_cost_usd": 0.05, "num_turns": 3, "result": "done", "is_error": False},
    ]
    trace = tmp_path / "trace.jsonl"
    trace.write_text("\n".join(json.dumps(event) for event in events))
    summary = summarize(trace)
    assert summary["tools"] == {"run": 1, "fork": 1}
    assert summary["cost_usd"] == 0.05 and summary["reply"] == "done"


async def test_single_shot_gets_no_tools_and_no_mcp(tmp_path: Path) -> None:
    """The single-shot agent gets an empty tool list and no MCP config."""
    launch = await prepare(SETUPS["single-shot"], "task", tmp_path / "m.sock", tmp_path, OPTIONS)
    assert "--mcp-config" not in launch.command
    assert launch.command[launch.command.index("--tools") + 1] == ""


async def test_mcp_only_agent_gets_only_chronobox_tools_for_its_setup(tmp_path: Path) -> None:
    """The MCP-only agent may use only chronobox tools, served with its setup's profile."""
    launch = await prepare(SETUPS["chronobox"], "task", tmp_path / "m.sock", tmp_path, OPTIONS)
    assert launch.command[launch.command.index("--allowedTools") + 1] == "mcp__chronobox"
    config = json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]["chronobox"]
    assert config["args"][-2:] == ["--profile", "chronobox"]


async def test_every_setup_uses_the_saved_token_when_there_is_one(tmp_path: Path) -> None:
    """A saved token file is passed to the agent as its OAuth token."""
    token = tmp_path / "token"
    token.write_text("sk-test\n")
    launch = await prepare(SETUPS["alone"], "task", tmp_path / "m.sock", tmp_path, replace(OPTIONS, token_file=token))
    assert launch.env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-test"


async def test_full_setups_need_the_token(tmp_path: Path) -> None:
    """A full-access setup without a saved token fails to launch."""
    with pytest.raises(RuntimeError, match="token"):
        await prepare(SETUPS["full-alone"], "task", tmp_path / "m.sock", tmp_path, OPTIONS)

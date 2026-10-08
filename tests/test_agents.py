import json
from pathlib import Path

from chronobox_ops.harness.agents import AgentOptions, extract_script, prepare, summarize_trace
from chronobox_ops.harness.setups import SETUPS

OPTIONS = AgentOptions(model="haiku", max_usd=1.0)


def test_extract_script_takes_the_last_bash_block() -> None:
    reply = "plan\n```bash\necho one\n```\nfinal\n```bash\necho two\nls\n```\n"
    assert extract_script(reply) == "echo two\nls"
    assert extract_script("no code here") is None


def test_summarize_trace_counts_tools_and_reads_the_final_reply(tmp_path: Path) -> None:
    events = [
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "mcp__chronobox__run", "input": {}}]},
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "mcp__chronobox__fork", "input": {}}]},
        },
        {"type": "result", "total_cost_usd": 0.05, "num_turns": 3, "result": "done", "is_error": False},
    ]
    trace = tmp_path / "trace.jsonl"
    trace.write_text("\n".join(json.dumps(event) for event in events))
    summary = summarize_trace(trace)
    assert summary["tools"] == {"run": 1, "fork": 1}
    assert summary["cost_usd"] == 0.05 and summary["reply"] == "done"


async def test_single_shot_gets_no_tools_and_no_mcp(tmp_path: Path) -> None:
    launch = await prepare(SETUPS["single-shot"], "task", tmp_path / "m.sock", tmp_path, OPTIONS)
    assert "--mcp-config" not in launch.command
    assert launch.command[launch.command.index("--tools") + 1] == ""


async def test_mcp_only_agent_gets_only_chronobox_tools_for_its_setup(tmp_path: Path) -> None:
    launch = await prepare(SETUPS["guard"], "task", tmp_path / "m.sock", tmp_path, OPTIONS)
    assert launch.command[launch.command.index("--allowedTools") + 1] == "mcp__chronobox"
    config = json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]["chronobox"]
    assert "--guard" in config["args"] and "chronobox" in config["args"]

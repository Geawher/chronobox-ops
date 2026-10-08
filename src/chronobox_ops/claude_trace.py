"""Read Claude Code's ``--output-format stream-json`` traces."""

from __future__ import annotations

import contextlib
import json
from collections import Counter
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path
from typing import Any

MCP_PREFIX = "mcp__chronobox__"
TEXT_CHARS = 600
RESULT_CHARS = 500


class EventType(StrEnum):
    ASSISTANT = "assistant"
    USER = "user"
    RESULT = "result"


class PartType(StrEnum):
    TEXT = "text"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"


def events(path: Path) -> Iterator[dict[str, Any]]:
    for line in path.read_text(errors="replace").splitlines():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue  # partial last line of an interrupted run


def summarize(path: Path) -> dict[str, Any]:
    """Tool calls (chronobox prefix removed), cost, turns and the final reply."""
    tools: list[str] = []
    final: dict[str, Any] = {}
    for event in events(path):
        if event.get("type") == EventType.RESULT:
            final = event
        elif event.get("type") == EventType.ASSISTANT:
            tools += [
                part["name"].removeprefix(MCP_PREFIX)
                for part in event.get("message", {}).get("content", [])
                if part.get("type") == PartType.TOOL_USE
            ]
    return {
        "tool_calls": len(tools),
        "tools": dict(Counter(tools)),
        "cost_usd": final.get("total_cost_usd"),
        "turns": final.get("num_turns"),
        "agent_error": final.get("is_error", not final),
        "reply": final.get("result") or "",
    }


def transcript(path: Path) -> str:
    """A readable transcript: the agent's text, its tool calls and what they returned."""
    if not path.exists():
        return "(no trace)"
    lines: list[str] = []
    for event in events(path):
        content = event.get("message", {}).get("content", [])
        if event.get("type") == EventType.ASSISTANT:
            lines += [line for line in map(_assistant_line, content) if line]
        elif event.get("type") == EventType.USER:
            lines += [_tool_result_line(part) for part in content if _is_tool_result(part)]
    return "\n".join(lines)


def _is_tool_result(part: object) -> bool:
    return isinstance(part, dict) and part.get("type") == PartType.TOOL_RESULT


def _assistant_line(part: dict[str, Any]) -> str:
    if part.get("type") == PartType.TEXT and part["text"].strip():
        return "· " + part["text"].strip()[:TEXT_CHARS]
    if part.get("type") == PartType.TOOL_USE:
        return f"> {part['name'].removeprefix(MCP_PREFIX)} {json.dumps(part['input'])[:TEXT_CHARS]}"
    return ""


def _tool_result_line(part: dict[str, Any]) -> str:
    content = part.get("content")
    if isinstance(content, list):
        content = "".join(piece.get("text", "") for piece in content)
    with contextlib.suppress(TypeError, ValueError, AttributeError):  # not JSON: show it as it is
        content = json.loads(content).get("result", content)
    return "  " + str(content)[:RESULT_CHARS].replace("\n", "\n  ")

"""The agent setups a task can be run under, and what each one is told.

Every setup runs a fresh headless Claude Code. They differ in what it is given:

| Setup | Claude Code's own tools | Reaches the machine via | chronobox tools |
|---|---|---|---|
| ``single-shot`` | none | one submitted script, run once | – |
| ``alone`` | none | MCP ``run``/``wait`` | – |
| ``rollback`` | none | MCP | checkpoint, rollback (whole machine) |
| ``chronobox`` | none | MCP | + forks, reports, lessons |
| ``full-alone`` | all (sandbox container, internet) | MCP over HTTP | – |
| ``full-chronobox`` | all | MCP over HTTP | forks, reports, lessons |
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from chronobox import Profile


class AgentKind(StrEnum):
    SINGLE_SHOT = "single-shot"
    MCP_ONLY = "mcp-only"
    FULL = "full"


MCP_ONLY_CONTEXT = (
    "\n\nThis is a production machine with live traffic. You reach it only through the "
    "chronobox tools (`run` executes a shell command on it, as root); there is no internet "
    "access. Work carefully and verify that your change really works. When you are done, "
    "reply with a short summary of what you changed."
)
FULL_CONTEXT = (
    "\n\nThis is a production machine with live traffic. You reach it only through the "
    "chronobox tools (`run` executes a shell command on it, as root). Your own workspace, where "
    "your other tools run, is a separate sandbox with internet access; it is not the production "
    "machine. Work carefully and verify that your change really works. When you are done, reply "
    "with a short summary of what you changed."
)
SINGLE_SHOT_CONTEXT = (
    "\n\nThis is a production machine with live traffic. You cannot inspect or access it "
    "yourself. Write ONE bash script that performs the whole task; it will be run once, as "
    "root, on the machine while traffic continues, and its result will be checked afterwards. "
    "Reply with the script in a single ```bash code block."
)
CONTEXT = {
    AgentKind.SINGLE_SHOT: SINGLE_SHOT_CONTEXT,
    AgentKind.MCP_ONLY: MCP_ONLY_CONTEXT,
    AgentKind.FULL: FULL_CONTEXT,
}


@dataclass(frozen=True)
class Setup:
    name: str
    kind: AgentKind
    profile: Profile

    def prompt(self, task_prompt: str) -> str:
        """The task prompt followed by the context for this setup's kind of agent."""
        return task_prompt + CONTEXT[self.kind]


SETUPS: dict[str, Setup] = {
    setup.name: setup
    for setup in (
        Setup("single-shot", AgentKind.SINGLE_SHOT, Profile.ALONE),
        Setup("alone", AgentKind.MCP_ONLY, Profile.ALONE),
        Setup("rollback", AgentKind.MCP_ONLY, Profile.ROLLBACK),
        Setup("chronobox", AgentKind.MCP_ONLY, Profile.CHRONOBOX),
        Setup("full-alone", AgentKind.FULL, Profile.ALONE),
        Setup("full-chronobox", AgentKind.FULL, Profile.CHRONOBOX),
    )
}

"""code_with_cli_agent — hand a coding task to a host CLI agent over ACP.

The main agent calls this when the user asks for code written/edited in a real
repository. It resolves an installed coding CLI (Claude Code / Codex / OpenCode),
gets the working directory approved through the assistant's permission flow, and
runs the agent, streaming its work into the chat as a CodingSession surface.
"""

from typing import Annotated

from ag2 import Context, tool
from pydantic import Field

from assistant.coding import detect
from assistant.coding import session as session_mod
from assistant.hitl.base import Asker
from assistant.permissions import PermissionManager


async def code_with_cli_agent(
    directory: Annotated[
        str,
        Field(description="Absolute path to the repository/folder the agent should work in."),
    ],
    task: Annotated[
        str,
        Field(
            description="What to build or change, in plain language (e.g. 'add a /health endpoint to app.py')."
        ),
    ],
    context: Context,
    agent: Annotated[
        str,
        Field(
            description="Which CLI agent: 'claude', 'codex', or 'opencode'. Empty = first available."
        ),
    ] = "",
) -> str:
    """Use a locally installed CLI coding agent to write or edit code in a folder.

    Use ONLY when the user asks for actual code changes in a real repository. The
    user must approve the folder the first time. The agent's plan, edits, and diffs
    stream into the chat. Returns a summary of what changed. If no coding agent is
    installed, this says so — don't fall back to shell/code tools.
    """
    pm = context.dependencies.get(PermissionManager)
    asker = context.dependencies.get(Asker)
    return await session_mod.run_coding_session(
        context=context,
        directory=directory,
        task=task,
        agent=agent,
        pm=pm,
        asker=asker,
    )


async def list_coding_agents() -> str:
    """List CLI coding agents and whether each is available.

    Call this to check what's available before offering to write code in a repo.
    Reflects the host bridge when configured (e.g. in Docker), else the local host.
    """
    endpoint = detect.bridge_endpoint()
    if endpoint is not None:
        from assistant.coding import bridge_client

        try:
            inventory = await bridge_client.list_agents(endpoint)
        except Exception as exc:  # noqa: BLE001
            return (
                f"Can't reach the host coding bridge at {endpoint.host}:{endpoint.port} "
                f"({exc}). Start `ag2-assistant acp-bridge` on the host."
            )
        header = f"Coding agents via the host bridge ({endpoint.host}:{endpoint.port}):"
        agents = inventory
    else:
        header = "Coding agents on this host:"
        agents = detect.detect_agents()

    lines = [
        f"- {info.label} ({info.name}): {'available' if info.available else 'not installed'}"
        for info in agents
    ]
    return header + "\n" + "\n".join(lines)


code_with_cli_agent_tool = tool(code_with_cli_agent)
list_coding_agents_tool = tool(list_coding_agents)


def build_coding_tools() -> list:
    """The coding-agent tools for the agent's toolset."""
    return [code_with_cli_agent_tool, list_coding_agents_tool]

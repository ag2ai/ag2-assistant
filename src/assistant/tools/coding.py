"""code_with_cli_agent — hand a coding task to a host CLI agent over ACP.

The main agent calls this when the user asks for code written/edited in a real
repository. It resolves an installed coding CLI (Claude Code / Codex / OpenCode),
gets the working directory approved through the assistant's permission flow, and
runs the agent, streaming its work into the chat as a CodingSession surface.

Both tools are built by :func:`build_coding_tools`, which closes over where to
look for adapters (``Config.search_path``) and the host ACP bridge, if any — so
nothing here reads the process environment. :func:`build_coding_functions` returns
the same pair unwrapped, for callers that invoke them directly.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

from ag2 import Context, tool
from pydantic import Field

from assistant.coding import detect
from assistant.coding import session as session_mod
from assistant.hitl.base import Asker
from assistant.permissions import PermissionManager


def build_coding_functions(
    *,
    search_path: Sequence[Path] = (),
    bridge: "detect.BridgeEndpoint | None" = None,
    runner=None,
) -> list:
    """The two coding-agent tool functions, bound to this host's facts.

    ``runner`` is ``run_coding_session``'s ACP-run seam, passed through so a caller
    can drive the tool without spawning an adapter; ``None`` = the real ACP run.
    """

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
            search_path=search_path,
            bridge=bridge,
            runner=runner,
        )

    async def list_coding_agents() -> str:
        """List CLI coding agents and whether each is available.

        Call this to check what's available before offering to write code in a repo.
        Reflects the host bridge when configured (e.g. in Docker), else the local host.
        """
        if bridge is not None:
            from assistant.coding.bridge_client import BridgeClient

            try:
                inventory = await BridgeClient(bridge).list_agents()
            except Exception as exc:  # noqa: BLE001
                return (
                    f"Can't reach the host coding bridge at {bridge.host}:{bridge.port} "
                    f"({exc}). Start `ag2-assistant acp-bridge` on the host."
                )
            header = f"Coding agents via the host bridge ({bridge.host}:{bridge.port}):"
            agents = inventory
        else:
            header = "Coding agents on this host:"
            agents = detect.detect_agents(search_path)

        lines = [
            f"- {info.label} ({info.name}): {'available' if info.available else 'not installed'}"
            for info in agents
        ]
        return header + "\n" + "\n".join(lines)

    return [code_with_cli_agent, list_coding_agents]


def build_coding_tools(**kwargs) -> list:
    """The coding-agent tools for the agent's toolset (see build_coding_functions)."""
    return [tool(fn) for fn in build_coding_functions(**kwargs)]

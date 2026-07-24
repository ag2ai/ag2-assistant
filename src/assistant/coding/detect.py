"""Detect coding-agent CLIs installed on the host.

Each supported agent has an ACP adapter that must be on ``PATH``:

  - ``claude`` → ``claude-agent-acp`` (the ``@agentclientprotocol/claude-agent-acp`` bin)
  - ``codex``  → ``codex-acp`` (the ``@agentclientprotocol/codex-acp`` bin)
  - ``opencode`` → ``opencode acp`` (the ``opencode`` bin, ``acp`` subcommand)

We only report what is actually present; nothing is launched. Auth is the CLI's
own on-disk login — we never inject keys.
"""

import os
import shutil
from dataclasses import dataclass

from assistant.coding.bridge_protocol import DEFAULT_PORT

# name → (executable to look up on PATH, full launch command)
_KNOWN: dict[str, tuple[str, list[str]]] = {
    "claude": ("claude-agent-acp", ["claude-agent-acp"]),
    "codex": ("codex-acp", ["codex-acp"]),
    "opencode": ("opencode", ["opencode", "acp"]),
}

_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
}


@dataclass(frozen=True)
class AgentInfo:
    """A known coding agent and whether its ACP adapter is on this host."""

    name: str  # short id: "claude" | "codex" | "opencode"
    label: str  # human name, e.g. "Claude Code"
    command: list[str]  # launch command for the ACP adapter
    available: bool  # the adapter executable resolves on PATH
    path: str | None  # resolved executable path, or None


def detect_agents() -> list[AgentInfo]:
    """All known agents, each flagged with its host availability."""
    agents: list[AgentInfo] = []
    for name, (executable, command) in _KNOWN.items():
        path = shutil.which(executable)
        agents.append(
            AgentInfo(
                name=name,
                label=_LABELS[name],
                command=list(command),
                available=path is not None,
                path=path,
            )
        )
    return agents


def available_agents() -> list[AgentInfo]:
    """Only the agents whose adapter is present on this host."""
    return [a for a in detect_agents() if a.available]


def pick(agents: list[AgentInfo], name: str = "") -> AgentInfo | None:
    """From an inventory, choose the named available agent, or the first available
    when ``name`` is empty. Returns ``None`` if none matches. Works for any source
    (local ``detect_agents`` or the host bridge's ``list``)."""
    available = [a for a in agents if a.available]
    if not name:
        return available[0] if available else None
    return next((a for a in available if a.name == name), None)


def resolve_agent(name: str = "") -> AgentInfo | None:
    """Pick a locally-available agent (see :func:`pick`), from the host's PATH."""
    return pick(available_agents(), name)


@dataclass(frozen=True)
class BridgeEndpoint:
    """A host ACP bridge to reach instead of spawning agents locally."""

    host: str
    port: int
    token: str = ""


def bridge_endpoint() -> "BridgeEndpoint | None":
    """The configured host bridge, from env, or ``None`` for local subprocess mode.

    ``AG2ASSISTANT_ACP_BRIDGE`` is ``host[:port]`` (e.g. ``host.docker.internal:8801``);
    ``AG2ASSISTANT_ACP_BRIDGE_TOKEN`` is the optional shared secret.
    """
    raw = os.environ.get("AG2ASSISTANT_ACP_BRIDGE", "").strip()
    if not raw:
        return None
    host, sep, port = raw.rpartition(":")
    if not sep:  # no ":" — bare host
        host, port = raw, str(DEFAULT_PORT)
    try:
        port_num = int(port)
    except ValueError:
        port_num = DEFAULT_PORT
    return BridgeEndpoint(
        host=host or "127.0.0.1",
        port=port_num,
        token=os.environ.get("AG2ASSISTANT_ACP_BRIDGE_TOKEN", "").strip(),
    )

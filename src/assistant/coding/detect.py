"""Detect coding-agent CLIs installed on the host.

Each supported agent has an ACP adapter that must be on ``PATH``:

  - ``claude`` → ``claude-agent-acp`` (the ``@agentclientprotocol/claude-agent-acp`` bin)
  - ``codex``  → ``codex-acp`` (the ``@agentclientprotocol/codex-acp`` bin)
  - ``opencode`` → ``opencode acp`` (the ``opencode`` bin, ``acp`` subcommand)

We only report what is actually present; nothing is launched. Auth is the CLI's
own on-disk login — we never inject keys.

Nothing here reads the process environment: the directories to search and the
host-bridge address arrive as arguments, resolved once at the boundary
(:func:`default_search_path` / :func:`bridge_endpoint` over an explicit env).
"""

import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

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
    command: list[str]  # launch command: the RESOLVED executable plus its arguments
    available: bool  # the adapter executable resolves on the search path
    path: str | None  # resolved executable path, or None


def _which(executable: str, search_path: Sequence[Path]) -> str | None:
    """Resolve an executable in the given directories only (never the process PATH)."""
    joined = os.pathsep.join(str(p) for p in search_path)
    return shutil.which(executable, path=joined) if joined else None


def detect_agents(search_path: Sequence[Path]) -> list[AgentInfo]:
    """All known agents, each flagged with its availability on this search path."""
    agents: list[AgentInfo] = []
    for name, (executable, command) in _KNOWN.items():
        path = _which(executable, search_path)
        agents.append(
            AgentInfo(
                name=name,
                label=_LABELS[name],
                # Spawn the executable we resolved, not the bare name: a subprocess
                # inheriting a different PATH must not launch a different adapter.
                command=[path, *command[1:]] if path else list(command),
                available=path is not None,
                path=path,
            )
        )
    return agents


def available_agents(search_path: Sequence[Path]) -> list[AgentInfo]:
    """Only the agents whose adapter is present on this search path."""
    return [a for a in detect_agents(search_path) if a.available]


def pick(agents: list[AgentInfo], name: str = "") -> AgentInfo | None:
    """From an inventory, choose the named available agent, or the first available
    when ``name`` is empty. Returns ``None`` if none matches. Works for any source
    (local ``detect_agents`` or the host bridge's ``list``)."""
    available = [a for a in agents if a.available]
    if not name:
        return available[0] if available else None
    return next((a for a in available if a.name == name), None)


def resolve_agent(name: str, search_path: Sequence[Path]) -> AgentInfo | None:
    """Pick an agent available on this search path (see :func:`pick`)."""
    return pick(available_agents(search_path), name)


def adapter_present(name: str, search_path: Sequence[Path]) -> bool:
    """Whether this agent's ACP adapter is on the search path."""
    known = _KNOWN.get(name)
    return known is not None and _which(known[0], search_path) is not None


def default_search_path(env: Mapping[str, str]) -> list[Path]:
    """``PATH`` from the given environment, as a list of directories. Boundary only."""
    return [Path(p) for p in env.get("PATH", "").split(os.pathsep) if p]


@dataclass(frozen=True)
class BridgeEndpoint:
    """A host ACP bridge to reach instead of spawning agents locally."""

    host: str
    port: int
    token: str = ""


def parse_bridge(raw: str, token: str = "") -> "BridgeEndpoint | None":
    """A ``host[:port]`` string as an endpoint, or ``None`` for local subprocess mode."""
    raw = (raw or "").strip()
    if not raw:
        return None
    host, sep, port = raw.rpartition(":")
    if not sep:  # no ":" — bare host
        host, port = raw, str(DEFAULT_PORT)
    try:
        port_num = int(port)
    except ValueError:
        port_num = DEFAULT_PORT
    return BridgeEndpoint(host=host or "127.0.0.1", port=port_num, token=(token or "").strip())


def bridge_endpoint(env: Mapping[str, str]) -> "BridgeEndpoint | None":
    """The host bridge configured in the given environment. Boundary only.

    ``AG2ASSISTANT_ACP_BRIDGE`` is ``host[:port]`` (e.g. ``host.docker.internal:8801``);
    ``AG2ASSISTANT_ACP_BRIDGE_TOKEN`` is the optional shared secret.
    """
    return parse_bridge(
        env.get("AG2ASSISTANT_ACP_BRIDGE", ""), env.get("AG2ASSISTANT_ACP_BRIDGE_TOKEN", "")
    )

"""Build an ``ag2.acp`` config for one coding run against a chosen host agent.

The working directory doubles as the ACP session ``cwd`` and the ``fs_root`` that
confines mediated file access, so an agent's writes stay under the folder the
user approved. Authentication is the CLI's own on-disk login — we deliberately
pass no provider API keys through ``env``.
"""

from ag2.acp import ACPConfig, ClaudeCodeConfig, CodexConfig, OpenCodeConfig
from ag2.acp.config import PermissionPolicy

from assistant.coding.detect import AgentInfo, BridgeEndpoint

# Per-prompt-turn ceiling (seconds). A coding turn can be long, but must not hang
# the chat forever; the ACP client cancels cooperatively, then hard-stops.
DEFAULT_TURN_TIMEOUT = 900.0

_PRESETS: dict[str, type[ACPConfig]] = {
    "claude": ClaudeCodeConfig,
    "codex": CodexConfig,
    "opencode": OpenCodeConfig,
}


def build_config(
    agent: AgentInfo,
    directory: str,
    *,
    permission_policy: PermissionPolicy = "ask",
    turn_timeout: float | None = DEFAULT_TURN_TIMEOUT,
    endpoint: BridgeEndpoint | None = None,
) -> ACPConfig:
    """Build the ACP config for ``agent`` scoped to ``directory``.

    Uses the agent's preset class when known (falling back to the base
    ``ACPConfig``), and pins ``command`` to what detection resolved. ``cwd ==
    fs_root == directory`` confines the agent to the approved folder.

    When ``endpoint`` is given, the config's ``_connect`` hook is set so
    ``ag2.acp`` opens the connection through the host bridge (TCP) instead of
    spawning a local subprocess; ``command`` is then a placeholder (the daemon
    owns the launch command on the host).
    """
    cls = _PRESETS.get(agent.name, ACPConfig)
    cfg = cls(
        command=list(agent.command) or ["__bridge__"],
        cwd=directory,
        fs_root=directory,
        permission_policy=permission_policy,
        turn_timeout=turn_timeout,
    )
    if endpoint is not None:
        from assistant.coding.bridge_client import BridgeClient

        cfg._connect = BridgeClient(endpoint).make_connector(agent.name, directory)
    return cfg

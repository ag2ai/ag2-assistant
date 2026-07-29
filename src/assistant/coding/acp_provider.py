"""Main-model provider: run the assistant's agent on Claude Code over ACP.

Unlike the coding-subagent flow (one directory per run), the MAIN agent's ACP
session is scoped to the profile workspace: ``cwd == fs_root == workspace_dir``
confines the CLI's mediated fs access to the folder the assistant already owns.
Auth is the CLI's own on-disk login — no keys are injected (subscription mode).

With ``expose_tools=True`` (ag2 default) the agent's full tool registry is
served to Claude Code over ag2's in-process HTTP MCP gateway; tools still
execute through the AG2 event stream, so permissions/HITL/middleware apply.
"""

from typing import TYPE_CHECKING

from ag2.acp import ACPConfig, ClaudeCodeConfig

from assistant.coding import bridge_client, detect

if TYPE_CHECKING:
    from assistant.config import Config

# Per-prompt-turn ceiling (seconds). A main-model turn contains Claude Code's
# whole inner tool loop, so it dwarfs a single API call; the silence watchdog
# observers cover wedges below this ceiling.
DEFAULT_TURN_TIMEOUT = 1800.0


def build_model_config(
    config: "Config", model: str | None = None, options: dict | None = None
) -> ACPConfig:
    """The ``ModelConfig`` driving Claude Code as the assistant's main model.

    ``options`` (the llm-config entry's free-form Advanced object) merges last
    into the constructor kwargs, so any ``ACPConfig`` field — ``turn_timeout``,
    ``allow_terminal``, even ``command`` — can be overridden per entry.
    """
    workspace = str(config.workspace_dir)
    endpoint = detect.bridge_endpoint()
    kwargs: dict = {
        "command": ["claude-agent-acp"],
        "cwd": workspace,
        "fs_root": workspace,
        "permission_policy": "ask",
        "turn_timeout": DEFAULT_TURN_TIMEOUT,
        **({"model": model} if model else {}),
        **(options or {}),
    }
    if model:
        # ACPConfig.model is response metadata only — it is NOT sent to the
        # adapter. claude-agent-acp picks its model from ANTHROPIC_MODEL
        # (alias "sonnet"/"opus"/"haiku" or a full versioned id both resolve;
        # unset → the CLI's own settings.json/default). An options env merges
        # over it, so a per-entry env can still override per key.
        kwargs["env"] = {"ANTHROPIC_MODEL": model, **(kwargs.get("env") or {})}
    if endpoint is not None:
        # Docker → host bridge: the CLI runs on the HOST, so ag2's MCP tool
        # gateway (bound to 127.0.0.1 inside this container) is unreachable —
        # tool exposure must stay off until the gateway is host-reachable.
        kwargs.setdefault("expose_tools", False)
    cfg = ClaudeCodeConfig(**kwargs)
    if endpoint is not None:
        cfg._connect = bridge_client.make_connector(endpoint, "claude", workspace)
    return cfg

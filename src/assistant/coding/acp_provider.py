"""Main-model providers: run the assistant's agent on a coding CLI over ACP.

Unlike the coding-subagent flow (one directory per run), the MAIN agent's ACP
session is scoped to the profile workspace: ``cwd == fs_root == workspace_dir``
confines the CLI's mediated fs access to the folder the assistant already owns.
Auth is each CLI's own on-disk login — no keys are injected (subscription mode).

With ``expose_tools=True`` (ag2 default) the agent's full tool registry is
served to the CLI agent over ag2's in-process HTTP MCP gateway; tools still
execute through the AG2 event stream, so permissions/HITL/middleware apply.
Both supported adapters advertise ``mcpCapabilities.http`` (verified live:
claude-agent-acp, codex-acp 1.1.7), so the HTTP-only gateway is compatible.
"""

import json
import re
from typing import TYPE_CHECKING, Callable

from ag2.acp import ACPConfig, ClaudeCodeConfig, CodexConfig

from assistant.coding import bridge_client, detect

if TYPE_CHECKING:
    from assistant.config import Config

# Per-prompt-turn ceiling (seconds). A main-model turn contains the CLI agent's
# whole inner tool loop, so it dwarfs a single API call; the silence watchdog
# observers cover wedges below this ceiling.
DEFAULT_TURN_TIMEOUT = 1800.0

# codex-acp reports model ids as "name[effort]" (e.g. "gpt-5.6-sol[medium]") but
# the underlying Codex config wants the two split. Effort strings are not
# validated here — the adapter is the authority on what's legal.
_EFFORT_SUFFIX = re.compile(r"^(?P<name>.+?)\s*\[(?P<effort>[^\]]+)\]$")


def _claude_model_env(model: str) -> dict[str, str]:
    # claude-agent-acp picks its model from ANTHROPIC_MODEL (alias
    # "sonnet"/"opus"/"haiku" or a full versioned id; unset → the CLI's own
    # settings.json/default).
    return {"ANTHROPIC_MODEL": model}


def _codex_model_env(model: str) -> dict[str, str]:
    # codex-acp has no model env var of its own; CODEX_CONFIG is a JSON object
    # merged into the Codex session config (unset → the CLI's own default).
    m = _EFFORT_SUFFIX.match(model)
    cfg = {"model": m["name"].strip(), "model_reasoning_effort": m["effort"].strip()} if m else {"model": model}
    return {"CODEX_CONFIG": json.dumps(cfg)}


# agent name (as in detect._KNOWN) → (ACPConfig preset, model → env derivation).
# The presets carry the right launch command ("claude-agent-acp" / "codex-acp").
_AGENTS: dict[str, tuple[type[ACPConfig], Callable[[str], dict[str, str]]]] = {
    "claude": (ClaudeCodeConfig, _claude_model_env),
    "codex": (CodexConfig, _codex_model_env),
}


def build_model_config(
    config: "Config",
    agent: str = "claude",
    model: str | None = None,
    options: dict | None = None,
) -> ACPConfig:
    """The ``ModelConfig`` driving a coding CLI as the assistant's main model.

    ``options`` (the llm-config entry's free-form Advanced object) merges last
    into the constructor kwargs, so any ``ACPConfig`` field — ``turn_timeout``,
    ``allow_terminal``, even ``command`` — can be overridden per entry.
    """
    config_cls, model_env = _AGENTS[agent]
    workspace = str(config.workspace_dir)
    endpoint = detect.bridge_endpoint()
    kwargs: dict = {
        "cwd": workspace,
        "fs_root": workspace,
        "permission_policy": "ask",
        "turn_timeout": DEFAULT_TURN_TIMEOUT,
        **({"model": model} if model else {}),
        **(options or {}),
    }
    if model:
        # ACPConfig.model is response metadata only — it is NOT sent to the
        # adapter. Each adapter takes its model selection via env instead; an
        # options env merges over it, so a per-entry env still overrides per key.
        kwargs["env"] = {**model_env(model), **(kwargs.get("env") or {})}
    if endpoint is not None:
        # Docker → host bridge: the CLI runs on the HOST, so ag2's MCP tool
        # gateway (bound to 127.0.0.1 inside this container) is unreachable —
        # tool exposure must stay off until the gateway is host-reachable.
        kwargs.setdefault("expose_tools", False)
    cfg = config_cls(**kwargs)
    if endpoint is not None:
        cfg._connect = bridge_client.make_connector(endpoint, agent, workspace)
    return cfg

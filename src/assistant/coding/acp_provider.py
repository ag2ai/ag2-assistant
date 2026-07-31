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
from typing import TYPE_CHECKING

from ag2.acp import ACPConfig, ClaudeCodeConfig, CodexConfig

from assistant.coding import detect
from assistant.coding.bridge_client import BridgeClient

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


def _codex_session_config(model: str) -> str:
    """The ``CODEX_CONFIG`` JSON selecting the entry's model for codex-acp.

    ``CODEX_CONFIG`` is the adapter's documented runtime option: a JSON object
    merged into the Codex session config. The adapter lists model ids as
    ``name[effort]``, but the underlying config wants those as two fields.
    """
    m = _EFFORT_SUFFIX.match(model)
    if m is None:
        return json.dumps({"model": model})
    return json.dumps({"model": m["name"].strip(), "model_reasoning_effort": m["effort"].strip()})


def build_claude_config(
    config: "Config",
    model: str | None = None,
    options: dict | None = None,
    *,
    connector_factory=None,
) -> ACPConfig:
    """Claude Code as the assistant's main model.

    The model rides the adapter's ``ANTHROPIC_MODEL`` env var (aliases like
    "sonnet" or full versioned ids); unset → the CLI's own settings/default.
    """
    env = {"ANTHROPIC_MODEL": model} if model else None
    return _build(ClaudeCodeConfig, "claude", config, model, env, options, connector_factory)


def build_codex_config(
    config: "Config",
    model: str | None = None,
    options: dict | None = None,
    *,
    connector_factory=None,
) -> ACPConfig:
    """Codex as the assistant's main model.

    The model rides the adapter's ``CODEX_CONFIG`` env var (JSON merged into
    the Codex session config); unset → the CLI's own default.
    """
    env = {"CODEX_CONFIG": _codex_session_config(model)} if model else None
    return _build(CodexConfig, "codex", config, model, env, options, connector_factory)


# provider name → its builder, so callers dispatch without a name-to-function
# conditional (see agent.ACP_PROVIDERS).
BUILDERS = {"claude_code": build_claude_config, "codex": build_codex_config}


def _build(
    config_cls: type[ACPConfig],
    agent: str,
    config: "Config",
    model: str | None,
    model_env: dict[str, str] | None,
    options: dict | None,
    connector_factory=None,
) -> ACPConfig:
    """The shared assembly behind both builders.

    ``options`` (the llm-config entry's free-form Advanced object) merges last
    into the constructor kwargs, so any ``ACPConfig`` field — ``turn_timeout``,
    ``allow_terminal``, even ``command`` — can be overridden per entry. The
    presets carry the right launch command ("claude-agent-acp" / "codex-acp").
    """
    workspace = str(config.workspace_dir)
    endpoint = detect.parse_bridge(config.acp_bridge, config.acp_bridge_token)
    kwargs: dict = {
        "cwd": workspace,
        "fs_root": workspace,
        "permission_policy": "ask",
        "turn_timeout": DEFAULT_TURN_TIMEOUT,
        **({"model": model} if model else {}),
        **(options or {}),
    }
    if model_env:
        # ag2 never sends ACPConfig.model to the agent (response metadata only),
        # so the model rides each adapter's documented env interface instead; an
        # options env merges over it, so a per-entry env still overrides per key.
        kwargs["env"] = {**model_env, **(kwargs.get("env") or {})}
    if endpoint is not None:
        # Docker → host bridge: the CLI runs on the HOST, so ag2's MCP tool
        # gateway (bound to 127.0.0.1 inside this container) is unreachable —
        # tool exposure must stay off until the gateway is host-reachable.
        kwargs.setdefault("expose_tools", False)
    cfg = config_cls(**kwargs)
    if endpoint is not None:
        if connector_factory is None:
            cfg._connect = BridgeClient(endpoint).make_connector(agent, workspace)
        else:
            cfg._connect = connector_factory(endpoint, agent, workspace)
    return cfg

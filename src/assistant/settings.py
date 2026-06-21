"""User-adjustable settings persisted to ``~/.ag2assistant/settings.json``.

Currently just the realtime voice. Kept separate from `config` (which is
env/file/defaults, read-only at runtime) because these are toggled live from the
GUI / tools and must persist across restarts.

The voice provider (Gemini or OpenAI) and its voice catalogue live in
``voice_providers``; this module is just the per-provider persistence layer, so
each provider remembers its own selection across restarts and provider switches.
"""

import json
import re
import shlex

from assistant import voice_providers
from assistant.config import data_dir

_MCP_KEY = "mcp_servers"
_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def voice_provider() -> str:
    """The active realtime voice provider (persisted setting → env → default)."""
    return voice_providers.active_provider()


def get_voice_provider() -> str | None:
    """The raw persisted voice-provider choice (or None). Used by
    voice_providers.active_provider(); kept here so persistence lives in one place."""
    return _read().get("voice_provider")


def set_voice_provider(provider: str) -> bool:
    """Persist the realtime voice provider. Returns False for an unknown provider."""
    if provider not in voice_providers.names():
        return False
    data = _read()
    data["voice_provider"] = provider
    _write(data)
    return True


def get_llm() -> dict:
    """The UI-selected assistant {provider, model} (or {}). Layered over config."""
    v = _read().get("llm")
    return v if isinstance(v, dict) else {}


def set_llm(provider: str | None = None, model: str | None = None) -> None:
    """Persist the assistant provider and/or model (only the given fields)."""
    data = _read()
    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    if provider:
        llm["provider"] = provider
    if model:
        llm["model"] = model
    data["llm"] = llm
    _write(data)


def list_mcp_servers(*, include_env: bool = False) -> list[dict]:
    """Persisted MCP stdio server configs. Env values are hidden by default."""
    servers = _read().get(_MCP_KEY)
    if not isinstance(servers, list):
        return []
    out = []
    for raw in servers:
        try:
            server = _normalise_mcp_server(raw)
        except ValueError:
            continue
        if include_env:
            out.append(server)
        else:
            public = {k: v for k, v in server.items() if k != "env"}
            public["env_keys"] = sorted((server.get("env") or {}).keys())
            out.append(public)
    return out


def upsert_mcp_server(server: dict) -> dict:
    """Add or replace one MCP stdio server config."""
    clean = _normalise_mcp_server(server)
    data = _read()
    servers = []
    for raw in data.get(_MCP_KEY, []):
        try:
            existing = _normalise_mcp_server(raw)
        except ValueError:
            continue
        if existing["name"] != clean["name"]:
            servers.append(existing)
    servers.append(clean)
    data[_MCP_KEY] = sorted(servers, key=lambda s: s["name"].lower())
    _write(data)
    public = {k: v for k, v in clean.items() if k != "env"}
    public["env_keys"] = sorted((clean.get("env") or {}).keys())
    return public


def delete_mcp_server(name: str) -> bool:
    data = _read()
    before = list_mcp_servers(include_env=True)
    after = [s for s in before if s["name"] != name]
    if len(after) == len(before):
        return False
    data[_MCP_KEY] = after
    _write(data)
    return True


def voices_for(provider: str | None = None) -> dict[str, str]:
    """The voice catalogue (name → style) for a provider (default: the active one)."""
    return voice_providers.get(provider).voices


def _list_value(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def split_args(value) -> list[str]:
    """Parse a UI arg string or accept an existing arg list."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if not isinstance(value, str) or not value.strip():
        return []
    return shlex.split(value)


def parse_env(value) -> dict[str, str]:
    """Parse KEY=VALUE lines from the UI."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if str(k).strip()}
    env: dict[str, str] = {}
    if isinstance(value, str):
        for line in value.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            if key:
                env[key] = val.strip()
    return env


def _normalise_mcp_server(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("MCP server config must be an object")
    name = str(raw.get("name") or "").strip()
    command = str(raw.get("command") or "").strip()
    if not _MCP_NAME_RE.fullmatch(name):
        raise ValueError(
            "MCP server name must be 1-64 letters, numbers, dots, dashes or underscores"
        )
    if not command:
        raise ValueError("MCP server command is required")
    cwd = str(raw.get("cwd") or "").strip() or None
    return {
        "name": name,
        "enabled": bool(raw.get("enabled", True)),
        "command": command,
        "args": split_args(raw.get("args")),
        "env": parse_env(raw.get("env")),
        "cwd": cwd,
        "allowed_tools": _list_value(raw.get("allowed_tools")),
        "blocked_tools": _list_value(raw.get("blocked_tools")),
    }


def _path():
    return data_dir() / "settings.json"


def _read() -> dict:
    try:
        return json.loads(_path().read_text())
    except Exception:
        return {}


def _write(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def _voice_map(data: dict) -> dict:
    """The per-provider voice selections, e.g. ``{"gemini": "Puck"}``."""
    raw = data.get("voice")
    return raw if isinstance(raw, dict) else {}


def get_voice(provider: str | None = None) -> str:
    """The persisted voice for a provider (default: active), or its default voice."""
    p = voice_providers.get(provider)
    v = _voice_map(_read()).get(p.name)
    return v if v in p.voices else p.default_voice


def set_voice(name: str, provider: str | None = None) -> bool:
    """Persist the realtime voice for a provider. Returns False for an unknown voice."""
    p = voice_providers.get(provider)
    if name not in p.voices:
        return False
    data = _read()
    vmap = _voice_map(data)
    vmap[p.name] = name
    data["voice"] = vmap
    _write(data)
    return True

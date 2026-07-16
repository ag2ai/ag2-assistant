"""User-adjustable settings persisted to a profile's ``config.yaml``.

Per-profile persistence for things toggled live from the GUI / tools: the realtime
voice (per provider), the persisted voice provider, focus areas, and the MCP server
list. (The LLM provider/model is NOT here — it's the install-wide
named ``llm_configs`` store now, common across profiles.) These keys live at the top
level of the same ``config.yaml`` that carries the profile's Config overlay sections
(``llm``/``agent``/…); the read-modify-write here preserves those neighbouring
sections. Kept separate from `config` (env/file/defaults, read-only at runtime)
because these are changed at runtime and must persist across restarts.

The store is a :class:`Settings` instance bound to an explicit path — one per
profile. There is **no** global default path: callers get the store for the profile
they operate on via ``profile_settings(config.data_dir)``, so an agent changing its
voice or loading its MCP servers touches only its own profile.

The voice provider (Gemini or OpenAI) and its voice catalogue live in
``voice_providers``; this module is just the per-provider persistence layer, so
each provider remembers its own selection across restarts and provider switches.

Note: the install-level *onboarded* flag does NOT live here — it moved to the
profile registry (``assistant.profiles.is_onboarded`` / ``set_onboarded``); one
first-run flow can create several profiles, so the flag is install-level.
"""

import re
import shlex
from math import isfinite
from pathlib import Path

from assistant import voice_providers
from assistant.config import read_yaml, write_yaml

_MCP_KEY = "mcp_servers"
_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_FOCUS_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


class Settings:
    """Per-profile settings persisted to one ``settings.json`` file.

    Bound to an explicit path at construction — there is no global default, so
    each profile's runtime reads/writes only its own file.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    # --- persistence ---

    def _read(self) -> dict:
        return read_yaml(self._path)

    def _write(self, data: dict) -> None:
        write_yaml(self._path, data)

    # --- voice provider ---

    def voice_provider(self) -> str:
        """The active realtime voice provider (persisted setting → env → default)."""
        return voice_providers.active_provider(self.get_voice_provider())

    def get_voice_provider(self) -> str | None:
        """The raw persisted voice-provider choice (or None)."""
        return self._read().get("voice_provider")

    def set_voice_provider(self, provider: str) -> bool:
        """Persist the realtime voice provider. Returns False for an unknown provider."""
        if provider not in voice_providers.names():
            return False
        data = self._read()
        data["voice_provider"] = provider
        self._write(data)
        return True

    # --- focuses (per-profile persona attribute) ---

    def get_focuses(self) -> list[str]:
        """The user's chosen focus areas for THIS profile (lowercase slugs the
        client sends, e.g. ``["research", "coding"]``). Empty list if unset."""
        return _focus_list(self._read().get("focuses"))

    def set_focuses(self, focuses) -> list[str]:
        """Persist the focus areas for this profile. Accepts a list of short strings
        (or a comma-string); normalised to lowercase slugs, deduped, order kept.
        Returns the stored list."""
        clean = _focus_list(focuses)
        data = self._read()
        data["focuses"] = clean
        self._write(data)
        return clean

    # --- gateway ---

    def set_reply_timeout(self, seconds: float) -> float:
        """Persist this profile's total chat-turn timeout in seconds."""
        value = float(seconds)
        if not isfinite(value) or value <= 0:
            raise ValueError("Reply timeout must be greater than zero.")
        data = self._read()
        gateway = data.get("gateway") if isinstance(data.get("gateway"), dict) else {}
        gateway["reply_timeout_s"] = value
        data["gateway"] = gateway
        self._write(data)
        return value

    # --- MCP servers ---

    def list_mcp_servers(self, *, include_env: bool = False) -> list[dict]:
        """Persisted MCP stdio server configs. Env values are hidden by default."""
        servers = self._read().get(_MCP_KEY)
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

    def upsert_mcp_server(self, server: dict) -> dict:
        """Add or replace one MCP stdio server config."""
        clean = _normalise_mcp_server(server)
        data = self._read()
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
        self._write(data)
        public = {k: v for k, v in clean.items() if k != "env"}
        public["env_keys"] = sorted((clean.get("env") or {}).keys())
        return public

    def delete_mcp_server(self, name: str) -> bool:
        data = self._read()
        before = self.list_mcp_servers(include_env=True)
        after = [s for s in before if s["name"] != name]
        if len(after) == len(before):
            return False
        data[_MCP_KEY] = after
        self._write(data)
        return True

    # --- voice selection (per provider) ---

    def voices_for(self, provider: str | None = None) -> dict[str, str]:
        """The voice catalogue (name → style) for a provider (default: the active one)."""
        return voice_providers.get(provider or self.voice_provider()).voices

    def get_voice(self, provider: str | None = None) -> str:
        """The persisted voice for a provider (default: active), or its default voice."""
        p = voice_providers.get(provider or self.voice_provider())
        v = _voice_map(self._read()).get(p.name)
        return v if v in p.voices else p.default_voice

    def set_voice(self, name: str, provider: str | None = None) -> bool:
        """Persist the realtime voice for a provider. Returns False for an unknown voice."""
        p = voice_providers.get(provider or self.voice_provider())
        if name not in p.voices:
            return False
        data = self._read()
        vmap = _voice_map(data)
        vmap[p.name] = name
        data["voice"] = vmap
        self._write(data)
        return True


def profile_settings(data_dir) -> Settings:
    """The Settings store for a profile's data dir — backed by the profile's
    ``config.yaml``. Settings keys live at the top level of the same file as the
    Config overlay sections; the read-modify-write in ``_write`` preserves them."""
    return Settings(Path(data_dir) / "config.yaml")


# --- pure helpers (path-free; shared by validation and the Settings methods) ---


def _list_value(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _focus_list(value) -> list[str]:
    """Normalise focus areas to short lowercase slugs (dedup, keep order).

    Accepts a list or a comma-string. Each entry must be a short slug
    (``[a-z0-9_-]``, ≤32 chars) — anything else is dropped, so the persona line
    can never carry junk. Capped at 12 to keep the injected prompt line small."""
    out: list[str] = []
    for raw in _list_value(value):
        slug = str(raw).strip().lower()
        if _FOCUS_RE.fullmatch(slug) and slug not in out:
            out.append(slug)
    return out[:12]


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


def _voice_map(data: dict) -> dict:
    """The per-provider voice selections, e.g. ``{"gemini": "Puck"}``."""
    raw = data.get("voice")
    return raw if isinstance(raw, dict) else {}

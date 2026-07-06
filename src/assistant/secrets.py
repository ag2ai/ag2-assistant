"""API-key store, persisted to ``~/.ag2assistant/secrets.json`` with 0600 perms.

Keys are plaintext on disk (comparable to a ``.env`` file) — the gateway binds
127.0.0.1 only, the API never returns raw keys (only a set/last-4 hint), and keys
are never logged. Keys are loaded into ``os.environ`` so the existing provider
plumbing (`agent.model_config`, `voice_providers`) — which all read ``os.environ`` —
works unchanged.

Kept separate from `settings.py` (non-secret preferences) to signal sensitivity.
"""

import json
import os

from assistant.config import data_dir
from assistant.profiles import CHANNEL_TOKEN_ENV_NAMES

# key id → the env var that consumes it. LLM providers + GitHub (skills registry:
# AG2's SkillSearchToolkit reads GITHUB_TOKEN to raise the GitHub limit 60→5000/hr).
KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "github": "GITHUB_TOKEN",
}
OLLAMA_BASE_ENV = "OLLAMA_BASE_URL"  # our convention; model_config reads it
DEFAULT_OLLAMA_BASE = "http://localhost:11434"

# Channel bot tokens get the same treatment as provider keys: stored here, loaded
# into os.environ, editable from the UI, applied live. Keyed directly by env var
# name (the closed set from profiles). On disk they live under a ``channels`` sub-map
# so they don't collide with provider fields.
_CHANNELS_FIELD = "channels"


def _path():
    return data_dir() / "secrets.json"


def _read() -> dict:
    try:
        return json.loads(_path().read_text())
    except Exception:
        return {}


def _write(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))
    try:
        p.chmod(0o600)
    except Exception:
        pass


def set_key(provider: str, value: str) -> bool:
    """Set or clear (empty value) a provider's key / Ollama base URL. Returns False
    for an unknown provider. Applies to os.environ immediately (set on save, pop on
    clear) so the change takes effect live — and persists it."""
    provider = (provider or "").lower()
    if provider != "ollama" and provider not in KEY_ENV:
        return False
    field = "ollama_base_url" if provider == "ollama" else provider
    env = OLLAMA_BASE_ENV if provider == "ollama" else KEY_ENV[provider]
    value = (value or "").strip()
    data = _read()
    if value:
        data[field] = value
        os.environ[env] = value
    else:
        data.pop(field, None)
        os.environ.pop(env, None)
    _write(data)
    return True


def clear(provider: str) -> bool:
    return set_key(provider, "")


def set_channel_token(env_name: str, value: str) -> bool:
    """Set or clear (empty value) a channel bot token, keyed by its env var name
    (e.g. ``TELEGRAM_BOT_TOKEN``). Returns False for an env name outside the closed
    channel-token set. Mirrors ``set_key``: non-empty → save + os.environ set;
    empty/None → remove from file + os.environ pop. Applied live and persisted."""
    if env_name not in CHANNEL_TOKEN_ENV_NAMES:
        return False
    value = (value or "").strip()
    data = _read()
    chans = data.get(_CHANNELS_FIELD)
    if not isinstance(chans, dict):
        chans = {}
    if value:
        chans[env_name] = value
        os.environ[env_name] = value
    else:
        chans.pop(env_name, None)
        os.environ.pop(env_name, None)
    if chans:
        data[_CHANNELS_FIELD] = chans
    else:
        data.pop(_CHANNELS_FIELD, None)
    _write(data)
    return True


def _saved_channel_tokens(data: dict) -> dict:
    """The ``channels`` sub-map from the store (empty dict if absent/malformed)."""
    chans = data.get(_CHANNELS_FIELD)
    return chans if isinstance(chans, dict) else {}


def load_into_env() -> None:
    """Populate os.environ from saved secrets (overriding) so the provider plumbing
    and channels see UI-entered keys/tokens. Missing secrets leave any existing env
    value untouched (a token set only in the real env still applies)."""
    data = _read()
    for provider, env in KEY_ENV.items():
        if data.get(provider):
            os.environ[env] = data[provider]
    if data.get("ollama_base_url"):
        os.environ[OLLAMA_BASE_ENV] = data["ollama_base_url"]
    for env_name, value in _saved_channel_tokens(data).items():
        if env_name in CHANNEL_TOKEN_ENV_NAMES and value:
            os.environ[env_name] = value


def status() -> dict:
    """Per-provider presence + a last-4 hint (never the raw key). A key set in the
    real env (e.g. .env) also counts as present. Ollama reports its base URL."""
    data = _read()
    out = {}
    for provider, env in KEY_ENV.items():
        v = data.get(provider) or os.environ.get(env)
        out[provider] = {"set": bool(v), "hint": ("…" + v[-4:]) if v else ""}
    base = data.get("ollama_base_url") or os.environ.get(OLLAMA_BASE_ENV) or ""
    out["ollama"] = {"set": bool(base), "base_url": base or DEFAULT_OLLAMA_BASE}
    return out


def channel_token_status() -> dict:
    """Per-env-var presence for channel bot tokens (never the raw value). A token set
    only in the real env (e.g. ``.env``) also counts as present."""
    data = _read()
    saved = _saved_channel_tokens(data)
    return {
        env_name: bool(saved.get(env_name) or os.environ.get(env_name))
        for env_name in sorted(CHANNEL_TOKEN_ENV_NAMES)
    }

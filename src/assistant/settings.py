"""User-adjustable settings persisted to ``~/.agclaw/settings.json``.

Currently just the realtime voice. Kept separate from `config` (which is
env/file/defaults, read-only at runtime) because these are toggled live from the
GUI / tools and must persist across restarts.

The voice provider (Gemini or OpenAI) and its voice catalogue live in
``voice_providers``; this module is just the per-provider persistence layer, so
each provider remembers its own selection across restarts and provider switches.
"""

import json

from assistant import voice_providers
from assistant.config import data_dir


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


def voices_for(provider: str | None = None) -> dict[str, str]:
    """The voice catalogue (name → style) for a provider (default: the active one)."""
    return voice_providers.get(provider).voices


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
    """The per-provider voice selections, migrating the legacy flat string
    (``{"voice": "Puck"}``) to ``{"voice": {"gemini": "Puck"}}`` on read."""
    raw = data.get("voice")
    if isinstance(raw, str):
        return {"gemini": raw}
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

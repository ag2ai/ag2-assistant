"""API-key store, persisted to ``~/.ag2assistant/secrets.json`` with 0600 perms.

Holds the SECRET entities (named, reusable, value-unique API keys — see CONTEXT.md
"Secrets" and ADR 0005) plus the non-Secret singletons that keep their legacy
fields: channel bot tokens, the GitHub token, and the Ollama base URL. Keys are
plaintext on disk (comparable to a ``.env`` file) — the gateway binds 127.0.0.1
only, the API never returns raw keys (only a set/last-4 hint), and keys are never
logged. Each provider's DEFAULT Secret is loaded into ``os.environ`` so the
existing provider plumbing (``agent.model_config``, ``voice_providers``) works
unchanged; non-default Secrets are never env-loaded — they flow in-process to the
one config that references them.

Kept separate from `settings.py` (non-secret preferences) to signal sensitivity.
"""

import json
import os
from secrets import token_hex

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

# A Connection's own token(s), under a Connection-scoped key: {connection id:
# {ENV_NAME: value}}. Handed to that Connection's adapter explicitly, never exported
# to os.environ — one process can hold three Telegram tokens this way.
_CONNECTION_TOKENS_FIELD = "connection_tokens"


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


# ---- Secrets: named, reusable API keys (CONTEXT.md "Secrets", ADR 0005) --------
# A Secret is {id, name, value, provider?, default?} stored under a ``secrets``
# list. Unique by value across the store. The raw value never leaves this module
# except via secret_value() (in-process plumbing only) — every API-facing shape is
# the "safe view" {id, name, provider, default, hint}. At most one Secret per
# provider tag is the Default: it inherits the old shared-key role, including
# loading into os.environ (set on gain, popped on loss — mirroring set_key).

LLM_PROVIDERS = ("openai", "gemini", "anthropic")
PROVIDER_TITLE = {"openai": "OpenAI", "gemini": "Gemini", "anthropic": "Anthropic"}
_SECRETS_FIELD = "secrets"


class DuplicateValue(ValueError):
    """A create/update tried to store a key value another Secret already holds
    (Secrets are unique by value). Carries the existing Secret's safe view so the
    API layer can 409 with a pointer and the model form can snap to it."""

    def __init__(self, existing: dict):
        super().__init__(f"this key already exists as {existing['name']!r}")
        self.existing = existing


def _hint(value: str) -> str:
    return ("…" + value[-4:]) if value else ""


def _stored_secrets(data: dict) -> list[dict]:
    items = data.get(_SECRETS_FIELD)
    return [s for s in items if isinstance(s, dict)] if isinstance(items, list) else []


def _secret_view(s: dict) -> dict:
    """The safe, API-facing shape: everything but the raw value (a last-4 hint)."""
    return {
        "id": s.get("id", ""),
        "name": s.get("name", ""),
        "provider": s.get("provider", ""),
        "default": bool(s.get("default")),
        "hint": _hint(s.get("value", "") or ""),
    }


def _find_stored(items: list[dict], sid: str) -> dict | None:
    sid = (sid or "").strip()
    return next((s for s in items if s.get("id") == sid), None)


def list_secrets() -> list[dict]:
    """Every Secret as a safe view (no raw values), in insertion order."""
    return [_secret_view(s) for s in _stored_secrets(_read())]


def get_secret(sid: str) -> dict | None:
    """One Secret's safe view by id, or None."""
    s = _find_stored(_stored_secrets(_read()), sid)
    return _secret_view(s) if s else None


def secret_value(sid: str) -> str:
    """The raw key one Secret holds (empty string if unknown/blank id). In-process
    only — flows into provider kwargs and voice connects; never returned by any
    endpoint."""
    s = _find_stored(_stored_secrets(_read()), sid)
    return (s or {}).get("value", "") or ""


def find_secret_by_value(value: str) -> dict | None:
    """Safe view of the Secret holding exactly ``value``, or None — the lookup
    behind value-uniqueness (create rejects with it; the model form snaps to it)."""
    value = (value or "").strip()
    if not value:
        return None
    s = next((s for s in _stored_secrets(_read()) if s.get("value") == value), None)
    return _secret_view(s) if s else None


def _validate_fields(name: str, value: str, provider: str, default: bool) -> None:
    if not name:
        raise ValueError("secret name is required")
    if not value:
        raise ValueError("secret value is required")
    if provider and provider not in LLM_PROVIDERS:
        raise ValueError(f"provider must be one of {', '.join(LLM_PROVIDERS)} or empty")
    if default and not provider:
        raise ValueError("only a provider-tagged secret can be a default")


def _displace_default(items: list[dict], provider: str, keep_id: str) -> None:
    for s in items:
        if s.get("id") != keep_id and s.get("provider") == provider:
            s["default"] = False


def _sync_env(items: list[dict], provider: str, had_default: bool) -> None:
    """Align a provider's env var with its (new) Default: set when one exists, pop
    when a Default just went away. A provider that never had a Default is left
    alone, so an .env-only key survives unrelated Secret edits."""
    if provider not in LLM_PROVIDERS:
        return
    d = next((s for s in items if s.get("provider") == provider and s.get("default")), None)
    env = KEY_ENV[provider]
    if d and d.get("value"):
        os.environ[env] = d["value"]
    elif had_default:
        os.environ.pop(env, None)


def create_secret(name: str, value: str, provider: str = "", default: bool = False) -> dict:
    """Create a Secret and return its safe view. ValueError on bad input;
    DuplicateValue when another Secret already holds this exact value.
    ``default=True`` (requires a provider tag) makes it the provider's Default,
    displacing any current one and applying the value to os.environ."""
    name = (name or "").strip()
    value = (value or "").strip()
    provider = (provider or "").strip().lower()
    default = bool(default)
    _validate_fields(name, value, provider, default)
    data = _read()
    items = _stored_secrets(data)
    dup = next((s for s in items if s.get("value") == value), None)
    if dup:
        raise DuplicateValue(_secret_view(dup))
    entry = {
        "id": "s_" + token_hex(4),
        "name": name,
        "value": value,
        "provider": provider,
        "default": default,
    }
    if default:
        _displace_default(items, provider, entry["id"])
    items.append(entry)
    data[_SECRETS_FIELD] = items
    _write(data)
    if default:
        os.environ[KEY_ENV[provider]] = value
    return _secret_view(entry)


def update_secret(
    sid: str,
    *,
    name: str | None = None,
    value: str | None = None,
    provider: str | None = None,
    default: bool | None = None,
) -> dict:
    """Partially update a Secret (None leaves a field unchanged) and return its new
    safe view. KeyError for an unknown id; DuplicateValue when the new value
    collides with another Secret; ValueError on bad input (blank name/value,
    default without a tag). Untagging a Default drops its Default status. Rotating
    a Default's value re-syncs its env var — and re-keys every referencing model,
    which read through secret_value() live."""
    data = _read()
    items = _stored_secrets(data)
    entry = _find_stored(items, sid)
    if entry is None:
        raise KeyError(sid)
    new_name = entry.get("name", "") if name is None else (name or "").strip()
    new_value = entry.get("value", "") if value is None else (value or "").strip()
    new_provider = (
        entry.get("provider", "") if provider is None else (provider or "").strip().lower()
    )
    new_default = bool(entry.get("default")) if default is None else bool(default)
    if default is True and not new_provider:
        raise ValueError("only a provider-tagged secret can be a default")
    if not new_provider:
        new_default = False  # only a tagged Secret can stay a Default
    _validate_fields(new_name, new_value, new_provider, new_default)
    if new_value != entry.get("value"):
        dup = next(
            (s for s in items if s.get("value") == new_value and s.get("id") != entry["id"]), None
        )
        if dup:
            raise DuplicateValue(_secret_view(dup))
    old_provider = entry.get("provider", "")
    had_default = {
        p: any(s.get("provider") == p and s.get("default") for s in items)
        for p in {old_provider, new_provider}
        if p
    }
    if new_default:
        _displace_default(items, new_provider, entry["id"])
    entry.update(name=new_name, value=new_value, provider=new_provider, default=new_default)
    data[_SECRETS_FIELD] = items
    _write(data)
    for p, had in had_default.items():
        _sync_env(items, p, had)
    return _secret_view(entry)


def delete_secret(sid: str) -> bool:
    """Remove a Secret by id (False if unknown). Always allowed, even while
    referenced — configs pointing at it degrade down the resolution order (Default
    → env → none). Deleting a provider's Default pops its env var (mirroring the
    old shared-key clear)."""
    data = _read()
    items = _stored_secrets(data)
    entry = _find_stored(items, sid)
    if entry is None:
        return False
    items = [s for s in items if s.get("id") != entry["id"]]
    data[_SECRETS_FIELD] = items
    _write(data)
    if entry.get("default") and entry.get("provider") in LLM_PROVIDERS:
        os.environ.pop(KEY_ENV[entry["provider"]], None)
    return True


def default_secret(provider: str) -> dict | None:
    """Safe view of ``provider``'s Default Secret, or None."""
    provider = (provider or "").strip().lower()
    s = next(
        (s for s in _stored_secrets(_read()) if s.get("provider") == provider and s.get("default")),
        None,
    )
    return _secret_view(s) if s else None


def set_default(sid: str) -> bool:
    """Make this Secret its provider's Default (False for an unknown id or an
    untagged Secret). Displaces the current Default and applies to os.environ."""
    try:
        update_secret(sid, default=True)
        return True
    except (KeyError, ValueError):
        return False


def set_key(provider: str, value: str) -> bool:
    """Set or clear (empty value) a provider's install-wide key / Ollama base URL.
    For the LLM providers this upserts the provider's DEFAULT SECRET (the
    onboarding/integrations path): a value updates the current Default — or adopts
    the Secret already holding that exact value (unique by value), or creates one
    named after the provider; empty deletes the Default. GitHub and Ollama keep
    their legacy top-level fields. Applies to os.environ immediately; returns
    False for an unknown provider."""
    provider = (provider or "").lower()
    value = (value or "").strip()
    if provider in ("ollama", "github"):
        field = "ollama_base_url" if provider == "ollama" else "github"
        env = OLLAMA_BASE_ENV if provider == "ollama" else KEY_ENV["github"]
        data = _read()
        if value:
            data[field] = value
            os.environ[env] = value
        else:
            data.pop(field, None)
            os.environ.pop(env, None)
        _write(data)
        return True
    if provider not in LLM_PROVIDERS:
        return False
    current = default_secret(provider)
    if not value:
        if current:
            delete_secret(current["id"])
        else:
            os.environ.pop(KEY_ENV[provider], None)  # clear semantics match the old set_key
        return True
    existing = find_secret_by_value(value)
    if existing:
        update_secret(existing["id"], provider=provider, default=True)
    elif current:
        update_secret(current["id"], value=value)
    else:
        create_secret(PROVIDER_TITLE[provider], value, provider=provider, default=True)
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


def channel_token(env_name: str) -> str:
    """One channel token's raw value — the saved one, else the process env. The env
    is read as a seed for a first Connection only; nothing else consumes this."""
    return _saved_channel_tokens(_read()).get(env_name) or os.environ.get(env_name, "")


def _stored_connection_tokens(data: dict) -> dict:
    """The ``connection_tokens`` sub-map (empty dict if absent/malformed)."""
    held = data.get(_CONNECTION_TOKENS_FIELD)
    return held if isinstance(held, dict) else {}


def set_connection_tokens(cid: str, tokens: dict) -> None:
    """Merge token value(s) into one Connection's scoped store, keyed by env-var name;
    an empty value clears that token. Env name outside the channel set → ValueError."""
    unknown = set(tokens) - CHANNEL_TOKEN_ENV_NAMES
    if unknown:
        raise ValueError(f"unknown channel token(s): {', '.join(sorted(unknown))}")
    data = _read()
    all_held = _stored_connection_tokens(data)
    held = dict(all_held.get(cid) or {})
    for env_name, value in tokens.items():
        value = (value or "").strip()
        if value:
            held[env_name] = value
        else:
            held.pop(env_name, None)
    all_held[cid] = held
    data[_CONNECTION_TOKENS_FIELD] = all_held
    _write(data)


def clear_connection_tokens(cid: str) -> None:
    """Forget every token one Connection holds — it no longer exists."""
    data = _read()
    all_held = _stored_connection_tokens(data)
    if all_held.pop(cid, None) is None:
        return
    data[_CONNECTION_TOKENS_FIELD] = all_held
    _write(data)


def connection_tokens(cid: str) -> dict:
    """The raw token(s) one Connection holds, keyed by env-var name. In-process only —
    they flow into adapter construction and are never returned by an endpoint."""
    held = _stored_connection_tokens(_read()).get(cid)
    return dict(held) if isinstance(held, dict) else {}


def connection_token_status(cid: str, env_names) -> dict:
    """Per-token presence and a last-4 hint for one Connection (never a raw value)."""
    held = connection_tokens(cid)
    return {e: {"set": bool(held.get(e)), "hint": _hint(held.get(e) or "")} for e in env_names}


def _saved_channel_tokens(data: dict) -> dict:
    """The ``channels`` sub-map from the store (empty dict if absent/malformed)."""
    chans = data.get(_CHANNELS_FIELD)
    return chans if isinstance(chans, dict) else {}


def load_into_env() -> None:
    """Populate os.environ from saved secrets (overriding) so the provider plumbing
    and channels see UI-entered keys/tokens: each provider's DEFAULT SECRET, the
    GitHub token, the Ollama base URL, and channel tokens. A provider with no
    Default leaves any existing env value untouched (an .env-only key still
    applies as the last-resort fallback)."""
    data = _read()
    for s in _stored_secrets(data):
        prov = s.get("provider", "")
        if s.get("default") and prov in LLM_PROVIDERS and s.get("value"):
            os.environ[KEY_ENV[prov]] = s["value"]
    if data.get("github"):
        os.environ[KEY_ENV["github"]] = data["github"]
    if data.get("ollama_base_url"):
        os.environ[OLLAMA_BASE_ENV] = data["ollama_base_url"]
    for env_name, value in _saved_channel_tokens(data).items():
        if env_name in CHANNEL_TOKEN_ENV_NAMES and value:
            os.environ[env_name] = value


def status() -> dict:
    """Per-provider presence + a last-4 hint (never the raw key): the provider's
    Default Secret, else its env var (a key set only in .env still counts). GitHub
    keeps its legacy field; Ollama reports its base URL."""
    data = _read()
    out = {}
    for provider in LLM_PROVIDERS:
        d = next(
            (
                s
                for s in _stored_secrets(data)
                if s.get("provider") == provider and s.get("default")
            ),
            None,
        )
        v = (d or {}).get("value") or os.environ.get(KEY_ENV[provider])
        out[provider] = {"set": bool(v), "hint": _hint(v or "")}
    v = data.get("github") or os.environ.get(KEY_ENV["github"])
    out["github"] = {"set": bool(v), "hint": _hint(v or "")}
    base = data.get("ollama_base_url") or os.environ.get(OLLAMA_BASE_ENV) or ""
    out["ollama"] = {"set": bool(base), "base_url": base or DEFAULT_OLLAMA_BASE}
    return out


def migrate() -> None:
    """One-shot upgrade of a legacy secrets.json into the Secret-entity shape
    (idempotent — the presence of the ``secrets`` field is the done-marker).

    Legacy top-level provider keys become provider-named, tagged DEFAULT Secrets;
    ``llm_keys``/``live_keys`` entries become Secrets auto-named after their config
    ("<config name> key"), with the config re-pointed via ``set_secret_id``. Values
    dedupe into one Secret each; when a shared provider key is among the merged
    sources, the survivor keeps the provider name, tag, and Default badge. Keys for
    configs that no longer exist are dropped. Channels/github/ollama fields are
    untouched. Called from Gateway.start() before load_into_env()."""
    data = _read()
    if _SECRETS_FIELD in data:
        return
    # Lazy imports: both stores import assistant.secrets at module level.
    from assistant import live_configs, llm_configs  # local: import cycle (configs import secrets)

    items: list[dict] = []
    by_value: dict[str, dict] = {}

    def _mint(name: str, value: str, provider: str = "", default: bool = False) -> dict:
        s = {
            "id": "s_" + token_hex(4),
            "name": name,
            "value": value,
            "provider": provider,
            "default": default,
        }
        items.append(s)
        by_value[value] = s
        return s

    for provider in LLM_PROVIDERS:
        v = str(data.pop(provider, "") or "").strip()
        if not v:
            continue
        existing = by_value.get(v)
        if existing is None:
            _mint(PROVIDER_TITLE[provider], v, provider, True)
        elif not existing.get("provider"):
            # merged with an untagged secret → the provider identity wins
            existing.update(name=PROVIDER_TITLE[provider], provider=provider, default=True)
        # two provider fields with the same value: the first one keeps the tag

    def _adopt(legacy: dict, get_config, set_secret_id) -> None:
        for cid, v in legacy.items():
            v = str(v or "").strip()
            cfg = get_config(cid)
            if not v or cfg is None:
                continue  # orphaned key (its config is gone) — dropped
            s = by_value.get(v) or _mint(f"{cfg['name']} key", v)
            set_secret_id(cid, s["id"])

    legacy_llm = data.pop("llm_keys", None)
    legacy_live = data.pop("live_keys", None)
    _adopt(
        legacy_llm if isinstance(legacy_llm, dict) else {},
        llm_configs.get_config,
        llm_configs.set_secret_id,
    )
    _adopt(
        legacy_live if isinstance(legacy_live, dict) else {},
        live_configs.get_config,
        live_configs.set_secret_id,
    )
    data[_SECRETS_FIELD] = items
    _write(data)


def channel_token_status() -> dict:
    """Per-env-var presence for channel bot tokens (never the raw value). A token set
    only in the real env (e.g. ``.env``) also counts as present."""
    data = _read()
    saved = _saved_channel_tokens(data)
    return {
        env_name: bool(saved.get(env_name) or os.environ.get(env_name))
        for env_name in sorted(CHANNEL_TOKEN_ENV_NAMES)
    }

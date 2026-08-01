"""Named live (voice) configurations — the install-wide list that backs realtime
voice, the spoken counterpart to :mod:`llm_configs`.

A live configuration is a named bundle of what it takes to open one realtime voice
session: a ``provider`` (one of the registered :mod:`voice_providers` — today
``gemini`` or ``openai``, the only backends with realtime support), a ``model`` (the
realtime model name; defaults to the provider's ``realtime_model``), a ``voice`` (one
of the provider's catalogue), and an optional ``secret_id`` referencing a Secret
(the key itself lives in the secrets store, never here). The list *and* the single ``active`` selection are
install-wide (voice, like the LLM, is common across profiles), so this store lives in
the ``live_configs:`` section of the global ``config.yaml``.

This mirrors :mod:`llm_configs` deliberately, but stays simpler: the provider set is
a fixed registry (no free-form types, no ``base_url``/``host``/subscription), so the
only key sources are the config's referenced Secret, the provider's shared env key,
or none.

The bridge to the runtime is :meth:`LiveConfigStore.active_config` +
:meth:`LiveConfigStore.resolve_key`, read fresh
by :mod:`assistant.voice` when a voice session connects — so nothing needs reloading
when the active config changes. An empty store (or an unresolved ``active``) makes the
call site fall back to the profile's legacy ``voice_provider``/voice, exactly like an
empty ``llm_configs`` falling back to the flat ``llm:`` defaults.
"""

from collections.abc import Mapping
from secrets import token_hex

from assistant import voice_providers
from assistant.config import read_global_config, update_global_section
from assistant.paths import Paths
from assistant.secrets import KEY_ENV, SecretStore

_SECTION = "live_configs"


def _clean_entry(raw: dict) -> dict:
    """Validate and normalise one entry to its canonical shape, raising ``ValueError``
    on bad input. ``provider`` must be a registered voice provider; ``voice`` defaults
    to the provider's default (and must be one it offers); ``model`` defaults to the
    provider's ``realtime_model`` when blank. ``id`` is preserved if present."""
    if not isinstance(raw, dict):
        raise ValueError("configuration must be an object")
    provider = str(raw.get("provider") or "").strip().lower()
    if provider not in voice_providers.names():
        raise ValueError(
            f"provider must be one of {', '.join(voice_providers.names())}, not {provider!r}"
        )
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("configuration name is required")
    p = voice_providers.get(provider)
    model = str(raw.get("model") or "").strip() or p.realtime_model
    voice = str(raw.get("voice") or "").strip() or p.default_voice
    if voice not in p.voices:
        raise ValueError(f"voice {voice!r} is not offered by {provider}")
    entry = {
        "id": str(raw.get("id") or "").strip(),
        "name": name,
        "provider": provider,
        "model": model,
        "voice": voice,
        "secret_id": str(raw.get("secret_id") or "").strip(),
    }
    if not entry["id"]:
        entry.pop("id")
    return entry


class LiveConfigStore:
    """One install's named live (voice) configurations, in the ``live_configs:``
    section of the global ``config.yaml``."""

    def __init__(self, paths: Paths) -> None:
        self._paths = paths
        self._secrets = SecretStore(paths)

    def _read(self) -> dict:
        """The store section (``{"active": id|None, "configs": [...]}``) of the global
        config.yaml. A missing or malformed section reads as an empty store."""
        data = read_global_config(self._paths).get(_SECTION)
        if not isinstance(data, dict):
            return {"active": None, "configs": []}
        configs = data.get("configs")
        return {
            "active": data.get("active"),
            "configs": [c for c in configs if isinstance(c, dict)]
            if isinstance(configs, list)
            else [],
        }

    def _write(self, data: dict) -> None:
        update_global_section(
            self._paths,
            _SECTION,
            {"active": data.get("active"), "configs": list(data.get("configs") or [])},
        )

    def list_configs(self) -> list[dict]:
        """Every configuration in the store, in insertion order (empty when unset)."""
        return list(self._read().get("configs") or [])

    def get_config(self, cid: str) -> dict | None:
        """One configuration by id, or None if absent."""
        return next((c for c in self.list_configs() if c.get("id") == cid), None)

    def save_config(self, raw: dict) -> dict:
        """Create (no ``id``) or update (``id`` present) a configuration and return the
        stored entry. A new entry gets an id ``"lv_" + token_hex(4)``; updating an
        unknown id raises ``KeyError``."""
        entry = _clean_entry(raw)
        data = self._read()
        configs = list(data.get("configs") or [])
        if "id" in entry:
            for i, existing in enumerate(configs):
                if existing.get("id") == entry["id"]:
                    configs[i] = entry
                    break
            else:
                raise KeyError(entry["id"])
        else:
            entry["id"] = "lv_" + token_hex(4)
            configs.append(entry)
        data["configs"] = configs
        self._write(data)
        return entry

    def delete_config(self, cid: str) -> bool:
        """Remove a configuration by id (False if unknown). Deleting the active one is
        allowed: the active pointer moves to the first remaining config, or None when it
        was the last (voice then falls back to the profile's legacy provider)."""
        data = self._read()
        configs = list(data.get("configs") or [])
        if not any(c.get("id") == cid for c in configs):
            return False
        remaining = [c for c in configs if c.get("id") != cid]
        data["configs"] = remaining
        if data.get("active") == cid:
            data["active"] = remaining[0]["id"] if remaining else None
        self._write(data)
        return True

    def set_secret_id(self, cid: str, sid: str) -> bool:
        """Point one config at a Secret by id (empty ``sid`` clears the reference).
        Returns False for an unknown config."""
        data = self._read()
        configs = list(data.get("configs") or [])
        for i, entry in enumerate(configs):
            if entry.get("id") == cid:
                configs[i] = {**entry, "secret_id": (sid or "").strip()}
                data["configs"] = configs
                self._write(data)
                return True
        return False

    def active_id(self) -> str | None:
        """The id of the active configuration, or None (empty store / none selected)."""
        return self._read().get("active")

    def set_active(self, cid: str) -> bool:
        """Make ``cid`` the active configuration (False for an unknown id)."""
        data = self._read()
        if not any(c.get("id") == cid for c in (data.get("configs") or [])):
            return False
        data["active"] = cid
        self._write(data)
        return True

    def active_config(self) -> dict | None:
        """The active configuration entry, or None when the store is empty or its
        ``active`` id doesn't resolve to a present entry."""
        aid = self.active_id()
        return self.get_config(aid) if aid else None

    def set_voice(self, cid: str, voice: str) -> bool:
        """Persist a config's chosen voice (each live config carries its own). Returns
        False for an unknown id or a voice the config's provider doesn't offer."""
        data = self._read()
        configs = list(data.get("configs") or [])
        for i, entry in enumerate(configs):
            if entry.get("id") == cid:
                p = voice_providers.get(entry.get("provider"))
                if voice not in p.voices:
                    return False
                configs[i] = {**entry, "voice": voice}
                data["configs"] = configs
                self._write(data)
                return True
        return False

    def resolve_key(self, entry: dict, env: Mapping[str, str]) -> str:
        """The raw API key a session for this config would send: its referenced Secret,
        else the provider's shared key from ``env`` (which a Default Secret contributes
        via ``Config.secret_env``). Empty string when neither is set (the builders then
        get an empty key and fail loudly). In-process only — never returned by any
        endpoint."""
        own = self._secrets.secret_value(entry.get("secret_id") or "")
        if own:
            return own
        return env.get(KEY_ENV.get(entry.get("provider", ""), ""), "")

    def key_source(self, entry: dict, env: Mapping[str, str]) -> str:
        """Which key this config would send, for honest UI labelling: ``"secret"`` (its
        referenced Secret), ``"shared"`` (the provider's key in ``env``), or ``"none"``
        (nothing available — the config can't run)."""
        if self._secrets.secret_value(entry.get("secret_id") or ""):
            return "secret"
        provider = entry.get("provider", "")
        if self._secrets.status(env).get(provider, {}).get("set"):
            return "shared"
        return "none"

    def usable(self, entry: dict, env: Mapping[str, str]) -> bool:
        """Whether this configuration can actually open a session right now — a
        per-config key OR the provider's shared key must be present."""
        return self.key_source(entry, env) != "none"

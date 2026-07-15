"""Named LLM configurations — the install-wide list that replaces the old
per-profile provider/model + Advanced-JSON settings.

An LLM configuration is a named, type-tagged bundle of what it takes to reach one
model: a ``type`` (one of :data:`TYPES`), a ``model`` name, an optional endpoint
(``base_url`` for OpenAI/Anthropic-compatible servers, ``host`` for Ollama), a
free-form ``options`` escape-hatch object, and — held separately in the secrets
store, never here — an optional per-config API key. The list *and* the single
``active`` selection are install-wide (the LLM is common across profiles), so this
store lives in the ``llm_configs:`` section of the global ``config.yaml`` (no
secrets), read/written via ``config.read_global_config`` / ``update_global_section``
so it shares the file without disturbing neighbouring sections and never recurses
back into ``load_config()``.

The bridge to the rest of the app is :func:`apply_active`: ``load_config()`` calls
it to *derive* the active entry onto the flat ``cfg.llm`` fields
(``provider``/``model``/``provider_options``) that ``model_config`` and friends
already consume — so downstream code stays untouched. Type → derivation:

===============  ================  =============================
type             cfg.llm.provider  injected provider_options
===============  ================  =============================
openai           openai            ``{"api": "chat", base_url?}``
openai_responses openai            ``{"api": "responses", base_url?}``
anthropic        anthropic         ``{base_url?}``
gemini           gemini            ``{}``
ollama           ollama            ``{host?}``
===============  ================  =============================

An entry's ``options`` merge FIRST, the type-forced fields (``api`` and the lifted
``base_url``/``host``) next, and the resolved per-config ``api_key`` LAST — riding
the ``provider_options`` merge machinery in ``model_config``. With no per-config key
the provider's conventional env var (``KEY_ENV``) still applies. Note: an active
entry SHADOWS the ``llm`` block in ``config.json`` (that block is only the flat
default used when the store is empty or has no active entry).
"""

from secrets import token_hex

from assistant import secrets
from assistant.config import read_global_config, update_global_section

# The supported configuration types. ``openai`` = Chat Completions API, the surface
# OpenAI-compatible servers (llama.cpp, vLLM, LM Studio) implement reliably;
# ``openai_responses`` = OpenAI's Responses API (their preferred surface, also enables
# the native image-generation tool); ``openai_subscription`` = "Sign in with ChatGPT",
# reaching the ChatGPT backend on the user's Codex/ChatGPT subscription (no API key,
# no endpoint — both come from ``codex_auth`` at call time). The rest map 1:1 to a
# provider.
TYPES = ("openai", "openai_responses", "openai_subscription", "anthropic", "gemini", "ollama")

# type → the ``cfg.llm.provider`` it derives to (all three OpenAI surfaces are provider
# "openai"; ``model_config`` picks the API from the injected ``api`` option, or the
# subscription branch off ``cfg.llm.auth_mode``).
PROVIDER_OF = {
    "openai": "openai",
    "openai_responses": "openai",
    "openai_subscription": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "ollama": "ollama",
}


def _subscription_signed_in() -> bool:
    """Whether ChatGPT-subscription sign-in is currently active. Lazy + guarded: a
    missing or broken ``codex_auth`` module must never raise into the usable()/health
    path, so any import or call failure reads as "not signed in"."""
    try:
        from assistant import codex_auth

        return bool(codex_auth.is_signed_in())
    except Exception:
        return False


_SECTION = "llm_configs"


def _read() -> dict:
    """The store section (``{"active": id|None, "configs": [...]}``) of the global
    config.yaml. A missing or malformed section reads as an empty store — the flat
    ``llm:`` defaults then apply, exactly like a malformed config file."""
    data = read_global_config().get(_SECTION)
    if not isinstance(data, dict):
        return {"active": None, "configs": []}
    configs = data.get("configs")
    return {
        "active": data.get("active"),
        "configs": [c for c in configs if isinstance(c, dict)] if isinstance(configs, list) else [],
    }


def _write(data: dict) -> None:
    update_global_section(
        _SECTION, {"active": data.get("active"), "configs": list(data.get("configs") or [])}
    )


def _clean_entry(raw: dict) -> dict:
    """Validate and normalise one entry to its canonical shape, raising ``ValueError``
    with a clear message on bad input. ``id`` is preserved if present (an update);
    callers minting a new entry leave it absent so :func:`save_config` assigns one."""
    if not isinstance(raw, dict):
        raise ValueError("configuration must be an object")
    ctype = str(raw.get("type") or "").strip()
    if ctype not in TYPES:
        raise ValueError(f"type must be one of {', '.join(TYPES)}, not {ctype!r}")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("configuration name is required")
    model = str(raw.get("model") or "").strip()
    if not model:
        raise ValueError("model is required")
    options = raw.get("options") or {}
    if not isinstance(options, dict):
        raise ValueError("options must be a JSON object")
    # Subscription mode has no endpoint fields — the base_url and bearer token both
    # come from codex_auth. Force them empty so a stale/typo'd value can't ride along.
    # Advanced options are stripped too: the ChatGPT backend rejects every sampling
    # parameter we probed live (temperature, top_p, max_output_tokens → "Unsupported
    # parameter"), so a stored option only breaks calls; the form hides the editor.
    is_subscription = ctype == "openai_subscription"
    entry = {
        "id": str(raw.get("id") or "").strip(),
        "name": name,
        "type": ctype,
        "model": model,
        "base_url": "" if is_subscription else str(raw.get("base_url") or "").strip(),
        "host": "" if is_subscription else str(raw.get("host") or "").strip(),
        "options": {} if is_subscription else options,
    }
    if not entry["id"]:
        entry.pop("id")
    return entry


def list_configs() -> list[dict]:
    """Every configuration in the store, in insertion order (empty list when unset)."""
    return list(_read().get("configs") or [])


def get_config(cid: str) -> dict | None:
    """One configuration by id, or None if absent."""
    return next((c for c in list_configs() if c.get("id") == cid), None)


def save_config(raw: dict) -> dict:
    """Create (no ``id``) or update (``id`` present) a configuration and return the
    stored entry. Validates via :func:`_clean_entry`. A new entry gets an id
    ``"c_" + token_hex(4)``; updating an unknown id raises ``KeyError``."""
    entry = _clean_entry(raw)
    data = _read()
    configs = list(data.get("configs") or [])
    if "id" in entry:
        for i, existing in enumerate(configs):
            if existing.get("id") == entry["id"]:
                configs[i] = entry
                break
        else:
            raise KeyError(entry["id"])
    else:
        entry["id"] = "c_" + token_hex(4)
        configs.append(entry)
    data["configs"] = configs
    _write(data)
    return entry


def delete_config(cid: str) -> bool:
    """Remove a configuration by id (returns False if unknown). Deleting the active one
    is allowed: the active pointer moves to the first remaining config, or None when it
    was the last (an empty store then falls back to the flat ``llm:`` defaults, exactly
    like a fresh install). The per-config secret is the endpoint's to clean up (this
    store holds no secrets)."""
    data = _read()
    configs = list(data.get("configs") or [])
    if not any(c.get("id") == cid for c in configs):
        return False
    remaining = [c for c in configs if c.get("id") != cid]
    data["configs"] = remaining
    if data.get("active") == cid:
        data["active"] = remaining[0]["id"] if remaining else None
    _write(data)
    return True


def active_id() -> str | None:
    """The id of the active configuration, or None (empty store / none selected)."""
    return _read().get("active")


def set_active(cid: str) -> bool:
    """Make ``cid`` the active configuration (returns False for an unknown id)."""
    data = _read()
    if not any(c.get("id") == cid for c in (data.get("configs") or [])):
        return False
    data["active"] = cid
    _write(data)
    return True


def active_config() -> dict | None:
    """The active configuration entry, or None when the store is empty or its
    ``active`` id doesn't resolve to a present entry."""
    aid = active_id()
    return get_config(aid) if aid else None


def entry_options(entry: dict) -> dict:
    """The ``provider_options`` kwargs derived from one entry, ready to drop into
    ``cfg.llm.provider_options[provider]``.

    Merge order (later wins): the entry's own ``options`` object first; then the
    type-forced fields — ``api`` for the two OpenAI surfaces plus the lifted
    ``base_url`` (OpenAI/Anthropic) or ``host`` (Ollama) when set; then the resolved
    per-config ``api_key`` last, so a config's own key overrides anything in options.
    Absent per-config key → the env fallback in ``model_config`` applies — EXCEPT
    when the entry targets a custom ``base_url``: the shared provider key must never
    be transmitted to a third-party/local endpoint, so a placeholder is forced
    instead. It is non-empty on purpose (the OpenAI SDK refuses a missing key and
    llama.cpp-style servers expect some bearer value); endpoints that need a real
    key (e.g. MiniMax) take it via the per-config key field."""
    opts = dict(entry.get("options") or {})
    ctype = entry.get("type")
    if ctype == "openai_subscription":
        # model_config routes this type through codex_auth off cfg.llm.auth_mode and
        # ignores provider_options entirely — so no api/base_url/key derivation here;
        # return only the entry's own free-form options untouched.
        return opts
    if ctype == "openai":
        opts["api"] = "chat"
    elif ctype == "openai_responses":
        opts["api"] = "responses"
    if ctype in ("openai", "openai_responses", "anthropic") and entry.get("base_url"):
        opts["base_url"] = entry["base_url"]
    elif ctype == "ollama" and entry.get("host"):
        opts["host"] = entry["host"]
    key = secrets.config_key(entry.get("id") or "")
    if key:
        opts["api_key"] = key
    elif entry.get("base_url"):
        opts["api_key"] = "unused"  # never leak the shared key to a custom endpoint
    return opts


def apply_active(cfg) -> None:
    """Derive the active configuration onto the flat ``cfg.llm`` fields, in place.

    No-op when the store is empty or its active id doesn't resolve (so a fresh
    install / CLI-before-store keeps the flat ``config.json`` gemini defaults). Called
    from ``load_config()`` BEFORE the env overrides, so ``AG2ASSISTANT_LLM_PROVIDER`` /
    ``AG2ASSISTANT_MODEL`` still win last."""
    entry = active_config()
    if entry is None:
        return
    provider = PROVIDER_OF[entry["type"]]
    cfg.llm.provider = provider
    cfg.llm.model = entry["model"]
    cfg.llm.provider_options[provider] = entry_options(entry)
    # OpenAI auth mode is a property of the active entry's type. Set it on EVERY
    # apply (not just the subscription branch) so switching back to a normal OpenAI
    # config resets it to key auth. The AG2ASSISTANT_OPENAI_AUTH_MODE env override
    # still wins last (applied after apply_active in load_config).
    cfg.llm.auth_mode = "subscription" if entry["type"] == "openai_subscription" else "api_key"


def usable(entry: dict) -> bool:
    """Whether this configuration can actually run right now — the signal behind the
    health dot. Ollama is local (always). A ``base_url`` (OpenAI/Anthropic-compatible
    server) needs no real provider key. Otherwise a per-config key OR the provider's
    env key must be present."""
    ctype = entry.get("type")
    if ctype == "openai_subscription":
        # No API key at all — usable exactly when ChatGPT sign-in is live.
        return _subscription_signed_in()
    if ctype == "ollama":
        return True
    if entry.get("base_url"):
        return True
    if secrets.config_key(entry.get("id") or ""):
        return True
    provider = PROVIDER_OF.get(ctype, "")
    return bool(secrets.status().get(provider, {}).get("set"))


def key_source(entry: dict) -> str:
    """Which key this configuration would actually send, for honest UI labelling:

    - ``"config"`` — its own per-config key (secrets ``llm_keys``); overrides all.
    - ``"not_needed"`` — Ollama (no key concept) or a custom ``base_url`` (a
      placeholder is sent; see :func:`entry_options` — the shared key never is).
    - ``"shared"`` — no key of its own; the provider's shared/env key (``KEY_ENV``)
      is what ``model_config``'s fallback will send to the provider's own endpoint.
    - ``"subscription"`` — ChatGPT sign-in (``openai_subscription``); no API key at
      all, the bearer token rides from ``codex_auth``.
    - ``"none"`` — nothing available; the config can't run (mirrors :func:`usable`).
    """
    if entry.get("type") == "openai_subscription":
        return "subscription"
    if secrets.config_key(entry.get("id") or ""):
        return "config"
    if entry.get("type") == "ollama" or entry.get("base_url"):
        return "not_needed"
    provider = PROVIDER_OF.get(entry.get("type"), "")
    if secrets.status().get(provider, {}).get("set"):
        return "shared"
    return "none"


def image_capable(entry: dict) -> bool:
    """Whether image generation works on this configuration's type.

    ``gemini`` (image-output modality), ``openai_responses`` and plain ``openai``
    without a ``base_url`` (the native image tool needs a real OpenAI endpoint — a
    compat server won't serve it), and ``openai_subscription`` (the ChatGPT backend
    runs the image tool too — verified live; AG2 captures the streamed image)."""
    t = entry.get("type")
    if t in ("gemini", "openai_responses", "openai_subscription"):
        return True
    return t == "openai" and not entry.get("base_url")


def image_entry() -> dict | None:
    """The configuration image generation runs on: the ACTIVE one, iff it is
    image-capable — otherwise None (the tool reports images unavailable). No
    fallback hunting through the list: images follow the selected configuration,
    so switching models never silently routes images somewhere else."""
    active = active_config()
    return active if active and image_capable(active) else None

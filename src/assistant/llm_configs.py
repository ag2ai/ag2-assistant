"""Named LLM configurations — the install-wide list that replaces the old
per-profile provider/model + Advanced-JSON settings.

An LLM configuration is a named, type-tagged bundle of what it takes to reach one
model: a ``type`` (one of :data:`TYPES`), a ``model`` name, an optional endpoint
(``base_url`` for OpenAI/Anthropic-compatible servers, ``host`` for Ollama), a
free-form ``options`` escape-hatch object, and an optional ``secret_id``
referencing a Secret (the key itself lives in the secrets store, never here). The list *and* the single
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
claude_code      claude_code       ``options passed to ACPConfig``
codex            codex             ``options passed to ACPConfig``
===============  ================  =============================

An entry's ``options`` merge FIRST, the type-forced fields (``api`` and the lifted
``base_url``/``host``) next, and the referenced Secret's ``api_key`` LAST — riding
the ``provider_options`` merge machinery in ``model_config``. With no referenced
Secret the provider's conventional env var (``KEY_ENV``) still applies. Note: an active
entry SHADOWS the ``llm`` block in ``config.json`` (that block is only the flat
default used when the store is empty or has no active entry).
"""

from collections.abc import Mapping
from secrets import token_hex

from assistant.config import read_global_config, update_global_section
from assistant.paths import Paths
from assistant.secrets import SecretStore

# The supported configuration types. ``openai`` = Chat Completions API, the surface
# OpenAI-compatible servers (llama.cpp, vLLM, LM Studio) implement reliably;
# ``openai_responses`` = OpenAI's Responses API (their preferred surface, also enables
# the native image-generation tool); ``openai_subscription`` = "Sign in with ChatGPT",
# reaching the ChatGPT backend on the user's Codex/ChatGPT subscription (no API key,
# no endpoint — both come from ``codex_auth`` at call time); ``claude_code`` = the
# user's Claude Code CLI driven over ACP (auth is the CLI's own disk login, options
# are ACPConfig constructor overrides); ``codex`` = the user's Codex CLI driven over
# ACP the same way (ChatGPT-subscription disk login). The rest map 1:1 to a provider.
TYPES = (
    "openai",
    "openai_responses",
    "openai_subscription",
    "anthropic",
    "gemini",
    "ollama",
    "claude_code",
    "codex",
)

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
    "claude_code": "claude_code",
    "codex": "codex",
}

# The CLI-login types: a coding CLI driven over ACP, auth = that CLI's own on-disk
# login. They share every rule that separates them from API types — no endpoint, no
# key, no model required (empty = the CLI's default), options are ACPConfig
# constructor overrides — so the rules below branch on this ONE tuple. A third
# adapter (detect.py already knows ``opencode``) becomes an entry here plus its
# ``_cli_login_present`` row, not another eight scattered literals.
CLI_LOGIN_TYPES = ("claude_code", "codex")


def _subscription_signed_in(paths: Paths) -> bool:
    """Whether ChatGPT-subscription sign-in is currently active. Lazy + guarded: a
    missing or broken ``codex_auth`` module must never raise into the usable()/health
    path, so any import or call failure reads as "not signed in"."""
    try:
        from assistant.codex_auth import CodexAuth  # local: import cycle (codex_auth)

        return bool(CodexAuth(paths).is_signed_in())
    except Exception:
        return False


def _acp_adapter_present(name: str) -> bool:
    """Whether a coding CLI's ACP adapter is reachable — on PATH locally, or via
    a configured host bridge (Docker). Lazy + guarded: a missing/broken coding
    module must read as "not available", never raise into the health path.

    Caveat in bridge mode: the bridge's agent inventory is async, so a configured
    bridge counts as present for EVERY agent — this answers "a bridge exists",
    not "that bridge has THIS adapter". A host missing the adapter therefore
    reads as usable here and fails at call time instead."""
    try:
        from assistant.coding import detect  # local: keep the health path lazy

        if detect.bridge_endpoint() is not None:
            return True
        return detect.resolve_agent(name) is not None
    except Exception:
        return False


# CLI-login type → the coding agent whose ACP adapter backs it (detect.py's names).
_CLI_LOGIN_AGENT = {"claude_code": "claude", "codex": "codex"}


def cli_login_present(ctype: str) -> bool:
    """Whether this CLI-login type's ACP adapter is reachable (auth itself is the
    CLI's own on-disk login, which the adapter consults)."""
    agent = _CLI_LOGIN_AGENT.get(ctype)
    return _acp_adapter_present(agent) if agent else False


_SECTION = "llm_configs"


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
    if not model and ctype not in CLI_LOGIN_TYPES:
        # For the CLI-login types an empty model means "the CLI's own default"
        # (no model env is derived). Every other type requires an explicit model.
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
    # The CLI-login types also have no endpoint/key fields (auth = the CLI's disk
    # login), but KEEP options: they override ACPConfig constructor fields.
    strip_endpoint = is_subscription or ctype in CLI_LOGIN_TYPES
    entry = {
        "id": str(raw.get("id") or "").strip(),
        "name": name,
        "type": ctype,
        "model": model,
        "base_url": "" if strip_endpoint else str(raw.get("base_url") or "").strip(),
        "host": "" if strip_endpoint else str(raw.get("host") or "").strip(),
        "secret_id": "" if strip_endpoint else str(raw.get("secret_id") or "").strip(),
        "options": {} if is_subscription else options,
    }
    if not entry["id"]:
        entry.pop("id")
    return entry


class LlmConfigStore:
    """One install's named LLM configurations, in the ``llm_configs:`` section of the
    global ``config.yaml``. The list and the single ``active`` selection are
    install-wide (the LLM is common across profiles)."""

    def __init__(self, paths: Paths) -> None:
        self._paths = paths
        self._secrets = SecretStore(paths)

    def _read(self) -> dict:
        """The store section (``{"active": id|None, "configs": [...]}``) of the global
        config.yaml. A missing or malformed section reads as an empty store — the flat
        ``llm:`` defaults then apply, exactly like a malformed config file."""
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
        stored entry. Validates via :func:`_clean_entry`. A new entry gets an id
        ``"c_" + token_hex(4)``; updating an unknown id raises ``KeyError``."""
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
            entry["id"] = "c_" + token_hex(4)
            configs.append(entry)
        data["configs"] = configs
        self._write(data)
        return entry

    def delete_config(self, cid: str) -> bool:
        """Remove a configuration by id (False if unknown). Deleting the active one is
        allowed: the active pointer moves to the first remaining config, or None when it
        was the last (an empty store then falls back to the flat ``llm:`` defaults).
        Referenced Secrets are independent entities and are never deleted with a config."""
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

    def entry_options(self, entry: dict) -> dict:
        """The ``provider_options`` kwargs derived from one entry, ready to drop into
        ``cfg.llm.provider_options[provider]``.

        Merge order (later wins): the entry's own ``options`` object first; then the
        type-forced fields — ``api`` for the two OpenAI surfaces plus the lifted
        ``base_url`` (OpenAI/Anthropic) or ``host`` (Ollama) when set; then the
        referenced Secret's ``api_key`` last, so a config's own Secret overrides
        anything in options. No resolving Secret → the env fallback in ``model_config``
        applies — EXCEPT when the entry targets a custom ``base_url``: the shared
        provider key must never be transmitted to a third-party/local endpoint, so a
        placeholder is forced instead. It is non-empty on purpose (the OpenAI SDK
        refuses a missing key and llama.cpp-style servers expect some bearer value);
        endpoints that need a real key (e.g. MiniMax) take it via a referenced Secret."""
        opts = dict(entry.get("options") or {})
        ctype = entry.get("type")
        if ctype == "openai_subscription":
            # model_config routes this type through codex_auth off cfg.llm.auth_mode and
            # ignores provider_options entirely — so no api/base_url/key derivation here;
            # return only the entry's own free-form options untouched.
            return opts
        if ctype in CLI_LOGIN_TYPES:
            # ACPConfig constructor overrides ride through untouched; there is no
            # api/base_url/key derivation for CLI-login providers.
            return opts
        if ctype == "openai":
            opts["api"] = "chat"
        elif ctype == "openai_responses":
            opts["api"] = "responses"
        if ctype in ("openai", "openai_responses", "anthropic") and entry.get("base_url"):
            opts["base_url"] = entry["base_url"]
        elif ctype == "ollama" and entry.get("host"):
            opts["host"] = entry["host"]
        key = self._secrets.secret_value(entry.get("secret_id") or "")
        if key:
            opts["api_key"] = key
        elif entry.get("base_url"):
            opts["api_key"] = "unused"  # never leak the shared key to a custom endpoint
        return opts

    def apply_active(self, cfg, override_id: str | None = None) -> None:
        """Derive the active configuration onto the flat ``cfg.llm`` fields, in place.

        No-op when the store is empty or its active id doesn't resolve (so a fresh
        install / CLI-before-store keeps the flat gemini defaults). Called from
        ``resolve_config()`` BEFORE the env overrides, so ``AG2ASSISTANT_LLM_PROVIDER`` /
        ``AG2ASSISTANT_MODEL`` still win last.

        ``override_id`` is a profile's per-profile **Active override** (ADR 0015): a
        selection into this shared list that is Active *for that profile*. It is preferred
        over the install-wide active when it resolves to a present config; a dangling
        override (deleted config) silently falls back to the install-wide active, never an
        error. ``Config.with_profile`` passes it so the effective Active is env pin >
        profile override > install-wide active > env fallback."""
        entry = self.get_config(override_id) if override_id else None
        if entry is None:
            entry = self.active_config()
        if entry is None:
            return
        provider = PROVIDER_OF[entry["type"]]
        cfg.llm.provider = provider
        cfg.llm.model = entry["model"]
        cfg.llm.provider_options[provider] = self.entry_options(entry)
        # OpenAI auth mode is a property of the active entry's type. Set it on EVERY
        # apply (not just the subscription branch) so switching back to a normal OpenAI
        # config resets it to key auth. The AG2ASSISTANT_OPENAI_AUTH_MODE env override
        # still wins last (applied after apply_active in resolve_config).
        cfg.llm.auth_mode = "subscription" if entry["type"] == "openai_subscription" else "api_key"

    def usable(self, entry: dict, env: Mapping[str, str]) -> bool:
        """Whether this configuration can actually run right now — the signal behind the
        health dot. Ollama is local (always). A ``base_url`` (OpenAI/Anthropic-compatible
        server) needs no real provider key. Otherwise a per-config key OR the provider's
        key in ``env`` must be present."""
        ctype = entry.get("type")
        if ctype == "openai_subscription":
            # No API key at all — usable exactly when ChatGPT sign-in is live.
            return _subscription_signed_in(self._paths)
        if ctype in CLI_LOGIN_TYPES:
            # No key either — usable exactly when that CLI's ACP adapter (or a bridge)
            # exists; auth itself is the CLI's on-disk login.
            return cli_login_present(str(ctype))
        if ctype == "ollama":
            return True
        if entry.get("base_url"):
            return True
        if self._secrets.secret_value(entry.get("secret_id") or ""):
            return True
        provider = PROVIDER_OF.get(ctype, "")
        return bool(self._secrets.status(env).get(provider, {}).get("set"))

    def key_source(self, entry: dict, env: Mapping[str, str]) -> str:
        """Which key this configuration would actually send, for honest UI labelling:

        - ``"secret"`` — its referenced Secret resolves; overrides all.
        - ``"not_needed"`` — Ollama (no key concept) or a custom ``base_url`` (a
          placeholder is sent; see :meth:`entry_options` — the shared key never is).
        - ``"shared"`` — no key of its own; the provider's shared key from ``env``
          (``KEY_ENV``) is what ``model_config``'s fallback will send.
        - ``"subscription"`` — ChatGPT sign-in (``openai_subscription``); no API key at
          all, the bearer token rides from ``codex_auth``.
        - ``"cli_login"`` — Claude Code (``claude_code``) or Codex (``codex``); the ACP
          adapter/bridge is present and auth is the CLI's own on-disk login.
        - ``"none"`` — nothing available; the config can't run (mirrors :meth:`usable`).
        """
        if entry.get("type") == "openai_subscription":
            return "subscription"
        if entry.get("type") in CLI_LOGIN_TYPES:
            # "cli_login" when the adapter (or bridge) is present, "none" otherwise —
            # so the web isUsable() predicate (key_source === 'none' → dead) works
            # for these types with no client-side special case.
            return "cli_login" if cli_login_present(str(entry.get("type"))) else "none"
        if self._secrets.secret_value(entry.get("secret_id") or ""):
            return "secret"
        if entry.get("type") == "ollama" or entry.get("base_url"):
            return "not_needed"
        provider = PROVIDER_OF.get(entry.get("type"), "")
        if self._secrets.status(env).get(provider, {}).get("set"):
            return "shared"
        return "none"

    def image_entry(self) -> dict | None:
        """The configuration image generation runs on: the ACTIVE one, iff it is
        image-capable — otherwise None (the tool reports images unavailable). No
        fallback hunting through the list: images follow the selected configuration,
        so switching models never silently routes images somewhere else."""
        active = self.active_config()
        return active if active and image_capable(active) else None


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

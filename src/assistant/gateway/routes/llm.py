"""Named LLM configurations and their spoken counterpart, the live (voice)
configurations. Both are install-wide lists with a single active selection.

An LLM change reloads every runtime (an agent is built from the config it booted
with); a live change does not, because the voice session reads the store fresh at
connect.

The four probe callables are ``create_app``'s parameters, not stores, so they
arrive as keyword collaborators rather than through ``GatewayDeps``: tests swap
them per app to keep a "Test" button from making a real provider call.

Pairs with gateway/schemas/llm.py (the response models) and
web/src/schemas/llm.ts (their zod twins).
"""

import asyncio
import time
from collections.abc import Callable

import ag2
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from assistant import live_configs, llm_configs, provider_catalog, voice_providers
from assistant.coding.model_catalog import CatalogModel, as_view
from assistant.config import Config
from assistant.gateway.routes.common import reload_all
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import (
    LiveConfigListResponse,
    LiveConfigSavedResponse,
    LlmConfigListResponse,
    LlmConfigSavedResponse,
    Ok,
    PingResultResponse,
    ProviderCatalogResponse,
)
from assistant.secrets import KEY_ENV, OLLAMA_BASE_ENV
from assistant.structured import aclose_config

# Query names GET /api/llm-configs/models refuses outright rather than ignores, so
# routing a pasted key through it fails loudly (ADR 0024).
_CATALOG_KEY_PARAMS = ("api_key", "apikey", "key", "secret", "token", "password")


def _truthy(value: str) -> bool:
    """A query flag the way FastAPI reads a bool one: 1/true/yes/on, case-blind."""
    return value.strip().lower() in ("1", "true", "yes", "on")


class LlmConfigRequest(BaseModel):
    """Create/update body for a named LLM configuration. A model's key is its
    ``secret_id`` reference (a Secret in the secrets store). ``api_key`` is
    DRAFT-TEST ONLY: the /test endpoints use a typed value directly ("" tests as
    if no Secret resolved); create/update ignore it. ``id`` is only read by the
    draft-test endpoint (create/update take the id from the URL path)."""

    id: str | None = None
    name: str
    type: str
    model: str
    base_url: str = ""
    host: str = ""
    secret_id: str = ""
    api_key: str | None = None
    options: dict = Field(default_factory=dict)
    activate: bool = False


class LiveConfigRequest(BaseModel):
    """Create/update body for a named live (voice) configuration. A config's key is
    its ``secret_id`` reference (a Secret in the secrets store). ``api_key`` is
    DRAFT-TEST ONLY (a typed value is used directly, never persisted). ``id`` is
    only read by the draft-test endpoint. ``voice`` is optional on save (defaults
    to the provider's default voice; usually changed via the voice picker, not
    this body)."""

    id: str | None = None
    name: str
    provider: str
    model: str = ""
    voice: str = ""
    secret_id: str = ""
    api_key: str | None = None
    activate: bool = False


def build_router(
    d: GatewayDeps,
    *,
    secret_env: Callable[[], dict[str, str]],
    llm_probe: Callable,
    llm_probe_timeout_s: float,
    llm_catalog_probe: Callable,
    live_probe: Callable,
) -> APIRouter:
    """The install-level LLM and live config routes, in the order they had in
    app.py — the two ``/test`` literals must stay ahead of their ``/{cid}``."""
    r = APIRouter()

    # ---- Named LLM configurations (install-wide list + single active selection) ----

    def _llm_entry_view(entry: dict, active: str | None) -> dict:
        """One config as the API exposes it: the stored fields, the referenced
        Secret's view (or a dangling-reference flag) and the provider's shared env
        key summary — never the raw values — plus ``key_source`` naming which one an
        actual call would send. That triple is what lets the UI say honestly why a
        keyless-looking config still works (shared fallback / no key needed)."""
        provider = llm_configs.PROVIDER_OF.get(entry["type"], "")
        shared = d.secret_store.status(secret_env()).get(provider, {})
        sec_view = d.secret_store.get_secret(entry.get("secret_id", ""))
        sec = (
            {"id": sec_view["id"], "name": sec_view["name"], "hint": sec_view["hint"]}
            if sec_view
            else None
        )
        view = {
            "id": entry["id"],
            "name": entry["name"],
            "type": entry["type"],
            "model": entry["model"],
            "base_url": entry.get("base_url", ""),
            "host": entry.get("host", ""),
            "options": entry.get("options", {}),
            "secret_id": entry.get("secret_id", ""),
            "secret": sec,
            "secret_missing": bool(entry.get("secret_id")) and sec is None,
            "key_source": d.llm_store.key_source(
                entry, secret_env(), search_path=d.search_path, bridge=d.acp_bridge
            ),  # secret | shared | not_needed | none | subscription
            "images": llm_configs.image_capable(entry),  # drives the row's "images" chip
            # {ok, extra, install} — optional provider library state for this type.
            "deps": llm_configs.deps_status(entry["type"]),
            "shared_key": {
                "env": KEY_ENV.get(provider, ""),
                "set": bool(shared.get("set")),
                "hint": shared.get("hint", ""),
            },
            "active": entry["id"] == active,
        }
        if entry["type"] == "openai_subscription":
            # The chip/form need the live ChatGPT sign-in state without a second
            # fetch. Lazy + guarded so a missing/broken codex_auth reads as signed-out.
            try:
                view["signed_in"] = bool(d.codex.status().get("signed_in"))
            except Exception:
                view["signed_in"] = False
        return view

    def _llm_env_override() -> dict | None:
        """The env pin banner payload: whichever of AG2ASSISTANT_LLM_PROVIDER /
        AG2ASSISTANT_MODEL is set (they override any active config in load_config), or
        None when neither is set."""
        out = {}
        if v := d.manager.env.get("AG2ASSISTANT_LLM_PROVIDER"):
            out["provider"] = v
        if v := d.manager.env.get("AG2ASSISTANT_MODEL"):
            out["model"] = v
        return out or None

    def _llm_probe_config(entry: dict):
        """A throwaway Config carrying just the entry's derived provider/model/options,
        for the dry-construct + test round-trip. Streaming off (a one-shot probe)."""
        probe = Config.for_paths(d.paths)
        # for_paths defaults every non-path field, so the host facts that reach a
        # real turn via apply_env_overrides are absent here. The ACP builders read
        # the bridge off the Config (acp_provider._build), so without this a Docker
        # probe spawns the adapter locally — inside an image that has none — and
        # reports a bare "[Errno 2]" instead of testing the host CLI at all.
        probe.acp_bridge = d.manager.config.acp_bridge
        probe.acp_bridge_token = d.manager.config.acp_bridge_token
        probe.llm.streaming = False
        probe.llm.provider = llm_configs.PROVIDER_OF[entry["type"]]
        probe.llm.model = entry["model"]
        probe.llm.provider_options[probe.llm.provider] = d.llm_store.entry_options(entry)
        # Subscription mode is carried on auth_mode (not provider_options), so mirror
        # apply_active here — otherwise the probe would test the key path with no key.
        if entry["type"] == "openai_subscription":
            probe.llm.auth_mode = "subscription"
        return probe

    @r.get(
        "/api/llm-configs",
        response_model=LlmConfigListResponse,
        response_model_exclude_unset=True,
    )
    async def list_llm_configs():
        """The install-wide named LLM configs, the active id, and any env override that
        pins provider/model over them (drives the 'pinned by env' UI banner)."""
        active = d.llm_store.active_id()
        return {
            "configs": [_llm_entry_view(e, active) for e in d.llm_store.list_configs()],
            "active": active,
            "env_override": _llm_env_override(),
            # Every type, not just the configured ones — the "Add model" template grid
            # reads this for types no config uses yet.
            "provider_deps": {t: llm_configs.deps_status(t) for t in llm_configs.TYPES},
        }

    @r.get("/api/llm-configs/models", response_model=ProviderCatalogResponse)
    async def llm_config_models(request: Request) -> Response:
        """A provider's model catalog in the ACP route's ``{models, current, reason}``
        envelope, named by type, endpoint and ``secret_id`` — no key material (ADR
        0024). ``?refresh=1`` forbids the HTTP cache; there is no other."""
        params = request.query_params
        if any(name in params for name in _CATALOG_KEY_PARAMS):
            return JSONResponse(
                {"ok": False, "error": "this route accepts no key material"}, status_code=400
            )
        ctype = params.get("type", "")
        if ctype in provider_catalog.NEVER_PROBEABLE:
            # Answered rather than probed: no key exists to probe with.
            return JSONResponse(as_view([], "", provider_catalog.NOT_PROBEABLE))
        if ctype not in provider_catalog.GATEWAY_PROBEABLE:
            return JSONResponse(
                {"ok": False, "error": f"no provider catalog for: {ctype}"}, status_code=404
            )
        env = secret_env()
        base_url = params.get("base_url", "")
        api_key = d.secret_store.secret_value(params.get("secret_id", ""))
        if not api_key and not base_url:
            # The install-wide key the request itself would fall back to — but never
            # to a custom endpoint, which _config_kwargs also refuses to hand it to.
            api_key = env.get(KEY_ENV.get(llm_configs.PROVIDER_OF.get(ctype, ""), ""), "")
        target = provider_catalog.CatalogTarget(
            type=ctype,
            base_url=base_url,
            # Same host the turn would use: the entry's, else the install's.
            host=params.get("host", "") or env.get(OLLAMA_BASE_ENV, ""),
            api_key=api_key,
        )
        reason = ""
        models: list[str] = []
        try:
            models = await llm_catalog_probe(target)
        except provider_catalog.CatalogUnavailable as exc:
            reason = exc.reason
        except Exception:
            # A probe that blew up is an endpoint we could not read, not a 500.
            reason = provider_catalog.UNREACHABLE
        rows = [CatalogModel(id=m, name=m, description="") for m in models]
        cache = "no-store" if _truthy(params.get("refresh", "")) else "private, max-age=30"
        return JSONResponse(as_view(rows, "", reason), headers={"Cache-Control": cache})

    async def _save_llm_config(req: LlmConfigRequest, cid: str | None):
        """Shared create/update: dry-construct the derived model_config BEFORE
        persisting (a bad type/kwarg fails here, 400 + the constructor's message, not on
        the agent's next turn), then save the entry, optionally activate, and reload
        every runtime. 404 when updating an unknown id."""
        entry = {
            "name": req.name,
            "type": req.type,
            "model": req.model,
            "base_url": req.base_url,
            "host": req.host,
            "secret_id": req.secret_id,
            "options": req.options,
        }
        if cid is not None:
            if d.llm_store.get_config(cid) is None:
                return JSONResponse(
                    {"ok": False, "error": f"unknown config: {cid}"}, status_code=404
                )
            entry["id"] = cid
        # Validate shape + derived construction before anything is written.
        try:
            probe_entry = llm_configs._clean_entry(entry)
            probe_entry.setdefault("id", cid or "")
            llm_probe(_llm_probe_config(probe_entry))
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)
        saved = d.llm_store.save_config(entry)
        if req.activate:
            d.llm_store.set_active(saved["id"])
        await reload_all(d.manager)
        active = d.llm_store.active_id()
        return {"ok": True, "config": _llm_entry_view(saved, active), "active": active}

    async def _ping_entry(entry: dict, draft_key: str | None = None):
        """The PONG round-trip shared by the saved-config and draft tests: build the
        derived config (streaming off, no tools/memory) and make ONE real call.
        ``draft_key`` overrides the key resolution for an unsaved edit: a typed value
        is used directly, "" tests as if the stored key were cleared (base_url configs
        then get the placeholder — the same thing a save would produce). A working
        reply → ``{ok, reply, latency_ms}``; ANY failure (construction, auth,
        timeout) → 502 ``{ok:false, error}``."""
        started = time.monotonic()
        try:
            probe = _llm_probe_config(entry)
            if draft_key is not None:
                opts = probe.llm.provider_options[probe.llm.provider]
                opts.pop("api_key", None)
                if draft_key:
                    opts["api_key"] = draft_key
                elif entry.get("base_url"):
                    opts["api_key"] = "unused"  # mirror entry_options' placeholder
            probe_cfg = llm_probe(probe)
            agent = ag2.Agent("ping", config=probe_cfg)
            try:
                reply = await asyncio.wait_for(
                    agent.ask("Reply with exactly: PONG"), timeout=llm_probe_timeout_s
                )
            finally:
                # One-shot probe: an ACP config spawned an adapter subprocess for
                # this ping — reap it here (no-op for ordinary provider configs).
                await aclose_config(probe_cfg)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=502)
        return {
            "ok": True,
            "reply": (getattr(reply, "body", "") or "")[:200],
            "latency_ms": int((time.monotonic() - started) * 1000),
        }

    @r.post(
        "/api/llm-configs",
        response_model=LlmConfigSavedResponse,
        response_model_exclude_unset=True,
    )
    async def create_llm_config(req: LlmConfigRequest):
        """Create a new named LLM configuration."""
        return await _save_llm_config(req, None)

    @r.post("/api/llm-configs/test", response_model=PingResultResponse)
    async def test_llm_config_draft(req: LlmConfigRequest):
        """Test a DRAFT configuration exactly as entered in the editor, WITHOUT saving.
        Registered before the /{cid} routes so the literal "test" segment isn't
        captured as an id. ``req.id`` (when editing an existing config) lets a blank
        key field fall back to that config's stored key, matching what a save would
        produce; a typed ``api_key`` is used directly and never persisted."""
        try:
            entry = llm_configs._clean_entry(
                {
                    "id": req.id or "",
                    "name": req.name or "draft",
                    "type": req.type,
                    "model": req.model,
                    "base_url": req.base_url,
                    "host": req.host,
                    "secret_id": req.secret_id or "",
                    "options": req.options,
                }
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)
        entry.setdefault("id", "")
        return await _ping_entry(entry, draft_key=req.api_key)

    @r.post(
        "/api/llm-configs/{cid}",
        response_model=LlmConfigSavedResponse,
        response_model_exclude_unset=True,
    )
    async def update_llm_config(cid: str, req: LlmConfigRequest):
        """Update an existing named LLM configuration (404 if unknown)."""
        return await _save_llm_config(req, cid)

    @r.delete("/api/llm-configs/{cid}", response_model=Ok)
    async def delete_llm_config(cid: str):
        """Delete a config (404 if unknown). Deleting the active one moves active to the
        next remaining config (or none — flat defaults). Reloads every runtime so the
        new active takes effect (referenced Secrets are independent and untouched)."""
        if d.llm_store.get_config(cid) is None:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        d.llm_store.delete_config(cid)
        await reload_all(d.manager)
        return {"ok": True}

    @r.post("/api/llm-configs/{cid}/use", response_model=Ok)
    async def use_llm_config(cid: str):
        """Make ``cid`` the active configuration and reload every runtime (404 unknown)."""
        if not d.llm_store.set_active(cid):
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        await reload_all(d.manager)
        return {"ok": True}

    @r.post("/api/llm-configs/{cid}/test", response_model=PingResultResponse)
    async def test_llm_config(cid: str):
        """Real PONG round-trip against a SAVED config, exercising the exact runtime
        key-resolution path. 404 if unknown; result shape per ``_ping_entry``."""
        entry = d.llm_store.get_config(cid)
        if entry is None:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        return await _ping_entry(entry)

    # ---- Named LIVE (voice) configurations — the spoken counterpart of the LLM
    # configs above. Install-wide list + single active selection, read fresh by the
    # voice session at connect (so no runtime reload on change). ----

    def _live_entry_view(entry: dict, active: str | None) -> dict:
        """One live config as the API exposes it: stored fields + the referenced
        Secret's view (or a dangling-reference flag) and the provider's shared env
        key summary (never the raw values) + ``key_source`` naming which one a
        session sends."""
        provider = entry["provider"]
        shared = d.secret_store.status(secret_env()).get(provider, {})
        sec_view = d.secret_store.get_secret(entry.get("secret_id", ""))
        sec = (
            {"id": sec_view["id"], "name": sec_view["name"], "hint": sec_view["hint"]}
            if sec_view
            else None
        )
        return {
            "id": entry["id"],
            "name": entry["name"],
            "provider": provider,
            "model": entry["model"],
            "voice": entry.get("voice", ""),
            "secret_id": entry.get("secret_id", ""),
            "secret": sec,
            "secret_missing": bool(entry.get("secret_id")) and sec is None,
            "key_source": d.live_store.key_source(entry, secret_env()),  # secret | shared | none
            "shared_key": {
                "env": KEY_ENV.get(provider, ""),
                "set": bool(shared.get("set")),
                "hint": shared.get("hint", ""),
            },
            "active": entry["id"] == active,
        }

    async def _ping_live(entry: dict, draft_key: str | None = None):
        """Models-list key probe (the live-config 'Test'): call the provider's cheap
        ``check`` with the resolved key. ``draft_key`` overrides for an unsaved edit —
        None uses the stored/shared key, "" tests as if the stored key were cleared, a
        value tests that key directly. Ok → ``{ok, reply, latency_ms}``; any failure →
        502 ``{ok:false, error}``."""
        if draft_key is None:
            key = d.live_store.resolve_key(entry, secret_env())
        elif draft_key:
            key = draft_key
        else:
            # "" tests as if the config's own Secret were cleared → the shared key.
            key = d.live_store.resolve_key({**entry, "secret_id": ""}, secret_env())
        started = time.monotonic()
        try:
            await asyncio.wait_for(live_probe(entry["provider"], key), timeout=llm_probe_timeout_s)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=502)
        return {"ok": True, "reply": "OK", "latency_ms": int((time.monotonic() - started) * 1000)}

    @r.get("/api/live-configs", response_model=LiveConfigListResponse)
    async def list_live_configs():
        """The install-wide named live configs, the active id, and the provider catalog
        (default model/voice per provider) that seeds the add-form and templates."""
        active = d.live_store.active_id()
        return {
            "configs": [_live_entry_view(e, active) for e in d.live_store.list_configs()],
            "active": active,
            "providers": [
                {
                    "name": n,
                    "default_model": voice_providers.get(n).realtime_model,
                    "default_voice": voice_providers.get(n).default_voice,
                }
                for n in voice_providers.names()
            ],
        }

    async def _save_live_config(req: LiveConfigRequest, cid: str | None):
        """Shared create/update: validate (bad provider/voice → 400), save, optionally
        activate. 404 when updating an unknown id. A blank ``voice`` on update keeps
        the config's existing voice (it's set via the picker, not this form) rather
        than resetting to the provider default."""
        existing = d.live_store.get_config(cid) if cid is not None else None
        if cid is not None and existing is None:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        voice = req.voice
        if not voice and existing and existing.get("provider") == req.provider:
            voice = existing.get("voice", "")
        entry = {
            "name": req.name,
            "provider": req.provider,
            "model": req.model,
            "voice": voice,
            "secret_id": req.secret_id,
        }
        if cid is not None:
            entry["id"] = cid
        try:
            saved = d.live_store.save_config(entry)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)
        except KeyError:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        if req.activate:
            d.live_store.set_active(saved["id"])
        active = d.live_store.active_id()
        return {"ok": True, "config": _live_entry_view(saved, active), "active": active}

    @r.post("/api/live-configs", response_model=LiveConfigSavedResponse)
    async def create_live_config(req: LiveConfigRequest):
        """Create a new named live configuration."""
        return await _save_live_config(req, None)

    @r.post("/api/live-configs/test", response_model=PingResultResponse)
    async def test_live_config_draft(req: LiveConfigRequest):
        """Probe a DRAFT live config as entered, WITHOUT saving. Registered before the
        /{cid} routes so "test" isn't captured as an id. ``req.id`` lets a blank key
        field fall back to that config's stored key; a typed key is used directly."""
        try:
            entry = live_configs._clean_entry(
                {
                    "id": req.id or "",
                    "name": req.name or "draft",
                    "provider": req.provider,
                    "model": req.model,
                    "voice": req.voice,
                    "secret_id": req.secret_id or "",
                }
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)
        entry.setdefault("id", "")
        return await _ping_live(entry, draft_key=req.api_key)

    @r.post("/api/live-configs/{cid}", response_model=LiveConfigSavedResponse)
    async def update_live_config(cid: str, req: LiveConfigRequest):
        """Update an existing named live configuration (404 if unknown)."""
        return await _save_live_config(req, cid)

    @r.delete("/api/live-configs/{cid}", response_model=Ok)
    async def delete_live_config(cid: str):
        """Delete a live config (404 if unknown). Deleting the active one moves active to
        the next remaining config (or none — legacy fallback). Referenced Secrets are
        independent and untouched."""
        if d.live_store.get_config(cid) is None:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        d.live_store.delete_config(cid)
        return {"ok": True}

    @r.post("/api/live-configs/{cid}/use", response_model=Ok)
    async def use_live_config(cid: str):
        """Make ``cid`` the active live configuration (404 if unknown)."""
        if not d.live_store.set_active(cid):
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        return {"ok": True}

    @r.post("/api/live-configs/{cid}/test", response_model=PingResultResponse)
    async def test_live_config(cid: str):
        """Models-list key probe against a SAVED live config. 404 if unknown."""
        entry = d.live_store.get_config(cid)
        if entry is None:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        return await _ping_live(entry)

    return r

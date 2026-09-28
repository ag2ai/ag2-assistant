"""Per-profile settings: the panel behind the gear, the health roll-up behind the
status dot, the MCP server list and the voice picker.

Pairs with gateway/schemas/settings.py and web/src/schemas/settings.ts.

Everything here writes ``config.yaml`` through ``assistant/settings.py`` and then
reloads THIS profile only — a settings change is per-profile by definition, so
none of these routes fans a reload out the way an install-wide write does.
``/settings/live-override`` is the exception that reloads nothing: the voice
session reads its config fresh at connect, so the change lands on the next call.
"""

from collections.abc import Callable
from pathlib import Path

from ag2.config import OllamaConfig
from ag2.context import ConversationContext
from ag2.stream import MemoryStream
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from assistant import voice_providers
from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import (
    FocusesSavedResponse,
    HealthChannelOut,
    LiveOverrideSavedResponse,
    LlmOverrideSavedResponse,
    McpHealthResponse,
    McpServerSavedResponse,
    McpServersSnapshotResponse,
    Ok,
    ProfileHealthResponse,
    ProfileSettingsResponse,
    ReplyTimeoutSavedResponse,
    VoiceCatalogResponse,
    VoiceSelectedResponse,
)
from assistant.settings import profile_settings
from assistant.tools.mcp import build_mcp_tools, describe_mcp_error
from assistant.voice import synthesize_preview


class FocusesRequest(BaseModel):
    focuses: list[str] = []


class ModelOverrideRequest(BaseModel):
    # A selection into the shared install-wide list; empty string clears the override
    # (→ back to the install-wide Active). Used for both the Text and Live switchers.
    config_id: str = ""


class ReplyTimeoutRequest(BaseModel):
    reply_timeout_s: float = Field(gt=0, le=3600)


class VoiceRequest(BaseModel):
    voice: str
    # When set, the voice op targets a named live config (its provider/key, and
    # select persists onto that config) instead of the profile's legacy voice setting.
    config_id: str | None = None


class MCPServerRequest(BaseModel):
    name: str
    command: str
    args: list[str] | str = Field(default_factory=list)
    env: dict[str, str] | str | None = None
    cwd: str | None = None
    allowed_tools: list[str] | str = Field(default_factory=list)
    blocked_tools: list[str] | str = Field(default_factory=list)
    enabled: bool = True


class VoiceProviderRequest(BaseModel):
    provider: str


def runtime_settings(runtime: ProfileRuntime):
    """This profile's Settings, resolved from the runtime's derived config."""
    cfg = runtime.require_config()
    return profile_settings(cfg.data_dir, voice_provider=cfg.voice_provider)


def _ollama_installed() -> bool:
    try:
        return type(OllamaConfig).__module__ != "unittest.mock"
    except Exception:
        return False


async def _mcp_health(server: dict) -> dict:
    tools = build_mcp_tools([server])
    if not tools:
        return {"ok": False, "error": "MCP server is disabled"}
    toolkit = tools[0]
    context = ConversationContext(stream=MemoryStream())
    try:
        schemas = await toolkit.schemas(context)
        error = toolkit.last_error
    finally:
        # This throwaway toolkit's persistent session would otherwise hold the
        # server process alive until idle expiry.
        await toolkit.aclose()
    # Discovery reports failures rather than raising: an unreachable server
    # arrives here as a live toolkit offering zero tools.
    if error is not None:
        return {"ok": False, "error": describe_mcp_error(error)[:500]}
    return {
        "ok": True,
        "tools": [
            getattr(getattr(schema, "function", None), "name", "")
            for schema in schemas
            if getattr(getattr(schema, "function", None), "name", "")
        ],
    }


def build_profile_router(
    d: GatewayDeps,
    get_runtime,
    *,
    secret_env: Callable[[], dict[str, str]],
) -> APIRouter:
    """The /api/p/{pid} settings slice.

    ``secret_env`` stays a keyword collaborator rather than a ``GatewayDeps``
    field for the reason ``llm_probe`` does: it closes over the install config's
    ambient slice, which ``create_app`` owns and tests substitute per app.
    """
    r = APIRouter()

    def available_providers() -> dict:
        """Which providers have a usable key right now — key-only. This is what the
        VOICE endpoints need (the realtime APIs always talk to the provider's own
        endpoint, so a base_url never makes a provider available). Assistant model
        availability is per-config now and lives in the named LLM configs store."""
        st = d.secret_store.status(secret_env())
        avail = {prov: st[prov]["set"] for prov in ("openai", "gemini", "anthropic")}
        avail["ollama"] = _ollama_installed()
        return avail

    @r.get(
        "/health",
        response_model=ProfileHealthResponse,
        response_model_exclude_unset=True,
    )
    async def profile_health(runtime: ProfileRuntime = Depends(get_runtime)):
        """Cheap, at-a-glance health of this profile's subsystems — the source for
        the UI's status dot. Presence/liveness signals ONLY: no MCP subprocess
        spawns, no provider pings, so it's cheap enough to poll on a short cycle.
        MCP servers are listed (config only) and probed on demand by the client via
        ``/settings/mcp/{name}/health``.

        ``overall`` rolls up the *core* signals: ``down`` if the agent isn't alive or
        the configured provider has no key (the agent can't run); ``warn`` if a
        channel bound to this profile failed to start; else ``ok``. Google and the
        scheduler are informational and never move ``overall``.
        """
        checks: list[dict] = []

        # Assistant agent — liveness (agent object built + not closed).
        gw = runtime.gateway.status() if runtime.gateway is not None else {"status": "stopped"}
        agent_ok = gw.get("status") == "ok"
        checks.append(
            {
                "id": "agent",
                "label": "Assistant",
                "state": "ok" if agent_ok else "down",
                "detail": f"model {gw.get('model')}" if agent_ok else "not running",
            }
        )

        # LLM provider — the active named config must be usable (per-config key, a
        # base_url compat server, Ollama, or the provider's env key). When the store is
        # empty we fall back to the flat provider's key check (fresh install / CLI).
        entry = d.llm_store.active_config()
        if entry is not None:
            key_set = d.llm_store.usable(
                entry, secret_env(), search_path=d.search_path, bridge=d.acp_bridge
            )
            detail = f"{entry['name']} · {entry['model']}"
        else:
            provider = runtime.require_config().llm.provider
            key_set = available_providers().get(provider, False)
            detail = f"{provider} · {'key set' if key_set else 'no key'}"
        checks.append(
            {
                "id": "provider",
                "label": "LLM key",
                "state": "ok" if key_set else "down",
                "detail": detail,
            }
        )

        # MCP servers — config only; the client probes each on panel open.
        mcp_servers = runtime_settings(runtime).list_mcp_servers()
        enabled = [s for s in mcp_servers if s.get("enabled", True)]
        checks.append(
            {
                "id": "mcp",
                "label": "MCP servers",
                "state": "ok" if enabled else "off",
                "detail": (
                    f"{len(enabled)} configured"
                    if enabled
                    else ("all disabled" if mcp_servers else "none configured")
                ),
                "servers": [
                    {"name": s["name"], "enabled": s.get("enabled", True)} for s in mcp_servers
                ],
            }
        )

        # Connections DEFAULTING to this profile — the ones whose conversations land
        # here (start-time active/error). Connections themselves are install-level.
        defaults = d.registry.connection_defaults()
        # Built as the response model rather than as bare dicts: the detail string
        # below reads these fields back, and the model is what says `error` is a
        # string or absent while `active` is a flag.
        items = [
            HealthChannelOut(
                connection=c.id,
                name=c.name,
                platform=c.platform,
                active=c.id in d.manager.channels,
                error=d.manager.channel_errors.get(c.id),
            )
            for c in d.connection_store.list_connections()
            if defaults.get(c.id) == runtime.pid
        ]
        ch_error = any(it.error for it in items)
        checks.append(
            {
                "id": "channels",
                "label": "Messaging",
                "state": "off" if not items else ("warn" if ch_error else "ok"),
                # Surface the ACTUAL failure reason (e.g. "Improper token…"), not a
                # generic "error" — the panel shows this, so it must say what to fix.
                "detail": (
                    ", ".join(
                        (it.error or f"{it.name} active")
                        if (it.error or it.active)
                        else f"{it.name} idle"
                        for it in items
                    )
                    or "none default here"
                ),
                "items": items,
            }
        )

        # Google — informational (file-presence: configured / signed in).
        signed_in = d.google.has_token()
        email = d.google.account_email()
        checks.append(
            {
                "id": "google",
                "label": "Google",
                "state": "ok" if signed_in else "off",
                "detail": (
                    (f"signed in as {email}" if email else "signed in")
                    if signed_in
                    else (
                        "configured — not signed in"
                        if d.google.is_configured()
                        else "not connected"
                    )
                ),
            }
        )

        # Task scheduler — informational; single-leader across processes.
        sched_running = bool(getattr(runtime.tasks, "scheduler_running", False))
        checks.append(
            {
                "id": "scheduler",
                "label": "Task scheduler",
                "state": "ok",
                "detail": "running" if sched_running else "running in another process",
            }
        )

        core_down = any(c["state"] == "down" for c in checks if c["id"] in ("agent", "provider"))
        core_warn = any(c["state"] == "warn" for c in checks if c["id"] == "channels")
        overall = "down" if core_down else ("warn" if core_warn else "ok")
        return {"overall": overall, "checks": checks}

    # ---- Voice picker: list voices, select (persist), preview (TTS) ----

    @r.get("/voice/voices", response_model=VoiceCatalogResponse)
    async def voice_voices(
        config_id: str | None = None, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """The voice catalogue + current selection. Scoped to a named live config when
        ``config_id`` is given (its provider + persisted voice); otherwise the profile's
        legacy voice-provider setting."""
        settings = runtime_settings(runtime)
        entry = d.live_store.get_config(config_id) if config_id else None
        provider = entry["provider"] if entry else settings.voice_provider()
        p_v = voice_providers.get(provider)
        current = entry.get("voice") if entry else settings.get_voice(provider)
        return {
            "voices": [{"name": n, "style": s} for n, s in p_v.voices.items()],
            "current": current,
            "provider": provider,
            "input_rate": p_v.input_rate,  # mic capture rate the client should use
        }

    @r.post("/voice/select", response_model=VoiceSelectedResponse)
    async def voice_select(req: VoiceRequest, runtime: ProfileRuntime = Depends(get_runtime)):
        """Persist the chosen voice — onto the named live config when ``config_id`` is
        given, else the profile's legacy per-provider voice setting."""
        if req.config_id:
            if not d.live_store.set_voice(req.config_id, req.voice):
                return Response(status_code=400)
        elif not runtime_settings(runtime).set_voice(req.voice):
            return Response(status_code=400)
        return {"ok": True, "voice": req.voice}

    @r.post("/voice/preview", response_model=None)
    async def voice_preview(req: VoiceRequest, runtime: ProfileRuntime = Depends(get_runtime)):
        """Synthesise a sample clip of one voice. Answers audio/wav bytes rather
        than JSON, hence ``response_model=None`` — the one route in this module
        with nothing for the contract to describe."""
        settings = runtime_settings(runtime)
        entry = d.live_store.get_config(req.config_id) if req.config_id else None
        provider = entry["provider"] if entry else None
        api_key = d.live_store.resolve_key(entry, secret_env()) if entry else ""
        # Validate the voice against the target provider's catalogue.
        catalog = voice_providers.get(provider or settings.voice_provider()).voices
        if req.voice not in catalog:
            return Response(status_code=400)
        try:
            wav = await synthesize_preview(
                runtime.require_config(), settings, req.voice, provider=provider, api_key=api_key
            )
        except Exception as exc:
            return Response(content=str(exc)[:200], status_code=502)
        return Response(content=wav, media_type="audio/wav")

    # ---- Settings ----

    @r.get(
        "/settings",
        response_model=ProfileSettingsResponse,
        response_model_exclude_unset=True,
    )
    async def get_settings(runtime: ProfileRuntime = Depends(get_runtime)):
        """Everything the settings panel renders, in one fetch.

        ``exclude_unset`` because a provider's key entry carries either a ``hint``
        or a ``base_url``, never both, and an MCP row's ``env_keys`` is optional on
        the wire — shipping the missing half as ``null`` is what the zod twin
        rejects.
        """
        cfg = runtime.require_config()
        settings = runtime_settings(runtime)
        keys = d.secret_store.status(secret_env())
        # Per-profile model Active override (ADR 0015): report BOTH this profile's
        # override id (None when inherited or dangling) and the EFFECTIVE Active id, so
        # each header switcher can render the current choice + mark it
        # inherited-vs-overridden without a second fetch. A dangling override reads as
        # no override → the install-wide Active (matching the resolution layer).
        llm_ovr = d.llm_store.resolved_override(settings.get_llm_override()) or None
        live_ovr = settings.get_live_override()
        live_ovr = live_ovr if (live_ovr and d.live_store.get_config(live_ovr)) else None
        return {
            "keys": keys,  # per-provider {set, hint} — never raw
            # Voice runs on the provider's own realtime endpoint, so a base_url
            # never makes it available — keys only.
            "voice_available": {prov: keys[prov]["set"] for prov in ("gemini", "openai")},
            # Display-only view of the resolved assistant model (the active named LLM
            # config, derived onto cfg.llm). Managed via /api/llm-configs, not here.
            "assistant": {"provider": cfg.llm.provider, "model": cfg.llm.model},
            # Per-profile Text/Live Active override + effective Active (drives the
            # Profiles-header switchers). override=None → inherits the install-wide.
            "llm_override": llm_ovr,
            "llm_active": d.llm_store.effective_active_id(llm_ovr) or None,
            "live_override": live_ovr,
            "live_active": live_ovr or d.live_store.active_id(),
            "codex": d.codex.status(),  # ChatGPT-subscription sign-in state
            "voice_provider": settings.voice_provider(),
            "mcp_servers": settings.list_mcp_servers(),
            "focuses": settings.get_focuses(),  # per-profile persona focus areas
            "reply_timeout_s": cfg.gateway.reply_timeout_s,
            "fs": {  # start roots for the folder picker
                "home": str(d.paths.home),
                "cwd": str(Path.cwd()),
                "workspace": str(Path(cfg.workspace_dir).expanduser()),
            },
        }

    @r.post(
        "/settings/mcp",
        response_model=McpServerSavedResponse,
        response_model_exclude_unset=True,
    )
    async def add_mcp_server(req: MCPServerRequest, runtime: ProfileRuntime = Depends(get_runtime)):
        settings = runtime_settings(runtime)
        try:
            server = settings.upsert_mcp_server(req.model_dump())
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        await d.manager.reload(runtime.pid)
        return {"ok": True, "server": server, "mcp_servers": settings.list_mcp_servers()}

    @r.delete(
        "/settings/mcp/{name}",
        response_model=McpServersSnapshotResponse,
        response_model_exclude_unset=True,
    )
    async def delete_mcp_server(name: str, runtime: ProfileRuntime = Depends(get_runtime)):
        settings = runtime_settings(runtime)
        if not settings.delete_mcp_server(name):
            return Response(status_code=404)
        await d.manager.reload(runtime.pid)
        return {"ok": True, "mcp_servers": settings.list_mcp_servers()}

    @r.post("/settings/mcp/{name}/health", response_model=McpHealthResponse)
    async def health_mcp_server(name: str, runtime: ProfileRuntime = Depends(get_runtime)):
        server = next(
            (
                s
                for s in runtime_settings(runtime).list_mcp_servers(include_env=True)
                if s["name"] == name
            ),
            None,
        )
        if server is None:
            return Response(status_code=404)
        try:
            return await _mcp_health(server)
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:500]}

    @r.post("/settings/focuses", response_model=FocusesSavedResponse)
    async def set_focuses(req: FocusesRequest, runtime: ProfileRuntime = Depends(get_runtime)):
        """Persist this profile's focus areas (a persona attribute injected into the
        agent's context), then reload so the reference-swapped agent picks up the new
        context line on its next turn."""
        settings = runtime_settings(runtime)
        focuses = settings.set_focuses(req.focuses)
        await d.manager.reload(runtime.pid)  # context change → next turn gets the line
        return {"ok": True, "focuses": focuses}

    @r.post("/settings/llm-override", response_model=LlmOverrideSavedResponse)
    async def set_llm_override(
        req: ModelOverrideRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Set (or clear, when ``config_id`` is empty) this profile's Active Text model
        override — a selection into the shared install-wide ``llm_configs`` list, NOT the
        install-wide Active (the composer switcher still owns that). Reloads this
        profile's runtime so its next message uses the new model. Unknown id → 404."""
        settings = runtime_settings(runtime)
        cid = (req.config_id or "").strip()
        if cid and d.llm_store.get_config(cid) is None:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        settings.set_llm_override(cid)
        await d.manager.reload(runtime.pid)  # next turn's agent is built from the new model
        return {"ok": True, "llm_override": cid or None}

    @r.post("/settings/live-override", response_model=LiveOverrideSavedResponse)
    async def set_live_override(
        req: ModelOverrideRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Set (or clear) this profile's Active Live (voice) model override — a
        selection into the shared install-wide ``live_configs`` list. Read fresh by the
        voice session at connect, so it takes effect on the NEXT voice session (no
        runtime reload needed). Unknown id → 404."""
        settings = runtime_settings(runtime)
        cid = (req.config_id or "").strip()
        if cid and d.live_store.get_config(cid) is None:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        settings.set_live_override(cid)
        return {"ok": True, "live_override": cid or None}

    @r.post("/settings/reply-timeout", response_model=ReplyTimeoutSavedResponse)
    async def set_reply_timeout(
        req: ReplyTimeoutRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Persist this profile's chat-turn timeout and reload its runtime."""
        timeout = runtime_settings(runtime).set_reply_timeout(req.reply_timeout_s)
        await d.manager.reload(runtime.pid)
        return {"ok": True, "reply_timeout_s": timeout}

    @r.post("/settings/voice_provider", response_model=Ok)
    async def set_settings_voice_provider(
        req: VoiceProviderRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        provider = req.provider.lower()
        if not available_providers().get(provider):
            return JSONResponse(
                {"ok": False, "error": f"Add the {provider} API key first."}, status_code=409
            )
        if not runtime_settings(runtime).set_voice_provider(provider):
            return Response(status_code=400)
        return {"ok": True}

    return r

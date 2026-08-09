"""Per-profile settings surfaces. Phase 1 carries only the health roll-up
(GET /api/p/{pid}/health); the rest of this module arrives with phase 7.

Pairs with gateway/schemas/settings.py and web/src/schemas/settings.ts.
"""

from collections.abc import Callable

from fastapi import APIRouter, Depends

from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import ProfileHealthResponse


def build_profile_router(
    d: GatewayDeps,
    get_runtime,
    *,
    secret_env: Callable[[], dict[str, str]],
    available_providers: Callable[[], dict],
    runtime_settings: Callable[[ProfileRuntime], object],
) -> APIRouter:
    """The /api/p/{pid} settings slice.

    The three keyword collaborators are still create_app's: ``secret_env`` and
    ``available_providers`` close over stores this module has no other claim on,
    and ``runtime_settings`` has a dozen callers among the routes phase 7 will
    move. Passing them keeps this a move rather than a rewrite — and keeps the
    import arrow pointing one way, since a route module never imports app.py.
    """
    r = APIRouter()

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
            provider = runtime.config.llm.provider
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
        items = [
            {
                "connection": c.id,
                "name": c.name,
                "platform": c.platform,
                "active": c.id in d.manager.channels,
                "error": d.manager.channel_errors.get(c.id),
            }
            for c in d.connection_store.list_connections()
            if defaults.get(c.id) == runtime.pid
        ]
        ch_error = any(it["error"] for it in items)
        checks.append(
            {
                "id": "channels",
                "label": "Messaging",
                "state": "off" if not items else ("warn" if ch_error else "ok"),
                # Surface the ACTUAL failure reason (e.g. "Improper token…"), not a
                # generic "error" — the panel shows this, so it must say what to fix.
                "detail": (
                    ", ".join(
                        (it["error"] or f"{it['name']} active")
                        if (it["error"] or it["active"])
                        else f"{it['name']} idle"
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

    return r

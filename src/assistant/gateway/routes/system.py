"""Install-wide status surfaces: health, usage, activity, coding agents, the
folder picker, the shared memory document, first-run identity seeding, and the
two account-level OAuth cards (Google and the ChatGPT subscription).

The OAuth pairs live here rather than in a module of their own because a module
follows its zod twin, and GoogleStatus / CodexStatus / CodexLoginUrl /
GoogleLoginUrl are all declared in web/src/schemas/system.ts.

Pairs with gateway/schemas/system.py (the response models) and
web/src/schemas/system.ts (their zod twins) — same file name in all three trees.
"""

import asyncio
import secrets as _secrets
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from assistant.codex_auth import (
    CodexAuthError,
    build_authorize_url,
    extract_auth_code,
    generate_pkce,
)
from assistant.coding.model_catalog import as_view
from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.routes.common import reload_all
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import (
    CodexLoginUrlResponse,
    CodexStatusResponse,
    CodingAgentsResponse,
    CodingCatalogResponse,
    FsListingErrorOut,
    FsListingOkOut,
    FsMkdirResponse,
    GoogleCredentialsErrorOut,
    GoogleCredentialsOkOut,
    GoogleLoginUrlErrorOut,
    GoogleLoginUrlOkOut,
    GoogleStatusResponse,
    HealthResponse,
    IdentitySeededResponse,
    MemoryDocResponse,
    Ok,
    StatusRowOut,
    UsageResponse,
    UsageRollupResponse,
)
from assistant.memory import read_profile, read_universal, write_profile, write_universal
from assistant.onboarding import identity_document
from assistant.workspace import invalid_dir_name, list_dirs, make_dir


class FsMkdirRequest(BaseModel):
    """Create one subfolder inside a host directory the folder picker is viewing. Distinct
    from `MkdirRequest`: that one writes into the Files space / a granted Folder, whereas
    this runs BEFORE any Grant exists (you're choosing the folder to grant), so it is
    host-scoped like its sibling `GET /api/fs/list`. `name` is a single component, not a
    path."""

    path: str
    name: str


class OnboardedRequest(BaseModel):
    value: bool = True


class MemoryRequest(BaseModel):
    text: str


class IdentityRequest(BaseModel):
    """Identity answers collected in web onboarding (all optional). Seed the shared
    universal "who the user is" doc, replacing the CLI first-chat interview."""

    name: str | None = None
    location: str | None = None
    hours: str | None = None
    style: str | None = None


class CredentialsUpload(BaseModel):
    content: str  # raw OAuth client JSON


class CodexCodeRequest(BaseModel):
    """Headless ChatGPT-subscription sign-in: a pasted auth code + its flow state."""

    state: str
    code: str


async def _activity(runtime: ProfileRuntime) -> tuple[int, int]:
    """Per-profile activity for the chip badges, from a single store scan (v2:
    a Task is standing config, a Run is one execution — this scans runs, not tasks).

    Returns ``(running, unseen_done)``:
      * ``running``     — runs not yet in a terminal state (RUNNING or NEEDS_INPUT).
      * ``unseen_done`` — finished, not-yet-opened runs: the count behind the chip's
        "unread results" dot. Mirrors the nav's per-row unread marker (``isUnread`` =
        terminal status && not seen), rolled up to the profile.
    """
    tasks = runtime.tasks
    store = getattr(tasks, "store", None) if tasks is not None else None
    if store is None:
        return 0, 0
    try:
        from assistant.tasks import RunStatus

        runs = await store.list_runs()
        running = sum(1 for r in runs if r.status not in RunStatus.TERMINAL)
        unseen_done = sum(1 for r in runs if r.status in RunStatus.TERMINAL and r.seen_at is None)
        return running, unseen_done
    except Exception:
        return 0, 0


def build_router(d: GatewayDeps, *, code_reader: Callable[[str], str]) -> APIRouter:
    """The install-level routes. Registration order below is the order these
    handlers had in app.py — see the plan's constraint on route order.

    ``code_reader`` is create_app's, not a store: it is how the ChatGPT sign-in
    flow waits for the OAuth redirect, and the default runs a real loopback
    listener, so every test that touches that flow swaps it.
    """
    r = APIRouter()

    @r.get("/api/health", response_model=HealthResponse, response_model_exclude_unset=True)
    async def health():
        """Process-level status: the first running runtime's gateway status, or a
        zero-profile stub (fresh install, §3.5)."""
        runtime = next(d.manager.runtimes(), None)
        if runtime is None or runtime.gateway is None:
            return {"status": "ok", "profiles": 0}
        return runtime.gateway.status()

    @r.get(
        "/api/coding/agents",
        response_model=CodingAgentsResponse,
        response_model_exclude_unset=True,
    )
    async def coding_agents():
        """Read-only status of CLI coding agents (for the Settings "Coding agents"
        card). In Docker with ``AG2ASSISTANT_ACP_BRIDGE`` set, reports the host
        bridge and the agents it exposes; otherwise the locally-installed agents.
        Never raises — an unreachable bridge is reported as ``connected: false``.
        """
        from assistant.coding import detect

        endpoint = d.acp_bridge
        if endpoint is None:
            agents = [
                {"name": a.name, "label": a.label, "available": a.available}
                for a in detect.detect_agents(d.search_path)
            ]
            return {"mode": "local", "bridge": None, "connected": True, "agents": agents}

        from assistant.coding.bridge_client import BridgeClient

        target = f"{endpoint.host}:{endpoint.port}"
        try:
            inventory = await BridgeClient(endpoint).list_agents()
        except Exception as exc:  # noqa: BLE001 — surface as a disconnected status
            return {
                "mode": "bridge",
                "bridge": target,
                "connected": False,
                "error": str(exc),
                "agents": [],
            }
        agents = [{"name": a.name, "label": a.label, "available": a.available} for a in inventory]
        return {"mode": "bridge", "bridge": target, "connected": True, "agents": agents}

    @r.get("/api/usage", response_model=UsageRollupResponse)
    async def usage():
        """Install-wide token/cost roll-up across ALL running profiles (for the HUD's
        "all profiles" total). ``profiles`` is one ``usage_today()`` snapshot per
        running runtime (with its ``pid``/``name``); ``total`` sums the numeric fields.

        ``total.priced`` is true only when EVERY contributing profile is priced — an
        unpriced profile means its tokens carry no cost, so the summed ``cost`` is an
        underestimate and the flag says so (matching the per-profile flag semantics and
        the HUD's "no price set" fallback). Archived profiles aren't running, so they're
        naturally excluded. Zero profiles → empty list + a zeroed total.
        """
        rows = []
        total = {"prompt": 0.0, "completion": 0.0, "total": 0.0, "cost": 0.0}
        all_priced = True
        any_profile = False
        for runtime in d.manager.runtimes():
            if runtime.gateway is None:
                continue
            any_profile = True
            today = runtime.gateway.usage_today()
            rows.append({"pid": runtime.pid, "name": runtime.meta.name, **today})
            for k in total:
                total[k] += today.get(k) or 0
            if not today.get("priced"):
                all_priced = False
        # Zero profiles (or none priced) → not priced. With profiles present, priced iff
        # every one is priced (an unpriced profile makes the summed cost incomplete).
        total["priced"] = bool(any_profile and all_priced)
        return {"profiles": rows, "total": total}

    @r.get("/api/status", response_model=list[StatusRowOut])
    async def status():
        """Per-profile activity for badges: busy = agent alive, running_tasks = count
        of RUNNING tasks, unseen_done = finished-but-not-yet-opened root tasks (the
        chip's unread-results dot). Aggregated over the running runtimes."""
        out = []
        for runtime in d.manager.runtimes():
            gw_status = runtime.gateway.status() if runtime.gateway is not None else {}
            running, unseen_done = await _activity(runtime)
            out.append(
                {
                    "pid": runtime.pid,
                    "busy": gw_status.get("status") == "ok",
                    "running_tasks": running,
                    "unseen_done": unseen_done,
                }
            )
        return out

    @r.post("/api/onboarded", response_model=Ok)
    async def set_onboarded(req: OnboardedRequest):
        """Mark first-run onboarding completed/dismissed (install-level, in the registry)."""
        d.registry.set_onboarded(req.value)
        return {"ok": True}

    # ---- Universal memory: the shared "who the user is" doc (root/user.db) ----

    def _user_store_path() -> Path:
        """The install-wide universal memory DB — the SAME file every profile's agent
        reads (``root_dir/user.db``). Profile-agnostic, so resolved from the root config."""
        return d.paths.root / "user.db"

    @r.get("/api/memory", response_model=MemoryDocResponse)
    async def get_universal_memory():
        """Read the shared universal "who the user is" document (identity facts injected
        into EVERY profile's context). Mirrors the per-profile GET /api/p/{pid}/memory."""
        return {"text": await read_universal(_user_store_path())}

    @r.post("/api/memory", response_model=Ok)
    async def set_universal_memory(req: MemoryRequest):
        """Replace the shared universal document (a user edit from any profile's Settings →
        Memory). Read fresh per turn, so all profiles' agents pick it up next turn."""
        await write_universal(req.text, _user_store_path())
        return {"ok": True}

    @r.post(
        "/api/identity",
        response_model=IdentitySeededResponse,
        response_model_exclude_unset=True,
    )
    async def seed_identity(req: IdentityRequest):
        """Seed the universal "who the user is" doc from web-onboarding identity answers
        (name/location/hours/style, all optional). Formats them with the SAME
        `identity_document` helper the CLI interview uses, so both surfaces produce an
        identical doc. Onboarding semantics: this only ever *seeds* — if the universal
        store already holds a doc it is left untouched (returns ``seeded: false``), and
        if every field is empty nothing is written (also ``seeded: false``). This is why
        a web-onboarded user's first chat never triggers the in-chat interview: the
        store is already seeded, so `needs_onboarding` is false."""
        doc = identity_document(req.model_dump())
        if not doc:
            return {"ok": True, "seeded": False, "reason": "empty"}
        path = _user_store_path()
        if (await read_universal(path)).strip():
            return {"ok": True, "seeded": False, "reason": "exists"}
        await write_universal(doc, path)
        return {"ok": True, "seeded": True}

    @r.get("/api/coding/{agent}/models", response_model=CodingCatalogResponse)
    async def coding_models(agent: str, refresh: bool = False) -> Response:
        """An ACP adapter's model catalog (agent: "claude" | "codex"), for the
        Settings model picker: ``{models, current, reason}``. Lazy + guarded — a
        missing adapter, bridge mode or a broken probe all read as an empty
        catalog, but ``reason`` says WHICH, so the form explains itself instead of
        silently degrading to a free-text field. ``?refresh=1`` skips the TTL cache."""
        if agent not in ("claude", "codex"):
            return JSONResponse({"ok": False, "error": f"unknown agent: {agent}"}, status_code=404)
        reason = d.catalog.unavailable_reason(agent)
        if reason:  # nothing to spawn — don't pay for a probe that can't work
            return JSONResponse(as_view([], "", reason))
        try:
            models, current = await d.catalog.list_models(agent, refresh=refresh)
        except Exception:
            return JSONResponse(as_view([], "", "probe_failed"))
        return JSONResponse(as_view(models, current, "" if models else "probe_failed"))

    @r.get("/api/fs/list", response_model=FsListingOkOut | FsListingErrorOut)
    async def fs_list(path: str = ""):
        """List immediate subdirectories of a host path — drives the folder picker. The
        gateway is local + single-user and `_origin_guard` blocks cross-origin, so this is
        safe; dotfolders are hidden. Empty path starts at home."""
        result = list_dirs(path or str(d.paths.home))
        if result is None:
            return {"ok": False, "error": "not a readable directory"}
        return {"ok": True, **result}

    @r.post("/api/fs/mkdir", response_model=FsMkdirResponse)
    async def fs_mkdir(req: FsMkdirRequest):
        """Create ONE subfolder inside the host directory the picker is viewing, so a
        working folder can be made without leaving the app. Same trust model as
        `fs_list` above (local + single-user, `_origin_guard` blocks cross-origin).

        Returns the new folder's ABSOLUTE path — `make_dir` reports a root-relative one,
        but the picker navigates by absolute path."""
        if list_dirs(req.path) is None:
            return JSONResponse({"error": "not a readable directory"}, status_code=400)
        if (why := invalid_dir_name(req.name)) is not None:
            return JSONResponse({"error": why}, status_code=400)

        status, _rel = make_dir(req.path, req.name)
        if status == "ok":
            return {"ok": True, "path": str(Path(req.path).expanduser().resolve() / req.name)}
        code, msg = (
            (409, "A folder with that name already exists")
            if status == "exists"
            else (400, "invalid path")
        )
        return JSONResponse({"error": msg}, status_code=code)

    # ---- Google OAuth (global, account-level) ----

    @r.get("/api/google/status", response_model=GoogleStatusResponse)
    async def google_status():
        libs = d.google.libs_available()
        return {
            "configured": d.google.is_configured(),
            "signed_in": d.google.has_token(),
            "email": d.google.account_email(),
            # A token without the optional [google] extra looks connected but can
            # do nothing — the UI shows the remedy instead of a healthy state.
            "libs_available": libs,
            "install_hint": None if libs else d.google.install_hint(),
        }

    @r.post(
        "/api/google/credentials",
        response_model=GoogleCredentialsOkOut | GoogleCredentialsErrorOut,
    )
    async def google_credentials(payload: CredentialsUpload):
        """Save an uploaded OAuth client JSON (so users avoid the filesystem)."""
        try:
            d.google.save_credentials_json(payload.content)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    @r.post(
        "/api/google/login_url",
        response_model=GoogleLoginUrlOkOut | GoogleLoginUrlErrorOut,
    )
    async def google_login_url(request: Request):
        """Build a Google consent URL whose redirect returns to this gateway.

        The user opens the URL (web button or a channel link). AG2ASSISTANT_PUBLIC_URL
        overrides the redirect base when the gateway is reachable at a public URL
        (so the round-trip can complete from another device).
        """
        if not d.google.is_configured():
            return {"ok": False, "error": "No OAuth client configured."}
        base = d.manager.env.get("AG2ASSISTANT_PUBLIC_URL") or str(request.base_url)
        redirect_uri = base.rstrip("/") + "/api/google/callback"
        try:
            auth_url, state, flow = await asyncio.to_thread(d.google.make_login_flow, redirect_uri)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        request.app.state.google_flows[state] = flow
        return {"ok": True, "auth_url": auth_url}

    @r.get("/api/google/callback", response_class=HTMLResponse)
    async def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
        def _page(title, msg):
            return (
                f"<!doctype html><meta charset=utf-8><title>{title}</title>"
                "<body style='font-family:system-ui;max-width:520px;margin:14vh auto;"
                "text-align:center;color:#171717'>"
                f"<h1 style='color:#f95339'>{title}</h1><p>{msg}</p>"
                "<p style='color:#737373'>You can close this tab.</p></body>"
            )

        if error:
            return HTMLResponse(_page("Cancelled", f"Google returned: {error}"))
        flow = request.app.state.google_flows.pop(state, None)
        if flow is None or not code:
            return HTMLResponse(_page("Expired", "This sign-in link is no longer valid."))
        try:
            email = await asyncio.to_thread(d.google.complete_login, flow, code)
        except Exception as exc:
            return HTMLResponse(_page("Sign-in failed", str(exc)))
        # Google tools are gated on has_token() at agent build time — reference-swap
        # reload every runtime so Gmail/Calendar/Drive attach on the next turn.
        await reload_all(d.manager)
        return HTMLResponse(_page("Connected ✓", f"AG2 Assistant is now connected to {email}."))

    @r.post("/api/google/logout", response_model=Ok)
    async def google_logout():
        ok = d.google.logout()
        # Drop the Google tools from every runtime immediately (same gate, reversed).
        await reload_all(d.manager)
        return {"ok": ok}

    # ---- OpenAI ChatGPT-subscription OAuth ("Sign in with ChatGPT") ----
    # Unofficial / gray-area vs OpenAI ToS — see assistant.codex_auth. The flow is a
    # loopback (localhost:1455) OAuth; the gateway is local + single-user, so it can
    # run the callback capture itself. Headless setups paste the code via /submit.

    @r.get("/api/codex/status", response_model=CodexStatusResponse)
    async def codex_status():
        return d.codex.status()

    @r.post("/api/codex/login_url", response_model=CodexLoginUrlResponse)
    async def codex_login_url(request: Request):
        """Begin a ChatGPT sign-in: return the consent URL for the UI to open, and
        start a background loopback listener (localhost:1455) that completes the flow
        when OpenAI redirects back. The UI polls GET /api/codex/status."""
        verifier, challenge = generate_pkce()
        state = _secrets.token_urlsafe(24)
        flows = request.app.state.codex_flows
        flows[state] = verifier
        url = build_authorize_url(challenge, state)

        async def _complete() -> None:
            try:
                code = await asyncio.to_thread(code_reader, state)
            except Exception:
                return  # loopback failed/timed out — leave the flow for /submit (headless)
            if flows.pop(state, None) is None:
                return  # already completed via /submit
            try:
                await asyncio.to_thread(d.codex.exchange_code, code, verifier)
            except Exception:
                return
            await reload_all(d.manager)

        asyncio.create_task(_complete())
        return {"ok": True, "auth_url": url, "state": state}

    @r.post("/api/codex/submit", response_model=Ok)
    async def codex_submit(request: Request, payload: CodexCodeRequest):
        """Headless fallback: exchange a manually pasted auth code for the flow's
        pending PKCE verifier. Used when the loopback callback can't reach the box
        (e.g. Docker/remote) — the user copies the ``code`` from the redirect URL."""
        verifier = request.app.state.codex_flows.pop(payload.state, None)
        if verifier is None:
            return JSONResponse(
                {"ok": False, "error": "unknown or expired sign-in"}, status_code=400
            )
        # Accept either the bare code or the whole redirect URL the user copied out
        # of the browser's address bar (even off the "connection refused" page).
        code = extract_auth_code(payload.code)
        try:
            await asyncio.to_thread(d.codex.exchange_code, code, verifier)
        except CodexAuthError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        await reload_all(d.manager)
        return {"ok": True}

    @r.post("/api/codex/logout", response_model=Ok)
    async def codex_logout():
        ok = d.codex.logout()
        await reload_all(d.manager)
        return {"ok": ok}

    return r


def build_profile_router(d: GatewayDeps, get_runtime) -> APIRouter:
    """The /api/p/{pid} slice: this profile's memory document and today's spend."""
    r = APIRouter()

    # ---- Memory: view + edit THIS profile's persona memory (profile.db) ----
    # (The shared universal "who the user is" doc is the global GET/POST /api/memory.)

    @r.get("/memory", response_model=MemoryDocResponse)
    async def get_memory(runtime: ProfileRuntime = Depends(get_runtime)):
        return {"text": await read_profile(runtime.require_config().data_dir / "profile.db")}

    @r.post("/memory", response_model=Ok)
    async def set_memory(req: MemoryRequest, runtime: ProfileRuntime = Depends(get_runtime)):
        await write_profile(req.text, runtime.require_config().data_dir / "profile.db")
        return {"ok": True}

    @r.get("/usage", response_model=UsageResponse)
    async def usage_today(runtime: ProfileRuntime = Depends(get_runtime)):
        """Today's token + estimated-cost totals (cost & activity HUD)."""
        return runtime.require_gateway().usage_today()

    return r

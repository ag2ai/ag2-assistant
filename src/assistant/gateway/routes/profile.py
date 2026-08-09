"""Profiles: the registry every client boots from, and its lifecycle.

A profile is a workspace with its own agent, memory and settings (§3.5). These
routes are install-level and never scoped to one — creating, renaming, archiving
and restoring a profile is something the install does to it, so none of them sit
under /api/p/{pid}.

Pairs with gateway/schemas/profile.py (the response models) and
web/src/schemas/profile.ts (their zod twins) — same file name in all three trees.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from assistant import AG2_VERSION, __version__
from assistant.gateway.profile_manager import ArchivedProfile, UnknownProfile
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import Ok, ProfileEnvelopeResponse, ProfileListResponse


class ProfileCreateRequest(BaseModel):
    name: str
    accent: str


class ProfileUpdateRequest(BaseModel):
    name: str | None = None
    accent: str | None = None


class ProfileArchiveRequest(BaseModel):
    new_default: str | None = None


def build_router(d: GatewayDeps) -> APIRouter:
    """The install-level profile routes, in the order they had in app.py."""
    r = APIRouter()

    def _profile_view(meta) -> dict:
        return {
            "id": meta.id,
            "name": meta.name,
            "accent": meta.accent,
            "workspace": str(d.paths.profile_dir(meta.id) / "workspace"),
            "created": meta.created,
        }

    @r.get("/api/profiles", response_model=ProfileListResponse)
    async def list_profiles():
        """The §3.5 contract, present in every state: unarchived profiles, the
        server-side active default, and the install-level onboarded flag. Empty list +
        null + false on fresh install. Channel bindings are install-level now — see
        GET /api/connections."""
        reg = d.registry.load_registry()
        allp = d.registry.list_profiles(include_archived=True)
        return {
            "profiles": [_profile_view(m) for m in allp if not m.archived],
            # Archived profiles for the Settings "Archived" section (ADR 0003): restore
            # or permanently delete them. Empty on a fresh install / when none archived.
            "archived": [_profile_view(m) for m in allp if m.archived],
            "active_default": reg.get("active_default"),
            "onboarded": bool(reg.get("onboarded")),
            # Versions ride the boot payload so the UI needn't make a second request.
            "version": __version__,
            "ag2_version": AG2_VERSION,
        }

    @r.post("/api/profiles", response_model=ProfileEnvelopeResponse)
    async def create_profile(req: ProfileCreateRequest):
        """Create a profile (dir + registry) and boot its runtime live (§3.5)."""
        try:
            runtime = await d.manager.create(req.name, req.accent)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"profile": _profile_view(runtime.meta)}

    @r.post("/api/profiles/{pid}", response_model=ProfileEnvelopeResponse)
    async def update_profile(pid: str, req: ProfileUpdateRequest):
        """Rename and/or set accent (both display-only, registry-level). Unknown pid →
        404, invalid value → 400."""
        if d.registry.get_profile(pid) is None:
            return JSONResponse({"error": f"unknown profile: {pid}"}, status_code=404)
        try:
            if req.name is not None:
                d.registry.rename_profile(pid, req.name)
            if req.accent is not None:
                d.registry.set_accent(pid, req.accent)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"profile": _profile_view(d.registry.get_profile(pid))}

    @r.delete("/api/profiles/{pid}", response_model=Ok)
    async def archive_or_purge_profile(
        pid: str, purge: bool = False, req: ProfileArchiveRequest | None = None
    ):
        """Soft-archive by default; hard-delete when ``?purge=true`` (ADR 0003).

        Archive: §4.9 guardrails — ValueError (guardrail) → 400, unknown → 404, already
        archived → 410. new_default may come in the body.

        Purge (``?purge=true``): permanently erase an ALREADY-archived profile. Unknown →
        404; a live (not-yet-archived) profile → 409 (archive-first). The explicit flag
        makes the soft→hard escalation deliberate."""
        if purge:
            try:
                await d.manager.purge(pid)
            except UnknownProfile:
                return JSONResponse({"error": f"unknown profile: {pid}"}, status_code=404)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=409)
            return {"ok": True}

        new_default = req.new_default if req is not None else None
        try:
            await d.manager.archive(pid, new_default=new_default)
        except UnknownProfile:
            return JSONResponse({"error": f"unknown profile: {pid}"}, status_code=404)
        except ArchivedProfile:
            return JSONResponse({"error": f"profile archived: {pid}"}, status_code=410)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"ok": True}

    @r.post("/api/profiles/{pid}/restore", response_model=ProfileEnvelopeResponse)
    async def restore_profile(pid: str):
        """Un-archive a profile and boot it live (ADR 0003). Unknown → 404; a live
        (non-archived) profile → 409; a boot failure rolls the archive flag back and
        surfaces 500."""
        try:
            runtime = await d.manager.restore(pid)
        except UnknownProfile:
            return JSONResponse({"error": f"unknown profile: {pid}"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except Exception as exc:  # boot failed; manager already rolled back to archived
            return JSONResponse({"error": f"could not restore profile: {exc}"}, status_code=500)
        return {"profile": _profile_view(runtime.meta)}

    return r

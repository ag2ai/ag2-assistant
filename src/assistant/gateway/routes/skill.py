"""Skills: the install-wide Enable/Disable surface (ADR 0016) and the
registry/git/upload install flows (ADR 0017), plus their per-profile mirrors.

Both surfaces live here because they are one domain seen from two scopes, and
every helper below is shared by both — only the TARGET differs. An
``/api/skills*`` route installs into the Global layer and fans a reload out to
every profile; the mirrored ``/api/p/{pid}/skills*`` route installs into that
profile's own dir and reloads it alone. The helpers are module-level rather than
closures because the two routers are two factories.

Pairs with gateway/schemas/skill.py (the response models) and
web/src/schemas/skill.ts (their zod twins) — same file name in all three trees.
"""

import asyncio
import os
import tempfile
from pathlib import Path

from ag2.exceptions import SkillDownloadError, SkillError, SkillInstallError
from ag2.tools.skills.skill_search.client import SkillsClient
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from assistant.agent import build_skills_runtime, bundled_skills_dir
from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.routes.common import reload_all
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import (
    ProfileSkillInstalledResponse,
    ProfileSkillListResponse,
    ProfileSkillMutatedResponse,
    SkillDiscoveredResponse,
    SkillInstalledResponse,
    SkillListResponse,
    SkillMutatedResponse,
    SkillSearchResultsResponse,
)
from assistant.skills import (
    DISABLE_OWN,
    ORIGIN_BUNDLED,
    ORIGIN_GLOBAL,
    ORIGIN_PROFILE,
    SUPPRESS_SHARED,
    SkillStateStore,
    skill_origin,
)
from assistant.skills_install import (
    SkillSourceError,
    discover_source,
    install_from_source,
    registry_install,
    registry_search,
)

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB raw upload cap

# Every install/discover failure maps to a 400 with the exception message. Discover
# only raises SkillSourceError; catching the superset is harmless and keeps one tuple.
_SKILL_INSTALL_ERRORS = (SkillSourceError, SkillDownloadError, SkillInstallError)


class SkillStateRequest(BaseModel):
    # Enabled (True) / Disabled (False). Install-wide for a Bundled/Global skill via
    # /api/skills/{name}/state; per-profile for a Profile skill via /api/p/{pid}/skills/{name}/state.
    enabled: bool


class SkillSearchRequest(BaseModel):
    query: str
    limit: int = 10


class SkillInstallRequest(BaseModel):
    # A registry install (ADR 0017 t04) passes ``install_id`` (from a search hit).
    # A git install (t05) passes ``git_url`` + the chosen ``names``. The target is the
    # surface, not a field here: the Application route lands Global, the profile route
    # lands in that profile.
    install_id: str | None = None
    git_url: str | None = None
    names: list[str] | None = None


class SkillDiscoverRequest(BaseModel):
    # Scan a git URL (t05) for every SKILL.md without installing. Upload discovery uses
    # the multipart route instead (a file can't ride a JSON body).
    git_url: str | None = None


def _skill_store(d: GatewayDeps) -> SkillStateStore:
    """A fresh SkillStateStore over the install-wide file. mtime self-refresh
    means a live turn's next build sees any change — same shape as _folder_store."""
    return SkillStateStore(d.paths.root / "skills.json")


def _installwide_skills(d: GatewayDeps) -> list[dict]:
    """The install-wide projection: every Bundled + Global skill with its name,
    description, origin, and install-wide ``enabled`` state.

    Discovery uses the ROOT config (skills_dir is the Global layer there;
    ``with_profile`` repoints it per profile) plus the bundled first-party dir,
    so this is genuinely install-wide — not any one profile's view. Origin is
    read from each skill's on-disk location: under the bundled dir → bundled,
    otherwise → global.
    """
    store = _skill_store(d)
    bundled_root = bundled_skills_dir()
    runtime = build_skills_runtime(d.manager.config)
    rows = [
        {
            "name": s.name,
            "description": s.metadata.description,
            "origin": skill_origin(s.location, bundled_root),
            "enabled": not store.is_disabled(s.name),
        }
        for s in runtime.skills
    ]
    # Deletable (Global, user-installed) first, then read-only Bundled — each group
    # by name — so the rows a user can act on sit at the top.
    return sorted(rows, key=lambda r: (r["origin"] == ORIGIN_BUNDLED, r["name"]))


def _skills_snapshot(d: GatewayDeps) -> dict:
    return {"skills": _installwide_skills(d)}


def _profile_skill_rows(d: GatewayDeps, runtime) -> list[dict]:
    """The active-profile projection (ADR 0016 ticket 02): every skill VISIBLE to
    this profile — inherited Bundled/Global (Suppressible here) plus the profile's
    OWN skills (Enable/Disable here) — each carrying origin, install-wide
    ``enabled``, per-profile ``suppressed``, and the resolved ``available``.

    Built through the one resolution seam (``SkillStateStore.is_available``) so a
    row can never disagree with the catalog the profile's agent actually gets: a
    skill Disabled install-wide reads unavailable here too.
    """
    store = _skill_store(d)
    bundled_root = bundled_skills_dir()
    profile = runtime.pid
    rows: dict[str, dict] = {}
    # Inherited shared layers (Global + Bundled), discovered from the Root config.
    for s in build_skills_runtime(d.manager.config).skills:
        rows[s.name] = {
            "name": s.name,
            "description": s.metadata.description,
            "origin": skill_origin(s.location, bundled_root),
            "enabled": not store.is_disabled(s.name),
        }
    # The profile's OWN skills (under its skills_dir) shadow a shared skill of the
    # same name (catalog precedence Profile > Global > Bundled). A Profile skill has
    # no install-wide Disable — it lives in one profile, toggled per-profile only.
    prof_skills_dir = runtime.config.skills_dir.resolve()
    for s in build_skills_runtime(runtime.config).skills:
        loc = Path(s.location).resolve() if s.location else None
        if loc is None or not (prof_skills_dir == loc or prof_skills_dir in loc.parents):
            continue  # bundled (extra_paths) — already covered by the shared pass
        rows[s.name] = {
            "name": s.name,
            "description": s.metadata.description,
            "origin": ORIGIN_PROFILE,
            "enabled": True,
        }
    for r in rows.values():
        kind = DISABLE_OWN if r["origin"] == ORIGIN_PROFILE else SUPPRESS_SHARED
        r["suppressed"] = store.is_suppressed(r["name"], profile, kind=kind)
        r["available"] = store.is_available(r["name"], profile, origin=r["origin"])
    order = {ORIGIN_BUNDLED: 0, ORIGIN_GLOBAL: 1, ORIGIN_PROFILE: 2}
    return sorted(rows.values(), key=lambda r: (order.get(r["origin"], 9), r["name"]))


def _remove_skill_dir(runtime, name: str) -> None:
    """Remove skill ``name`` by its REAL on-disk directory. The lenient loader
    permits a skill whose frontmatter ``name`` differs from its directory
    (``weather-helper/`` with ``name: weather``); ``runtime.remove`` assumes
    dir == name and would 404 such a hand-placed skill, leaving it undeletable from
    the UI. Resolve the actual dir via the loader, then remove by its basename so
    the runtime's path-traversal guard still applies."""
    skill_dir = runtime.get_path(name)  # SkillError if the name isn't on disk
    runtime.remove(skill_dir.name)


async def _save_upload(upload: UploadFile, tmp_dir: Path) -> Path:
    """Stream an uploaded file into ``tmp_dir`` in bounded chunks and return its path
    (original name preserved so discover/install can tell a .zip from a SKILL.md).
    Caps the total read so a huge upload can't exhaust RAM before it ever reaches the
    unpacker; the archive's UNCOMPRESSED size is capped again at unpack."""
    name = os.path.basename(upload.filename or "upload") or "upload"
    dest = tmp_dir / name
    total = 0
    with dest.open("wb") as f:
        while chunk := await upload.read(1024 * 1024):
            total += len(chunk)
            if total > _MAX_UPLOAD_BYTES:
                raise SkillSourceError("upload is too large")
            f.write(chunk)
    return dest


async def _install_from_req(runtime, req: SkillInstallRequest, client: SkillsClient | None) -> dict:
    """Install into ``runtime``'s layer from a registry id (t04) or a git source
    (t05). Raises one of ``_SKILL_INSTALL_ERRORS``. Shared by both surfaces — only
    the target runtime and the reload differ."""
    if req.install_id:
        return {"installed": [await registry_install(runtime, req.install_id, client=client)]}
    if req.git_url:
        # git clone + copytree are blocking (up to a 120s clone timeout); off-load
        # them so a slow/hanging remote never freezes the whole gateway.
        installed = await asyncio.to_thread(
            install_from_source, runtime, req.names or [], git_url=req.git_url
        )
        return {"installed": installed}
    raise SkillSourceError("provide a registry install_id or a git_url")


async def _discover_git(git_url: str | None) -> dict:
    """Scan a git URL for every SKILL.md (no install). The clone is blocking (120s
    timeout) so it runs off the event loop. Shared by both surfaces' discover routes."""
    return {"skills": await asyncio.to_thread(discover_source, git_url=git_url)}


async def _install_upload_into(runtime, file: UploadFile, names: str) -> dict:
    """Install the selected (comma-separated) ``names`` from an uploaded source into
    ``runtime``. Shared by both surfaces' install-upload routes."""
    wanted = [n.strip() for n in (names or "").split(",") if n.strip()]
    with tempfile.TemporaryDirectory(prefix="skill-up-") as td:
        path = await _save_upload(file, Path(td))
        # Unpack + copytree are blocking → run off-loop.
        installed = await asyncio.to_thread(
            install_from_source,
            runtime,
            wanted,
            upload_path=path,
            filename=file.filename or "",
        )
        return {"installed": installed}


async def _discover_upload_file(file: UploadFile) -> dict:
    """Discover skills in an uploaded SKILL.md / zipped folder (no install)."""
    with tempfile.TemporaryDirectory(prefix="skill-up-") as td:
        path = await _save_upload(file, Path(td))
        skills = await asyncio.to_thread(  # blocking unpack + scan → off-loop
            discover_source, upload_path=path, filename=file.filename or ""
        )
        return {"skills": skills}


def build_router(d: GatewayDeps, *, skills_client: SkillsClient | None = None) -> APIRouter:
    """The install-wide Skills surface, in the order it had in app.py.

    ``skills_client`` is a create_app parameter rather than a GatewayDeps field:
    tests swap the skills.sh registry client per app, exactly as they do
    ``llm_probe`` and ``code_reader``.
    """
    r = APIRouter()

    @r.get("/api/skills", response_model=SkillListResponse)
    async def get_skills():
        """The install-wide skill projection: Bundled + Global skills with origin
        and their install-wide Enabled/Disabled state (drives Application → Skills)."""
        return _skills_snapshot(d)

    @r.post("/api/skills/{name}/state", response_model=SkillMutatedResponse)
    async def set_skill_state(name: str, req: SkillStateRequest):
        """Enable/Disable a Bundled or Global skill install-wide. Fans out a reload
        to every live runtime so the catalog changes everywhere at once — an
        in-flight turn finishes on the old catalog, the next turn sees the change.
        404 for a name that is not an install-wide skill."""
        known = {s["name"] for s in _installwide_skills(d)}
        if name not in known:
            return JSONResponse({"error": f"unknown skill: {name}"}, status_code=404)
        _skill_store(d).set_enabled(name, req.enabled)
        await reload_all(d.manager)  # install-wide change → every profile's agent rebuilds
        return {"ok": True, **_skills_snapshot(d)}

    @r.delete("/api/skills/{name}", response_model=SkillMutatedResponse)
    async def delete_skill(name: str):
        """Delete a **Global** skill from disk install-wide, then cascade-purge its
        state (install-wide Disable + every profile's Suppression) so a later same-named
        re-install resolves default-on everywhere — no ghost. Fans out a reload to all
        live runtimes. A **Bundled** skill is first-party/read-only → 409 (not deletable);
        an unknown name → 404. Mirrors DELETE /api/folders/{id}'s grant cascade."""
        config = d.manager.config
        store = _skill_store(d)
        runtime = build_skills_runtime(config)
        bundled_root = bundled_skills_dir()
        row = next((s for s in runtime.skills if s.name == name), None)
        if row is None:
            return JSONResponse({"error": f"unknown skill: {name}"}, status_code=404)
        if skill_origin(row.location, bundled_root) == ORIGIN_BUNDLED:
            return JSONResponse(
                {"error": f"{name} is a first-party skill and can't be deleted"},
                status_code=409,
            )
        try:
            _remove_skill_dir(runtime, name)
        except (SkillError, FileNotFoundError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        runtime.invalidate()
        store.purge(name)  # drop Disable + every shared Suppression of this name
        await reload_all(d.manager)
        return {"ok": True, **_skills_snapshot(d)}

    # ---- Installing skills from Settings (registry / git / upload — ADR 0017) ----
    # The target is the SURFACE: the /api/skills* routes below install into the Global
    # layer and fan out; the mirrored /api/p/{pid}/skills* routes install into the active
    # profile and reload only it. Both delegate to skills_install over the right runtime.

    @r.post("/api/skills/search", response_model=SkillSearchResultsResponse)
    async def search_skills(req: SkillSearchRequest):
        """Proxy a skills.sh registry search → ``{results:[{name, install_id,
        description, installs}]}``. Target-agnostic: both surfaces search through here,
        then install via their own (Global vs Profile) install route."""
        try:
            return {"results": await registry_search(req.query, req.limit, client=skills_client)}
        except Exception as exc:  # a registry/network failure shouldn't 500 the page
            return JSONResponse({"error": f"search failed: {exc}"}, status_code=502)

    @r.post("/api/skills/discover", response_model=SkillDiscoveredResponse)
    async def discover_skills(req: SkillDiscoverRequest):
        """Scan a git URL for every SKILL.md (no install) → ``{skills:[{name,
        description}]}`` for the checklist. 400 for an unreachable/invalid source."""
        try:
            return await _discover_git(req.git_url)
        except SkillSourceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @r.post("/api/skills/discover-upload", response_model=SkillDiscoveredResponse)
    async def discover_skills_upload(file: UploadFile = File(...)):
        """Discover skills in an uploaded SKILL.md / zipped folder (no install)."""
        try:
            return await _discover_upload_file(file)
        except SkillSourceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @r.post("/api/skills/install", response_model=SkillInstalledResponse)
    async def install_skill(req: SkillInstallRequest):
        """Install into the **Global** layer from a registry id or a git URL + names,
        then fan out a reload so every profile sees it next turn. A name collision in the
        target replaces the prior skill. 400 on a bad source (nothing half-installed)."""
        try:
            result = await _install_from_req(
                build_skills_runtime(d.manager.config), req, skills_client
            )
        except _SKILL_INSTALL_ERRORS as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await reload_all(d.manager)
        return {"ok": True, **result, **_skills_snapshot(d)}

    @r.post("/api/skills/install-upload", response_model=SkillInstalledResponse)
    async def install_skill_upload(file: UploadFile = File(...), names: str = Form(...)):
        """Install selected skills from an uploaded source into the **Global** layer.
        ``names`` is a comma-separated list (multipart can't carry a JSON array)."""
        try:
            result = await _install_upload_into(build_skills_runtime(d.manager.config), file, names)
        except _SKILL_INSTALL_ERRORS as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await reload_all(d.manager)
        return {"ok": True, **result, **_skills_snapshot(d)}

    return r


def build_profile_router(
    d: GatewayDeps, get_runtime, *, skills_client: SkillsClient | None = None
) -> APIRouter:
    """Skills scoped to the profile in the URL (Suppression of shared skills,
    own-skill state, and installs that land in this profile only — ADR 0016).

    A change here reloads ONLY this profile (``manager.reload(pid)``); the
    install-wide toggles above fan out to all.
    """
    r = APIRouter()

    @r.get("/skills", response_model=ProfileSkillListResponse)
    async def profile_skills(runtime: ProfileRuntime = Depends(get_runtime)):
        """This profile's resolved skill projection — inherited Bundled/Global skills
        (Suppressible here) and the profile's own skills (Enable/Disable here)."""
        return {"skills": _profile_skill_rows(d, runtime)}

    async def _suppress(name: str, runtime, suppressed: bool) -> dict:
        # Shared by the suppress/un-suppress routes: validate against the projection
        # (built once), flip the per-profile off-record, reload only this profile.
        if name not in {r["name"] for r in _profile_skill_rows(d, runtime)}:
            return JSONResponse({"error": f"unknown skill: {name}"}, status_code=404)
        # A Suppression of an inherited shared skill — tagged SHARED so a same-named
        # Global Delete's purge clears it (but never a Profile skill's own off-state).
        _skill_store(d).set_suppressed(name, runtime.pid, suppressed, kind=SUPPRESS_SHARED)
        await d.manager.reload(runtime.pid)
        return {"ok": True, "skills": _profile_skill_rows(d, runtime)}

    @r.post("/skills/{name}/suppress", response_model=ProfileSkillMutatedResponse)
    async def suppress_skill(name: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """Suppress an inherited (Bundled/Global) skill for THIS profile only — off
        here, untouched everywhere else. Reloads only this profile so its next turn
        drops the skill; other profiles never rebuild. 404 for a name not visible here."""
        return await _suppress(name, runtime, True)

    @r.delete("/skills/{name}/suppress", response_model=ProfileSkillMutatedResponse)
    async def unsuppress_skill(name: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """Clear this profile's Suppression of a shared skill — back to inherited "on".
        Reloads only this profile. 404 for a name not visible here."""
        return await _suppress(name, runtime, False)

    @r.post("/skills/{name}/state", response_model=ProfileSkillMutatedResponse)
    async def set_profile_skill_state(
        name: str, req: SkillStateRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Enable/Disable a skill this profile OWNS, scoped to the profile — its own
        Disable never leaves it (stored as the same per-profile off-record Suppression
        uses). Reloads only this profile. 404 unless ``name`` is a Profile skill here
        (a shared Bundled/Global skill is Suppressed, not Disabled, per profile)."""
        row = next((r for r in _profile_skill_rows(d, runtime) if r["name"] == name), None)
        if row is None or row["origin"] != ORIGIN_PROFILE:
            return JSONResponse({"error": f"not a profile skill: {name}"}, status_code=404)
        # A Disable of THIS profile's own skill — tagged OWN so a same-named Global
        # purge leaves it intact; only this copy's Delete clears it.
        _skill_store(d).set_suppressed(name, runtime.pid, not req.enabled, kind=DISABLE_OWN)
        await d.manager.reload(runtime.pid)
        return {"ok": True, "skills": _profile_skill_rows(d, runtime)}

    @r.delete("/skills/{name}", response_model=ProfileSkillMutatedResponse)
    async def delete_profile_skill(name: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """Delete one of THIS profile's own Profile skills from disk — removed for this
        profile only; other profiles never rebuild. Clears this profile's off-record for
        the name so a same-named re-install here is default-on. 404 for an unknown name;
        409 for a shared Bundled/Global skill (delete a Global skill from Application →
        Skills, which cascades; Bundled is never deletable)."""
        row = next((r for r in _profile_skill_rows(d, runtime) if r["name"] == name), None)
        if row is None:
            return JSONResponse({"error": f"unknown skill: {name}"}, status_code=404)
        if row["origin"] != ORIGIN_PROFILE:
            return JSONResponse(
                {"error": f"{name} isn't this profile's own skill — can't delete it here"},
                status_code=409,
            )
        prof_runtime = build_skills_runtime(runtime.config)
        try:
            _remove_skill_dir(prof_runtime, name)
        except (SkillError, FileNotFoundError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        prof_runtime.invalidate()
        # Clear only THIS profile's OWN off-record for the name (kind=OWN): a Profile
        # skill lives in one profile. Leaving a same-named SHARED Suppression standing
        # keeps a shadowed Global skill suppressed after the copy is gone — and this
        # never touches the Global skill's install-wide/other-profile state.
        _skill_store(d).set_suppressed(name, runtime.pid, False, kind=DISABLE_OWN)
        await d.manager.reload(runtime.pid)
        return {"ok": True, "skills": _profile_skill_rows(d, runtime)}

    # ---- Install into THIS profile (registry / git / upload — ADR 0017) ----
    # Same delegation as the Global /api/skills* install routes, but the target is the
    # profile's own skills dir (build_skills_runtime over runtime.config) and only this
    # profile reloads. Registry search is target-agnostic → done via GLOBAL /api/skills/search.

    @r.post("/skills/install", response_model=ProfileSkillInstalledResponse)
    async def install_profile_skill(
        req: SkillInstallRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Install into THIS profile from a registry id or a git URL + names; reloads
        only this profile. Collision in the profile's dir replaces the prior skill. Same
        body as the Global route (``_install_from_req``) — only the target + reload differ."""
        try:
            result = await _install_from_req(
                build_skills_runtime(runtime.config), req, skills_client
            )
        except _SKILL_INSTALL_ERRORS as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await d.manager.reload(runtime.pid)
        return {"ok": True, **result, "skills": _profile_skill_rows(d, runtime)}

    @r.post("/skills/discover", response_model=SkillDiscoveredResponse)
    async def discover_profile_skills(
        req: SkillDiscoverRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Scan a git URL for every SKILL.md (no install) for the profile's checklist."""
        try:
            return await _discover_git(req.git_url)
        except SkillSourceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @r.post("/skills/discover-upload", response_model=SkillDiscoveredResponse)
    async def discover_profile_skills_upload(
        file: UploadFile = File(...), runtime: ProfileRuntime = Depends(get_runtime)
    ):
        try:
            return await _discover_upload_file(file)
        except SkillSourceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @r.post("/skills/install-upload", response_model=ProfileSkillInstalledResponse)
    async def install_profile_skill_upload(
        file: UploadFile = File(...),
        names: str = Form(...),
        runtime: ProfileRuntime = Depends(get_runtime),
    ):
        """Install selected skills from an uploaded source into THIS profile."""
        try:
            result = await _install_upload_into(build_skills_runtime(runtime.config), file, names)
        except _SKILL_INSTALL_ERRORS as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await d.manager.reload(runtime.pid)
        return {"ok": True, **result, "skills": _profile_skill_rows(d, runtime)}

    return r

"""Connections: one configured instance of a messaging platform, plus the three
tables hung off it — which profiles it can reach (exposure), who may speak to it
(paired accounts), and where each of its group conversations lands.

A Connection is install-level and never owned by a profile (ADR 0022), so a
platform may connect as many times as you like and every route here is keyed by a
connection id rather than by the platform.

Pairs with gateway/schemas/connection.py (the response models) and
web/src/schemas/connection.ts (their zod twins) — same file name in all three trees.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from assistant import profiles as profiles_mod
from assistant.connections import Connection, surface_key, surfaces
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import (
    ConnectionExposureResponse,
    ConnectionGroupsResponse,
    ConnectionListResponse,
    ConnectionOut,
    ConnectionPairingResponse,
    Ok,
    PairingCodeIssuedResponse,
)
from assistant.pairing import PairedAccount
from assistant.peers import Peer


class ConnectionCreateRequest(BaseModel):
    platform: str
    name: str = ""  # blank takes the platform's next free default name
    tokens: dict[str, str] = Field(default_factory=dict)  # {ENV_NAME: value}


class ConnectionRenameRequest(BaseModel):
    name: str


class ConnectionTokenRequest(BaseModel):
    tokens: dict[str, str] = Field(default_factory=dict)  # every env the platform needs


class ConnectionDefaultRequest(BaseModel):
    profile: str | None = None  # pid conversations land in by default, or null for none


class ConnectionExposureRequest(BaseModel):
    profile: str
    surface: str  # one of the Connection's own surface ids
    exposed: bool


class PairAccountRequest(BaseModel):
    value: str  # a numeric account id (authoritative) or a handle (an invitation)


class GroupProfileRequest(BaseModel):
    profile: str  # the pid to re-point a group Peer at; never null — a group is pinned


def build_router(d: GatewayDeps) -> APIRouter:
    """The Connection routes and the three tables hung off them, in the order they
    had in app.py."""
    r = APIRouter()

    def _connection_entry(connection: Connection) -> dict:
        """One Connection as the API shows it: its identity, token(s) as a set flag and
        hint, default profile, whether the adapter is live, why not, and its roster size."""
        cid = connection.id
        return {
            "id": cid,
            "platform": connection.platform,
            "name": connection.name,
            "tokens": d.connection_store.token_status(cid),
            "default_profile": d.registry.connection_defaults().get(cid),
            "active": cid in d.manager.channels,
            "error": d.manager.channel_errors.get(cid),
            # A live Connection with nobody paired answers nobody (ADR 0021) — the count
            # is what lets Settings say so rather than leave it looking healthy.
            "paired_accounts": len(d.pairing_store.list_accounts(cid)),
        }

    def _token_error(platform: str, tokens: dict[str, str]):
        """Refuse a Connection that could never start: an unknown platform, a token env
        that is not that platform's, or one of its tokens missing."""
        if platform not in profiles_mod.CHANNEL_PLATFORMS:
            return JSONResponse({"error": f"unknown channel platform: {platform}"}, status_code=400)
        envs = profiles_mod.CHANNEL_TOKEN_ENVS[platform]
        unknown = set(tokens) - set(envs)
        if unknown:
            return JSONResponse(
                {"error": f"invalid token env(s) for {platform}: {', '.join(sorted(unknown))}"},
                status_code=400,
            )
        missing = [e for e in envs if not (tokens.get(e) or "").strip()]
        if missing:
            return JSONResponse(
                {"error": f"missing token(s) for {platform}: {', '.join(missing)}"}, status_code=400
            )
        return None

    @r.get("/api/connections", response_model=ConnectionListResponse)
    async def list_connections():
        """Every configured instance of a platform, in creation order. An install that
        already had bot tokens is migrated to one Connection per platform on this read.
        A Connection's token(s) appear only as a set flag and a last-4 hint."""
        return {
            "connections": [_connection_entry(c) for c in d.connection_store.list_connections()]
        }

    @r.post("/api/connections", response_model=ConnectionOut)
    async def create_connection(req: ConnectionCreateRequest):
        """Register a Connection on ``platform`` with its token(s) and start it at once;
        one that will not start still records its reason. Bad tokens or platform → 400."""
        if (bad := _token_error(req.platform, req.tokens)) is not None:
            return bad
        connection = d.connection_store.create_connection(req.platform, req.name, tokens=req.tokens)
        await d.manager.start_channel(connection.id)
        return _connection_entry(connection)

    @r.post("/api/connections/{cid}", response_model=ConnectionOut)
    async def rename_connection(cid: str, req: ConnectionRenameRequest):
        """Change a Connection's display name; its id, tokens and everything keyed by it
        are untouched. Unknown Connection → 404, a blank name → 400."""
        if d.connection_store.get_connection(cid) is None:
            return JSONResponse({"error": f"unknown connection: {cid}"}, status_code=404)
        try:
            connection = d.connection_store.rename_connection(cid, req.name)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return _connection_entry(connection)

    @r.post("/api/connections/{cid}/token", response_model=ConnectionOut)
    async def replace_connection_token(cid: str, req: ConnectionTokenRequest):
        """Replace a Connection's token(s) and restart it on them, keeping its identity.
        A replacement that will not start rolls back → 400; unknown → 404."""
        connection = d.connection_store.get_connection(cid)
        if connection is None:
            return JSONResponse({"error": f"unknown connection: {cid}"}, status_code=404)
        if (bad := _token_error(connection.platform, req.tokens)) is not None:
            return bad
        envs = profiles_mod.CHANNEL_TOKEN_ENVS[connection.platform]
        prior = d.connection_store.tokens_for(cid)
        d.connection_store.set_tokens(cid, req.tokens)
        active, reason = await d.manager.restart_channel(cid)
        if not active:
            d.connection_store.set_tokens(cid, {e: prior.get(e, "") for e in envs})
            await d.manager.restart_channel(cid)
            return JSONResponse({"error": reason}, status_code=400)
        return _connection_entry(connection)

    @r.delete("/api/connections/{cid}", response_model=Ok)
    async def delete_connection(cid: str):
        """Stop a Connection and forget it with its token(s), Peers, paired accounts,
        pairing code, default-Profile entry and exposure records. Unknown → 404."""
        if d.connection_store.get_connection(cid) is None:
            return JSONResponse({"error": f"unknown connection: {cid}"}, status_code=404)
        await d.manager.stop_channel(cid)
        d.manager.channel_errors.pop(cid, None)
        d.connection_store.delete_connection(cid)
        return {"ok": True}

    @r.post("/api/connections/{cid}/default", response_model=ConnectionOut)
    async def set_connection_default(cid: str, req: ConnectionDefaultRequest):
        """Set the profile this Connection's conversations land in by default (or clear
        it with profile:null). Takes effect on the next message — the adapter itself keeps
        running either way. Unknown Connection → 404; unknown/archived pid → 400."""
        connection = d.connection_store.get_connection(cid)
        if connection is None:
            return JSONResponse({"error": f"unknown connection: {cid}"}, status_code=404)
        try:
            d.connection_store.set_default_profile(cid, req.profile)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return _connection_entry(connection)

    def _exposure_view(connection: Connection) -> dict:
        """Which profiles this Connection can reach, per surface, plus the one its
        conversations land in by default — one table, since the two are one decision."""
        return {
            "surfaces": [
                {"kind": kind, "id": surface} for kind, surface in surfaces(connection).items()
            ],
            "exposure": d.connection_store.exposure(connection.id),
            "default_profile": d.registry.connection_defaults().get(connection.id),
        }

    @r.get("/api/connections/{cid}/exposure", response_model=ConnectionExposureResponse)
    async def list_connection_exposure(cid: str):
        """This Connection's surfaces and every profile's reachability on each.
        Default-allow, so a profile nobody has withdrawn reads true everywhere."""
        connection = d.connection_store.get_connection(cid)
        if connection is None:
            return JSONResponse({"error": f"unknown connection: {cid}"}, status_code=404)
        return _exposure_view(connection)

    @r.post("/api/connections/{cid}/exposure", response_model=ConnectionExposureResponse)
    async def set_connection_exposure(cid: str, req: ConnectionExposureRequest):
        """Expose or withdraw one profile on one surface of this Connection; withdrawing
        the default's last surface clears it. Unknown → 404, bad profile/surface → 400."""
        connection = d.connection_store.get_connection(cid)
        if connection is None:
            return JSONResponse({"error": f"unknown connection: {cid}"}, status_code=404)
        try:
            d.connection_store.set_exposure(cid, req.profile, req.surface, req.exposed)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return _exposure_view(connection)

    # ---- Paired accounts (per Connection; who may speak to it at all — ADR 0021) ----

    def _account_view(account: PairedAccount) -> dict:
        """One paired account. ``pending`` is what the UI shows differently: an
        invitation to a handle, not yet an identity."""
        return {
            "key": account.key,
            "account_id": account.account_id,
            "handle": account.handle,
            "pending": account.pending,
        }

    def _pairing_view(cid: str | None) -> dict:
        """One Connection's roster and live code. No Connection yet reads as an empty
        roster — nobody is paired, which is what there is to say."""
        code = d.pairing_store.live_code(cid) if cid else None
        return {
            "accounts": [_account_view(a) for a in d.pairing_store.list_accounts(cid)]
            if cid
            else [],
            "code": None if code is None else {"code": code.code, "expires_at": code.expires_at},
        }

    @r.get("/api/connections/{cid}/pairing", response_model=ConnectionPairingResponse)
    async def list_connection_pairing(cid: str):
        """Who may reach this one Connection, and its live one-time code (or null).
        A grant here is no grant on another Connection of the same platform."""
        if d.connection_store.get_connection(cid) is None:
            return JSONResponse({"error": f"unknown connection: {cid}"}, status_code=404)
        return _pairing_view(cid)

    @r.post("/api/connections/{cid}/pairing", response_model=ConnectionPairingResponse)
    async def add_connection_pairing(cid: str, req: PairAccountRequest):
        """Allow an account on this Connection by numeric id or by handle."""
        connection = d.connection_store.get_connection(cid)
        if connection is None:
            return JSONResponse({"error": f"unknown connection: {cid}"}, status_code=404)
        try:
            d.pairing_store.add_account(cid, req.value, connection.platform)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return _pairing_view(cid)

    @r.delete("/api/connections/{cid}/pairing/{key:path}", response_model=ConnectionPairingResponse)
    async def revoke_connection_pairing(cid: str, key: str):
        """Withdraw one entry from this Connection alone. Nothing to withdraw → 404."""
        if d.connection_store.get_connection(cid) is None:
            return JSONResponse({"error": f"unknown connection: {cid}"}, status_code=404)
        if not d.pairing_store.revoke(cid, key):
            return JSONResponse({"error": f"not paired: {key}"}, status_code=404)
        return _pairing_view(cid)

    @r.post("/api/connections/{cid}/pairing/code", response_model=PairingCodeIssuedResponse)
    async def issue_connection_pairing_code(cid: str):
        """Mint this Connection's one live code, replacing its earlier one and no
        other's. The code works only on the Connection it was minted for."""
        if d.connection_store.get_connection(cid) is None:
            return JSONResponse({"error": f"unknown connection: {cid}"}, status_code=404)
        d.pairing_store.issue_code(cid)
        return _pairing_view(cid)["code"]

    # ---- Group Peers (a group's profile is pinned, and re-pointed only here) ----

    def _connection_groups(cid: str) -> list[Peer]:
        """Every group Peer that arrived on this one Connection."""
        return [
            p for p in d.peer_store.list_peers() if p.connection == cid and p.surface == "group"
        ]

    def _group_surface_profiles(cid: str, platform: str) -> list[profiles_mod.ProfileMeta]:
        """The unarchived profiles exposed to this Connection's group surface — what a
        group can be pinned to, whether or not its runtime is up right now."""
        surface = surface_key(cid, platform, "group")
        withdrawn = d.registry.withdrawn_from(surface)
        return [m for m in d.registry.list_profiles() if m.id not in withdrawn]

    def _connection_group_view(connection: Connection) -> dict:
        """This Connection's group Peers with the profile each is pinned to, plus the
        profiles exposed to this Connection's group surface."""
        return {
            "groups": [
                {"chat_id": p.chat_id, "profile": p.profile}
                for p in _connection_groups(connection.id)
            ],
            "profiles": [
                {"id": m.id, "name": m.name}
                for m in _group_surface_profiles(connection.id, connection.platform)
            ],
        }

    @r.get("/api/connections/{cid}/groups", response_model=ConnectionGroupsResponse)
    async def list_connection_groups(cid: str):
        """This Connection's group Peers and what each is pinned to. Unknown → 404."""
        connection = d.connection_store.get_connection(cid)
        if connection is None:
            return JSONResponse({"error": f"unknown connection: {cid}"}, status_code=404)
        return _connection_group_view(connection)

    @r.post(
        "/api/connections/{cid}/groups/{chat_id}/profile",
        response_model=ConnectionGroupsResponse,
    )
    async def set_connection_group_profile(cid: str, chat_id: str, req: GroupProfileRequest):
        """Re-point one of this Connection's groups at a profile exposed to its group
        surface. Unknown Connection or group → 404; an unreachable profile → 400."""
        connection = d.connection_store.get_connection(cid)
        if connection is None:
            return JSONResponse({"error": f"unknown connection: {cid}"}, status_code=404)
        surface = surface_key(cid, connection.platform, "group")
        if req.profile not in {m.id for m in _group_surface_profiles(cid, connection.platform)}:
            return JSONResponse(
                {"error": f"profile not reachable from {surface}: {req.profile}"}, status_code=400
            )
        if not any(p.chat_id == chat_id for p in _connection_groups(cid)):
            return JSONResponse({"error": f"no group peer: {chat_id}"}, status_code=404)
        d.peer_store.select_profile(
            cid, chat_id, req.profile, platform=connection.platform, surface="group"
        )
        return _connection_group_view(connection)

    return r

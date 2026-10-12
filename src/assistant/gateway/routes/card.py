"""Layered Cards Settings and explicit Chat draft Save."""

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from assistant.a2ui import CARD_VOCABULARY, card_layers
from assistant.card_drafts import DraftError
from assistant.cards import Card, CardError, CardStateStore, scan_cards
from assistant.config import Config
from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.routes.common import purge_card_state, shared_card_file
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas.card import (
    CardListResponse,
    CardMutatedResponse,
    CardSaveResponse,
    ProfileCardListResponse,
    ProfileCardMutatedResponse,
)
from assistant.state_store import DISABLE_OWN, ORIGIN_BUNDLED, ORIGIN_PROFILE, SUPPRESS_SHARED


class CardSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str
    expected_version: int
    surface_id: str
    name: str
    filename: str
    request_id: str
    replace: bool = False
    conflict_token: str = ""


class CardStateRequest(BaseModel):
    enabled: bool


def _file_path(path: Path, config: Config, origin: str) -> str:
    if origin == ORIGIN_PROFILE:
        return str(path.relative_to(config.workspace_dir))
    return str(path.absolute())


def _snapshot(d: GatewayDeps, runtime: ProfileRuntime | None = None) -> dict:
    config = runtime.require_config() if runtime is not None else d.manager.config
    layers = card_layers(config) if runtime is not None else card_layers(config)[:2]
    cards: dict[str, Card] = {}
    files: list[dict[str, str]] = []
    problems: list[dict[str, str]] = []
    for origin, directory in layers:
        scan = scan_cards(directory, CARD_VOCABULARY, origin)
        cards.update(scan.cards)
        files.extend(
            {"path": _file_path(path, config, origin), "name": path.name, "origin": origin}
            for path in scan.files
        )
        problems.extend(
            {"path": _file_path(p.path, config, origin), "origin": origin, "error": p.error}
            for p in scan.problems
        )
    state = CardStateStore(d.paths.root)
    rows = []
    for card in cards.values():
        if card.path is None:
            continue
        if (
            card.origin != ORIGIN_PROFILE
            and shared_card_file(config, str(card.path.absolute()))[0] is None
        ):
            continue
        row = {
            "name": card.name,
            "description": card.description,
            "topic": card.topic,
            "origin": card.origin,
            "path": _file_path(card.path, config, card.origin),
            "enabled": card.origin == ORIGIN_PROFILE or not state.is_disabled(card.name),
        }
        if runtime is not None:
            kind = DISABLE_OWN if card.origin == ORIGIN_PROFILE else SUPPRESS_SHARED
            row["suppressed"] = state.is_suppressed(card.name, runtime.pid, kind=kind)
            row["available"] = state.is_available(card.name, runtime.pid, origin=card.origin)
        rows.append(row)
    return {
        "cards": sorted(rows, key=lambda row: row["name"]),
        "problems": problems,
        "files": files,
    }


def build_router(d: GatewayDeps) -> APIRouter:
    r = APIRouter()

    @r.get("/api/cards", response_model=CardListResponse)
    async def list_cards():
        return _snapshot(d)

    @r.post("/api/cards/state", response_model=CardMutatedResponse)
    async def set_state(name: str, req: CardStateRequest):
        if name not in {c["name"] for c in _snapshot(d)["cards"]}:
            return JSONResponse({"error": f"unknown card: {name}"}, status_code=404)
        CardStateStore(d.paths.root).set_enabled(name, req.enabled)
        return {"ok": True, **_snapshot(d)}

    @r.delete("/api/cards", response_model=CardMutatedResponse)
    async def delete_card(name: str):
        row = next((c for c in _snapshot(d)["cards"] if c["name"] == name), None)
        if row is None:
            return JSONResponse({"error": f"unknown card: {name}"}, status_code=404)
        if row["origin"] == ORIGIN_BUNDLED:
            return JSONResponse({"error": "bundled cards cannot be deleted"}, status_code=409)
        try:
            Path(row["path"]).unlink()
        except OSError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        purge_card_state(d.manager.config, name, row["origin"])
        return {"ok": True, **_snapshot(d)}

    return r


def build_profile_router(d: GatewayDeps, get_runtime) -> APIRouter:
    r = APIRouter()

    @r.post("/chats/{chat_id}/cards/save", response_model=CardSaveResponse)
    async def save_card(
        chat_id: str, request: CardSaveRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        try:
            return await runtime.require_gateway().save_card_definition(
                chat_id, **request.model_dump()
            )
        except DraftError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except CardError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except OSError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @r.get("/cards", response_model=ProfileCardListResponse)
    async def list_cards(runtime: ProfileRuntime = Depends(get_runtime)):
        return _snapshot(d, runtime)

    @r.post("/cards/state", response_model=ProfileCardMutatedResponse)
    async def set_state(
        name: str, req: CardStateRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        row = next((c for c in _snapshot(d, runtime)["cards"] if c["name"] == name), None)
        if row is None or row["origin"] != ORIGIN_PROFILE:
            return JSONResponse({"error": f"not a profile card: {name}"}, status_code=404)
        CardStateStore(d.paths.root).set_suppressed(
            name, runtime.pid, not req.enabled, kind=DISABLE_OWN
        )
        return {"ok": True, **_snapshot(d, runtime)}

    def suppress(name: str, runtime: ProfileRuntime, suppressed: bool):
        row = next((c for c in _snapshot(d, runtime)["cards"] if c["name"] == name), None)
        if row is None:
            return JSONResponse({"error": f"unknown card: {name}"}, status_code=404)
        if row["origin"] == ORIGIN_PROFILE:
            return JSONResponse({"error": f"{name} is a profile-owned card"}, status_code=409)
        CardStateStore(d.paths.root).set_suppressed(
            name, runtime.pid, suppressed, kind=SUPPRESS_SHARED
        )
        return {"ok": True, **_snapshot(d, runtime)}

    @r.post("/cards/suppress", response_model=ProfileCardMutatedResponse)
    async def suppress_card(name: str, runtime: ProfileRuntime = Depends(get_runtime)):
        return suppress(name, runtime, True)

    @r.delete("/cards/suppress", response_model=ProfileCardMutatedResponse)
    async def unsuppress_card(name: str, runtime: ProfileRuntime = Depends(get_runtime)):
        return suppress(name, runtime, False)

    @r.delete("/cards", response_model=ProfileCardMutatedResponse)
    async def delete_card(name: str, runtime: ProfileRuntime = Depends(get_runtime)):
        row = next((c for c in _snapshot(d, runtime)["cards"] if c["name"] == name), None)
        if row is None:
            return JSONResponse({"error": f"unknown card: {name}"}, status_code=404)
        if row["origin"] != ORIGIN_PROFILE:
            return JSONResponse(
                {"error": "only profile-owned cards can be deleted here"}, status_code=409
            )
        try:
            (runtime.require_config().workspace_dir / row["path"]).unlink()
        except OSError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        purge_card_state(runtime.require_config(), name, row["origin"])
        return {"ok": True, **_snapshot(d, runtime)}

    return r

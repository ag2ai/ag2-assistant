"""Secrets — named reusable API keys, install-wide (CONTEXT.md "Secrets", ADR 0005).

A Secret's raw value never leaves the process: every body here carries the safe
view (`assistant/secrets.py` `_secret_view`), which holds a last-4 hint instead.

Pairs with gateway/schemas/secret.py (the response models) and
web/src/schemas/secret.ts (their zod twins).
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from assistant.gateway.routes.common import reload_all
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import Ok, SecretListResponse, SecretSavedResponse
from assistant.secrets import DuplicateValue


class KeyRequest(BaseModel):
    provider: str
    value: str = ""  # empty clears the key


class SecretCreateRequest(BaseModel):
    """Create body for a Secret (named reusable API key — CONTEXT.md "Secrets").
    ``value`` is write-only: no endpoint ever returns it (views carry a last-4
    hint). ``default`` requires a provider tag."""

    name: str
    value: str
    provider: str = ""
    default: bool = False


class SecretUpdateRequest(BaseModel):
    """Partial update for a Secret — None leaves a field unchanged. Rotating
    ``value`` re-keys every model referencing this Secret."""

    name: str | None = None
    value: str | None = None
    provider: str | None = None
    default: bool | None = None


def build_router(d: GatewayDeps) -> APIRouter:
    """The install-level secret routes, in the order they had in app.py."""
    r = APIRouter()

    @r.post("/api/secrets/key", response_model=Ok)
    async def set_secrets_key(req: KeyRequest):
        """Save/clear a provider API key (global secrets). Reloads ALL runtimes so
        every profile's agent picks up the change on its next turn."""
        if not d.secret_store.set_key(req.provider, req.value):
            return Response(status_code=400)
        await reload_all(d.manager)
        return {"ok": True}

    # ---- Secrets: named reusable API keys (CONTEXT.md "Secrets", ADR 0005).
    # Registered AFTER /api/secrets/key so the literal "key" segment keeps routing
    # to the provider-key handler, not /{sid}. ----

    def _secret_views() -> list[dict]:
        """Safe views + the names of the model configs referencing each Secret
        (drives the "used by N models" delete confirm)."""
        llm = d.llm_store.list_configs()
        live = d.live_store.list_configs()
        out = []
        for s in d.secret_store.list_secrets():
            used = [c.get("name", "") for c in llm if c.get("secret_id") == s["id"]]
            used += [c.get("name", "") for c in live if c.get("secret_id") == s["id"]]
            out.append({**s, "used_by": used})
        return out

    @r.get("/api/secrets", response_model=SecretListResponse)
    async def list_secrets_api():
        """Every Secret as a safe view — name/provider/default/hint/used_by, never
        the raw value."""
        return {"secrets": _secret_views()}

    @r.post(
        "/api/secrets",
        response_model=SecretSavedResponse,
        response_model_exclude_unset=True,
    )
    async def create_secret_api(req: SecretCreateRequest):
        """Create a Secret. 409 + the existing Secret's view when the value is
        already stored (unique by value — the model form snaps to ``existing``).
        Reloads all runtimes (a new Default changes env-derived keys)."""
        try:
            view = d.secret_store.create_secret(
                req.name, req.value, provider=req.provider, default=req.default
            )
        except DuplicateValue as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc), "existing": exc.existing}, status_code=409
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)
        await reload_all(d.manager)
        return {"ok": True, "secret": view}

    @r.post(
        "/api/secrets/{sid}",
        response_model=SecretSavedResponse,
        response_model_exclude_unset=True,
    )
    async def update_secret_api(sid: str, req: SecretUpdateRequest):
        """Partial update (rename / rotate / retag / set-default). 404 unknown, 409
        duplicate value, 400 bad input. Rotating re-keys every referencing model."""
        try:
            view = d.secret_store.update_secret(
                sid, name=req.name, value=req.value, provider=req.provider, default=req.default
            )
        except KeyError:
            return JSONResponse({"ok": False, "error": f"unknown secret: {sid}"}, status_code=404)
        except DuplicateValue as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc), "existing": exc.existing}, status_code=409
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)
        await reload_all(d.manager)
        return {"ok": True, "secret": view}

    @r.delete("/api/secrets/{sid}", response_model=Ok)
    async def delete_secret_api(sid: str):
        """Delete a Secret (404 unknown). Always allowed — referencing configs
        degrade down the resolution order; deleting a Default pops its env var."""
        if not d.secret_store.delete_secret(sid):
            return JSONResponse({"ok": False, "error": f"unknown secret: {sid}"}, status_code=404)
        await reload_all(d.manager)
        return {"ok": True}

    return r

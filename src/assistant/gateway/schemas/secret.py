"""Secrets — named reusable API keys. Mirrors web/src/schemas/secret.ts.

The raw value never appears here by construction: every field below comes from
``assistant/secrets.py`` ``_secret_view``, which substitutes a last-4 ``hint``.
"""

from typing import Literal

from pydantic import BaseModel


class SecretOut(BaseModel):
    """One Secret's safe view.

    ``used_by`` is filled in only by ``GET /api/secrets``, which cross-references
    the LLM and live config stores to drive the "used by N models" delete
    confirm. The save routes answer with the store's bare view and say nothing
    about references, so the field carries a default and those routes are
    declared ``response_model_exclude_unset=True`` — shipping ``[]`` there would
    claim "nothing uses this", which a rename or a rotate has not established.
    """

    id: str
    name: str
    provider: str
    default: bool
    hint: str
    used_by: list[str] = []


class SecretListResponse(BaseModel):
    """GET /api/secrets."""

    secrets: list[SecretOut]


class SecretSavedResponse(BaseModel):
    """POST /api/secrets and POST /api/secrets/{sid}.

    ``ok`` is a Literal because the failure shapes (409 duplicate, 404 unknown,
    400 bad input) are non-2xx bodies built by hand, not this model.
    """

    ok: Literal[True]
    secret: SecretOut

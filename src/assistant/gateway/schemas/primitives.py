"""Bodies that are not specific to any one domain."""

from typing import Any

from pydantic import BaseModel


class Ok(BaseModel):
    """The bare acknowledgement a mutation route answers with."""

    ok: bool


class ErrorBody(BaseModel):
    """Every error body in app.py has this shape: 105 of them return {"error": str}."""

    error: str


class SecretRefOut(BaseModel):
    """The trimmed Secret view embedded in an LLM or live config.

    Deliberately narrower than the Secret's own view: a config row names the key
    it points at, it does not restate the Secret's provider tag or Default flag.
    """

    id: str
    name: str
    hint: str


class SharedKeyOut(BaseModel):
    """The provider-wide env key summary both config views carry: which variable
    holds the fallback key, whether it is set, and its last-4 hint."""

    env: str
    set: bool
    hint: str


# The shape FastAPI's ``responses=`` takes: status code -> OpenAPI response object.
ResponseSpecs = dict[int | str, dict[str, Any]]

# Attached once to the app and once to the profile router; FastAPI propagates a
# router-level ``responses`` to every route beneath it, so these six codes are
# documented for all 130 JSON routes without per-route repetition.
#
# Every entry spells its ``description`` out. Left off, FastAPI falls back to the
# interpreter's own reason phrase (``http.HTTPStatus``) — and that table CHANGES
# between versions: 3.13 renamed 422 to "Unprocessable Content" and 413 to
# "Content Too Large" after RFC 9110. The artifact would then depend on whichever
# Python generated it, and CI would read a perfectly current file as stale. The
# phrases below follow RFC 9110 and belong to this repo, not to the stdlib.
ERROR_RESPONSES: ResponseSpecs = {
    400: {"model": ErrorBody, "description": "Bad Request"},
    404: {"model": ErrorBody, "description": "Not Found"},
    409: {"model": ErrorBody, "description": "Conflict"},
    410: {"model": ErrorBody, "description": "Gone"},
    422: {"model": ErrorBody, "description": "Unprocessable Content"},
    502: {"model": ErrorBody, "description": "Bad Gateway"},
}

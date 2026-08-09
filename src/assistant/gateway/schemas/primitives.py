"""Bodies that are not specific to any one domain."""

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


# Attached once to the app and once to the profile router; FastAPI propagates a
# router-level ``responses`` to every route beneath it, so these six codes are
# documented for all 130 JSON routes without per-route repetition.
ERROR_RESPONSES: dict[int, dict[str, type[ErrorBody]]] = {
    400: {"model": ErrorBody},
    404: {"model": ErrorBody},
    409: {"model": ErrorBody},
    410: {"model": ErrorBody},
    422: {"model": ErrorBody},
    502: {"model": ErrorBody},
}

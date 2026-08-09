"""The one base class every response body in this package inherits."""

from pydantic import BaseModel, ConfigDict


class ResponseModel(BaseModel):
    """Base for every response body the gateway declares.

    ``extra="allow"`` is deliberate. FastAPI uses a response model as a filter:
    with the default ``extra="ignore"`` a key the model forgot to declare is
    dropped from the wire, and the front end sees a field that silently stopped
    arriving. Allowing extras inverts that — an incomplete model merely leaves
    the key out of the OpenAPI schema, it cannot break the UI.

    The remaining failure mode is a DECLARED field that does not arrive: that
    raises ResponseValidationError (500), which tests catch. Hence every field
    the gateway sends only sometimes must carry a default.

    A default alone is not enough, though: FastAPI would then SEND that field as
    ``null``, and the paired zod schema declares such fields ``.optional()``,
    which rejects null. So every route carrying one of these models is declared
    ``response_model_exclude_unset=True``. A field the handler left out then stays
    out of the response, while a field it set to None explicitly (zod's
    ``.nullable()``) still ships — the wire stays byte-identical to what the
    handler returned. Attach the flag to every route, unconditionally: a model
    that gains a defaulted field later must not silently change the wire.
    """

    model_config = ConfigDict(extra="allow")

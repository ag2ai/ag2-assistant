"""Source refresh acknowledgements carry typed stream events."""

from typing import Any, Literal

from pydantic import BaseModel


class SourceEvent(BaseModel):
    type: str
    data: dict[str, Any]


class CardSourceResponse(BaseModel):
    status: Literal["updated", "error", "approval_required", "discarded", "configured"]
    events: list[SourceEvent]

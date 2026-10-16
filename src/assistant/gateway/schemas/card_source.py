"""Source refresh acknowledgements carry typed stream events."""

from typing import Any, Literal

from pydantic import BaseModel


class SourceData(BaseModel):
    created_at: float = 0
    surface_id: str
    source_id: str
    path: str
    status: Literal["updated", "error", "approval_required", "discarded", "configured"]
    error: str
    data: dict[str, Any]
    code_version: str
    approval_required: bool


class SourceEvent(BaseModel):
    type: str
    data: SourceData


class CardSourceResponse(BaseModel):
    status: Literal["updated", "error", "approval_required", "discarded", "configured"]
    events: list[SourceEvent]

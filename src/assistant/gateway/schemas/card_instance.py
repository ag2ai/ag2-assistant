"""Portable rendered message envelopes and explicit Save results."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class CardInstanceMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surface_id: str
    version: Literal["v1.0"]
    catalog_id: str
    component: dict[str, Any]
    data: dict[str, Any]
    title: str
    intent: str


class CardInstanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["card-instance"]
    format_version: Literal[1]
    instance_id: str
    saved_at: str
    save_request_id: str
    request_hash: str
    message: CardInstanceMessage


class CardInstanceSaveResponse(BaseModel):
    status: Literal["saved"]
    path: str
    instance_id: str
    saved_at: str
    history_recorded: bool
    message: str

"""Portable rendered message envelopes and explicit Save results."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator


class CardInstanceMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surface_id: str
    version: Literal["1.0"]
    catalog_id: str
    component: dict[str, Any]
    data: dict[str, Any]
    title: str
    intent: str

    @field_validator("version", mode="before")
    @classmethod
    def _read_legacy_version(cls, value: Any) -> Any:
        """Files and surfaces saved before ag2 1.3.1 carry the v1.0 wire version with its "v"."""
        return "1.0" if value == "v1.0" else value


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

"""File-backed Screen listings and their expanded A2UI surfaces."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from assistant.gateway.schemas.card_instance import CardInstanceMessage


class ScreenDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["screen"]
    format_version: Literal[1]
    title: str
    layout: list[dict[str, Any]]


class ScreenRow(BaseModel):
    path: str
    title: str
    error: str


class ScreenListResponse(BaseModel):
    screens: list[ScreenRow]


class ScreenSource(BaseModel):
    slot: str
    path: str
    surface_id: str
    source_id: str
    source: dict[str, Any]


class ScreenResponse(BaseModel):
    path: str
    title: str
    message: CardInstanceMessage
    sources: dict[str, ScreenSource]

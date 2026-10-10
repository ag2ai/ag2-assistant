"""Layered Card availability, diagnostics, and draft Save outcomes."""

from typing import Literal

from pydantic import BaseModel

CardOrigin = Literal["bundled", "global", "profile"]


class CardOut(BaseModel):
    name: str
    description: str
    topic: str
    origin: CardOrigin
    path: str
    enabled: bool


class ProfileCardOut(CardOut):
    suppressed: bool
    available: bool


class CardProblemOut(BaseModel):
    path: str
    origin: CardOrigin
    error: str


class CardFileOut(BaseModel):
    path: str
    name: str
    origin: CardOrigin


class CardListResponse(BaseModel):
    cards: list[CardOut]
    problems: list[CardProblemOut]
    files: list[CardFileOut]


class ProfileCardListResponse(BaseModel):
    cards: list[ProfileCardOut]
    problems: list[CardProblemOut]
    files: list[CardFileOut]


class CardMutatedResponse(CardListResponse):
    ok: Literal[True]


class ProfileCardMutatedResponse(ProfileCardListResponse):
    ok: Literal[True]


class CardSaveResponse(BaseModel):
    status: Literal["saved", "conflict"]
    name: str
    path: str
    conflict_token: str
    history_recorded: bool
    message: str

"""Chats: the drawer rows, the transcript a reopened chat restores, and the
reply a sent message answers with.

Mirrors web/src/schemas/chat.ts. Field lists come from that file, which was
validated against real responses.
"""

from typing import Literal

from pydantic import BaseModel


class TranscriptMessageOut(BaseModel):
    """One display turn: gateway/core.py writes {role, text} pairs."""

    role: Literal["user", "agent"]
    text: str


class ChatRowOut(BaseModel):
    """One drawer row. core.py list_chats normalises every field, so none is
    optional: a missing title becomes "", an absent star becomes False."""

    chat_id: str
    updated: str
    title: str
    starred: bool
    preview: str
    turns: int


class ChatListResponse(BaseModel):
    """GET /api/p/{pid}/chats — newest first."""

    chats: list[ChatRowOut]


class TranscriptResponse(BaseModel):
    """GET /api/p/{pid}/chats/{chat_id}.

    Carries the Chat's own model override ('' = it inherits) alongside the model
    a message sent right now would run on, so the composer's switcher needs no
    second call (ADR 0025).
    """

    chat_id: str
    messages: list[TranscriptMessageOut]
    model: str
    effective_model: str


class MessageResponse(BaseModel):
    """POST /api/p/{pid}/message — the assistant's reply and the chat it landed in."""

    reply: str
    chat_id: str

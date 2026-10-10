"""Explicit draft-definition Save outcomes."""

from typing import Literal

from pydantic import BaseModel


class CardSaveResponse(BaseModel):
    status: Literal["saved", "conflict"]
    name: str
    path: str
    conflict_token: str
    history_recorded: bool
    message: str

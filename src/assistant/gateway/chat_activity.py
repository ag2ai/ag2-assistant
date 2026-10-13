"""Chat activity and read receipts projected from the typed event stream."""

from dataclasses import dataclass, field
from typing import Literal

from ag2.events import BaseEvent

from assistant.events import (
    ChatSeen,
    InquiryAnswered,
    InquiryRaised,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)

ChatStatus = Literal["running", "completed", "error", "waiting"]


@dataclass
class ChatActivity:
    """The latest Turn's state, pending inquiries, and acknowledged outcome."""

    state: ChatStatus | None = None
    turn_id: str = ""
    seen_turn: str = ""
    updated: float = 0.0
    pending: set[str] = field(default_factory=set)

    def observe(self, event: BaseEvent) -> None:
        if isinstance(event, TurnStarted):
            self.state, self.turn_id = "running", event.turn_id
            self.pending.clear()
        elif isinstance(event, InquiryRaised):
            self.pending.add(event.inquiry_id)
        elif isinstance(event, InquiryAnswered):
            self.pending.discard(event.inquiry_id)
        elif isinstance(event, TurnCompleted):
            self.state, self.turn_id = "completed", event.turn_id
            self.pending.clear()
        elif isinstance(event, TurnFailed) and self.turn_id:
            self.state = "error"
            self.pending.clear()
        elif isinstance(event, TurnCancelled):
            self.state, self.turn_id = None, ""
            self.pending.clear()
        elif isinstance(event, ChatSeen) and event.turn_id == self.turn_id:
            self.seen_turn = event.turn_id
        else:
            return
        self.updated = event.created_at

    def snapshot(self, *, running: bool) -> dict:
        """Read outcomes disappear; pending inquiries and active Turns remain visible."""
        status = self.state
        if self.pending:
            status = "waiting"
        elif status == "running" and not running:
            status = "error"
        unread = status in {"completed", "error"} and self.turn_id != self.seen_turn
        if status in {"completed", "error"} and not unread:
            status = None
        return {
            "status": status,
            "status_version": self.turn_id,
            "status_updated": self.updated,
            "unread": unread,
        }

"""Human-in-the-loop core — the pluggable `Asker` seam.

An `Asker` collects a human answer to a question, routed to whatever surface made
the request (a chat channel, or the desktop browser). The agent reaches it
through AG2's `hitl_hook` (for open `context.input()` questions) and, later,
through the permission middleware (for multi-option approvals).
"""

import contextlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ag2.events import HumanMessage


@dataclass
class Question:
    """A question to put to the human."""

    text: str
    options: list[str] | None = None  # None -> free-text answer expected
    detail: str | None = None  # smaller secondary context
    kind: str = "question"  # "question" | "permission"


class PendingGuard:
    """Mixin for askers: track in-flight waits so the turn's timeout clock can
    pause while it would be wrong to keep ticking (see
    ``gateway.repair.wait_reply``) — a human deciding on a prompt, or a
    sanctioned long-running external run (a CLI coding agent) that carries its
    own timeout.

    Implementations wrap the waiting section in ``with self.pending_guard():``;
    ``has_pending()`` then reports whether THIS asker (i.e. this turn) is
    paused on such a wait."""

    _inflight: int = 0

    def has_pending(self) -> bool:
        return self._inflight > 0

    @contextlib.contextmanager
    def pending_guard(self):
        self._inflight += 1
        try:
            yield
        finally:
            self._inflight -= 1


@runtime_checkable
class Asker(Protocol):
    """Collects a human answer, on the surface that requested it."""

    async def ask(self, question: Question, timeout: float | None = None) -> str:
        """Return the human's answer (the chosen option, or free text)."""
        ...


def build_hitl_hook(asker: "Asker"):
    """Adapt an `Asker` into an AG2 `hitl_hook` for open `context.input()` calls.

    AG2's `HumanInputRequest` carries only a prompt string, so these are
    free-text questions. Multi-option approvals call `asker.ask` directly.
    """

    # No annotations on `hook`: AG2 inspects the hook's signature, and a
    # forward-ref annotation it can't resolve raises a Pydantic error at call time.
    async def hook(event):
        answer = await asker.ask(Question(text=event.content))
        return HumanMessage(content=answer)

    return hook

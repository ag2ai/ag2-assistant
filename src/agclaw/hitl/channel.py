"""Channel HITL plumbing — correlate a pending question with its answer.

A chat-originated question blocks the agent run until the user answers *in that
chat* (a button tap, or — for free-text questions — their next message). Each
channel owns a `PendingAsks` to track at most one open question per chat and
resolve it when the answer arrives on the channel's event loop.
"""

import asyncio


class PendingAsks:
    """Tracks one in-flight question per chat and resolves it on answer."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future] = {}

    def is_awaiting(self, chat_id: str) -> bool:
        return chat_id in self._pending

    def create(self, chat_id: str) -> asyncio.Future:
        """Open a pending question for a chat (replaces any existing one)."""
        old = self._pending.get(chat_id)
        if old is not None and not old.done():
            old.cancel()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[chat_id] = fut
        return fut

    def resolve(self, chat_id: str, answer: str) -> bool:
        """Resolve a chat's pending question. Returns False if none was waiting."""
        fut = self._pending.pop(chat_id, None)
        if fut is not None and not fut.done():
            fut.set_result(answer)
            return True
        return False

    def discard(self, chat_id: str) -> None:
        self._pending.pop(chat_id, None)

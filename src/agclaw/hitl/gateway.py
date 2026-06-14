"""Gateway Asker — routes HITL questions through the gateway's own HITL registry.

Unlike `DesktopAsker` (which opens a browser popup), this registers the question
on the gateway's shared `HitlServer` registry so a connected UI client can answer
it: REST clients poll `GET /api/hitl/pending` and POST `/hitl/{id}/answer`;
WebSocket clients get a `question` frame pushed via `on_question` and answer over
the same socket. Either way the styled `/hitl/{id}` page works too.
"""

import asyncio

from agclaw.hitl.base import Question

# Timeout = deny, so a never-answered prompt fails safe instead of hanging forever.
_DEFAULT_TIMEOUT = 300.0


class GatewayAsker:
    """Asks via the gateway's HITL registry; optionally pushes the question."""

    def __init__(self, server, on_question=None, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._server = server
        self._on_question = on_question
        self._timeout = timeout

    async def ask(self, question: Question, timeout: float | None = None) -> str:
        req_id, fut = self._server.register(question)
        if self._on_question is not None:
            try:
                await self._on_question(req_id, question, self._server.path_for(req_id))
            except Exception:
                pass  # a push failure shouldn't abort the prompt; the page still works
        try:
            return await asyncio.wait_for(fut, timeout=timeout or self._timeout)
        except asyncio.TimeoutError:
            from agclaw.permissions import DENY

            return DENY  # unanswered → deny (safe default for permission prompts)
        finally:
            self._server.discard(req_id)

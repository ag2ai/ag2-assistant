"""Gateway core — session management over the AGClaw agent.

The Gateway exposes a simple `send_message(text, session_id)` surface that any
facade (REST/WebSocket) can call. Each session keeps its own multi-turn
conversation via AG2's `AgentReply.ask()` chaining, which is isolated per chain —
session A's history never leaks into session B.

Why direct `ask()` rather than the network Hub here: the Hub's strengths are
*distributed transport* and *multi-agent* coordination (see
`examples/network_gateway_spike.py`, which serves an agent over WebSocket). For a
single-agent UI request/response facade, a per-session reply chain is simpler,
isolated, and lower-latency than routing every turn through a network channel.
The two compose: a future multi-agent or cross-machine deployment can put this
gateway's agent on a Hub without changing the facade.
"""

import asyncio

from autogen.beta import AgentReply

from agclaw.agent import create_agent, turn_prompt
from agclaw.config import Config

REPLY_TIMEOUT = 120.0


class Gateway:
    """Manages per-session conversations with the AGClaw agent."""

    def __init__(
        self,
        config: Config | None = None,
        memory: bool = True,
        platform: str = "gateway",
    ) -> None:
        self._config = config or Config()
        self._memory = memory
        self._platform = platform
        self._agent = None
        self._permissions = None
        # session_id -> latest AgentReply in that session's chain
        self._sessions: dict[str, AgentReply] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        """Create the shared agent. Direct `ask()` is history-isolated per call,
        so one agent safely backs all sessions (each keeps its own reply chain)."""
        from agclaw.permissions import PermissionStore

        self._agent = create_agent(
            self._config, memory=self._memory, platform=self._platform
        )
        self._permissions = PermissionStore()

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    async def send_message(
        self, text: str, session_id: str = "default", asker=None
    ) -> str:
        """Send a user message and return the agent's reply.

        Each session_id keeps its own multi-turn history. Calls within a single
        session are serialised so the conversation chain stays consistent.

        `asker` (optional) binds human-in-the-loop questions and permission
        prompts to the surface that made the request (e.g. the chat it came from).
        """
        if self._agent is None:
            raise RuntimeError("Gateway not started")

        extra = self._ask_kwargs(asker)

        async with self._session_lock(session_id):
            prior = self._sessions.get(session_id)
            prompt = turn_prompt(self._config)  # refresh date/time each turn
            coro = (
                prior.ask(text, prompt=prompt, **extra)
                if prior is not None
                else self._agent.ask(text, prompt=prompt, **extra)
            )
            reply: AgentReply = await asyncio.wait_for(coro, timeout=REPLY_TIMEOUT)
            self._sessions[session_id] = reply
            return reply.body

    def _ask_kwargs(self, asker) -> dict:
        """Per-turn hitl_hook + dependencies bound to this request's asker."""
        from agclaw.permissions import PermissionManager

        # Shared grant store, per-request asker → one PermissionManager per turn.
        deps: dict = {
            PermissionManager: PermissionManager(self._permissions, asker)
        }
        out: dict = {"dependencies": deps}
        if asker is not None:
            from agclaw.hitl import build_hitl_hook

            out["hitl_hook"] = build_hitl_hook(asker)
        return out

    def status(self) -> dict:
        """Lightweight status snapshot for health endpoints."""
        return {
            "status": "ok" if self._agent is not None else "stopped",
            "model": self._config.llm.model,
            "memory": self._memory,
            "platform": self._platform,
            "sessions": len(self._sessions),
        }

    async def close(self) -> None:
        """Release session state."""
        self._sessions.clear()
        self._locks.clear()
        self._agent = None

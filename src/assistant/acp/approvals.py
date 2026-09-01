"""Owner-side approvals for ACP turns.

``ACPAgent`` drives ``Agent._execute`` directly (``ag2.acp.executor.AgentExecutor
._dispatch``) — never through ``Gateway.send_message`` — so the fresh, per-turn
``PermissionManager`` that ``Gateway._ask_kwargs`` builds
(``gateway/core.py:1292``) and hands to ``agent.run(..., dependencies=...)``
never reaches an ACP turn: the executor builds its own ``Context`` straight from
``agent._agent_dependencies``, with no per-call override seam.

The fix reuses the *class* (``PermissionManager``) and the mechanism
(``self.asker.ask(...)``, called directly — see ``permissions.py:494,575``) but
supplies the missing "one instance per turn" wiring via an ``Agent``-level
middleware instead: ``on_turn`` fires once per turn (exactly once per
``session/prompt``, freshly constructed each time — see
``ag2/agent.py::_turn_scope``), which is the same cardinality
``Gateway._ask_kwargs`` relies on. Installing it is additive — no edits to
``gateway/core.py``, ``permissions.py``, or the tools that read
``context.dependencies.get(PermissionManager)`` (``tools/approval.py``,
``tools/files.py``, ``tools/coding.py``).

The asker is owner-side by construction: the ACP client is never consulted and
never sees the approval question (``permissions.py`` calls ``self.asker.ask``
directly, never AG2's ``context.input()``/``hitl_hook`` — the one path
``ag2.acp.executor._reject_human_input`` welds shut). See
``ADR 0033``.

Deny-on-cancel (``ApprovalNotObtained``, ``_PendingToolCalls``) does not keep
the turn alive — confirmed empirically, not a design choice: ``session/cancel``
cancels the turn's task while it's suspended inside the pending
``asker.ask(...)`` await; ``FunctionTool.__call__`` catches whatever that call
raises and reports it as the failing tool's own ``ToolErrorEvent`` (so
``ApprovalNotObtained``'s message reaches the transcript), but the turn's own
task still goes on to finish as ``asyncio.CancelledError`` shortly after
(``ag2.tools.executor.execute_tools`` batches tool calls through
``asyncio.gather(...)``, whose cancellation semantics — CPython bpo-32684 —
report the batch as cancelled once any child accepted a cancel request,
independent of what that child went on to do). ``stop_reason="cancelled"`` is
therefore the correct, expected outcome and is already handled correctly
(``AgentExecutor.heal_cancelled_turn`` via ``ACPAgent._heal``): the side effect
never ran, and the session stays usable for the next prompt.
``_PendingToolCalls`` is a backstop for the rarer case where a tool call never
gets *any* of its own result recorded before the cancellation reaches here (a
custom tool that doesn't route through ``FunctionTool.__call__``'s own
handling) — it records the same message directly, before
``heal_cancelled_turn`` would otherwise synthesize its generic one.
"""

import asyncio
import contextlib
from collections.abc import Iterator

from ag2.annotations import Context
from ag2.events import BaseEvent, ToolCallEvent, ToolCallsEvent, ToolResultEvent
from ag2.middleware import AgentTurn, BaseMiddleware, Middleware
from ag2.middleware.base import ModelResponse

from assistant.gateway.core import Gateway
from assistant.hitl.base import Asker, PendingGuard, Question
from assistant.permissions import PermissionManager

_APPROVAL_NOT_OBTAINED = (
    "Approval not obtained: the request was cancelled or the connection "
    "dropped before the owner could respond."
)


class ApprovalNotObtained(RuntimeError):
    """A pending approval never got an answer — the turn/connection ended first.

    Raised (never returned as a plain deny string) so ``FunctionTool.__call__``
    reports it as the gated tool call's own failure — its message reaches the
    transcript even though the turn still ends up cancelled (see the module
    docstring). The side effect never runs, and the wait ends promptly instead
    of hanging on a browser tab nobody can answer any more.
    """


class _CancellationSafeAsker(PendingGuard):
    """Wraps an owner-side ``Asker`` so a cancelled/aborted wait ends promptly
    instead of hanging forever on a browser tab nobody can answer any more.

    ``session/cancel`` and a dropped connection both cancel the running turn's
    task (``AgentSession.cancel`` / connection teardown in
    ``ag2/acp/agent.py`` and ``sessions.py``); for a turn parked on an
    approval, that lands as ``asyncio.CancelledError`` right here, at the only
    ``await`` in flight.
    """

    def __init__(self, inner: Asker) -> None:
        self._inner = inner

    async def ask(self, question: Question, timeout: float | None = None) -> str:
        with self.pending_guard():
            try:
                return await self._inner.ask(question, timeout=timeout)
            except asyncio.CancelledError as exc:
                raise ApprovalNotObtained(_APPROVAL_NOT_OBTAINED) from exc


def _build_permission_manager(gateway: Gateway, asker: Asker) -> PermissionManager:
    """One turn's worth of permission state, over the gateway's own persistent
    stores — the same shape ``Gateway._ask_kwargs`` builds per ``send_message``."""
    config = gateway.config
    return PermissionManager(
        gateway.permissions,
        asker=_CancellationSafeAsker(asker),
        sandbox=config.tools.sandbox,
        folders=gateway.folders,
        profile=config.data_dir.name,
        workspace_dir=config.workspace_dir,
    )


class _PendingToolCalls:
    """Live-tracks which of this turn's tool calls have no result yet.

    Fed by a stream subscription held for the whole turn. On a cancelled turn,
    :meth:`deny_unresolved` writes a loose ``ToolResultEvent`` for each call
    still open, *before* ``AgentExecutor.heal_cancelled_turn`` runs its own
    repair — that function prefers an existing ``ToolResultEvent`` over the
    generic one it would otherwise synthesize (``executor.py``'s ``completed``
    map), so our clearer reason wins.
    """

    def __init__(self) -> None:
        self._pending: dict[str, ToolCallEvent] = {}

    @contextlib.contextmanager
    def attached(self, stream) -> Iterator[None]:
        async def on_calls(ev: ToolCallsEvent) -> None:
            for call in ev.calls:
                self._pending[call.id] = call

        async def on_result(ev: ToolResultEvent) -> None:
            self._pending.pop(ev.parent_id, None)

        with (
            stream.where(ToolCallsEvent).sub_scope(on_calls),
            stream.where(ToolResultEvent).sub_scope(on_result),
        ):
            yield

    async def deny_unresolved(self, context: Context) -> None:
        for call in list(self._pending.values()):
            await context.send(ToolResultEvent.from_call(call, _APPROVAL_NOT_OBTAINED))


class _OwnerApprovalMiddleware(BaseMiddleware):
    """Installs a fresh, turn-scoped ``PermissionManager`` before each ACP turn
    runs, and records why on a cancel that catches one mid-approval."""

    def __init__(
        self, event: BaseEvent, context: Context, *, gateway: Gateway, asker: Asker
    ) -> None:
        super().__init__(event, context)
        self._gateway = gateway
        self._asker = asker

    async def on_turn(
        self, call_next: AgentTurn, event: BaseEvent, context: Context
    ) -> ModelResponse:
        # setdefault: a Gateway turn pre-injects its own per-turn manager via
        # agent.run(dependencies=...) (core.py:_ask_kwargs) and must keep it.
        context.dependencies.setdefault(
            PermissionManager, _build_permission_manager(self._gateway, self._asker)
        )
        tracker = _PendingToolCalls()
        with tracker.attached(context.stream):
            try:
                return await call_next(event, context)
            except asyncio.CancelledError:
                await tracker.deny_unresolved(context)
                raise


def install_owner_side_approvals(agent, gateway: Gateway, asker: Asker) -> None:
    """Wire ``agent`` (the one ``ACPAgent`` will serve) so every turn's gated
    tool calls ask ``asker`` — never the ACP client.

    Idempotent, and safe on a shared runtime agent: ``on_turn`` only fills the
    ``PermissionManager`` slot when the turn did not bring its own (a Gateway
    ``send_message`` turn pre-injects one per request and keeps it).
    """
    for entry in agent.middleware:
        if getattr(getattr(entry, "middleware", entry), "cls", None) is _OwnerApprovalMiddleware:
            return
    agent.add_middleware(Middleware(_OwnerApprovalMiddleware, gateway=gateway, asker=asker))

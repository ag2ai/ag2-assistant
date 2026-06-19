"""HITL inquiries as first-class, persisted primitives.

A clarifying question, a permission request, or a confirmation used to be
transient — it lived only on the stack of the coroutine `await`-ing it, so a
restart, a dropped socket, or a timed-out turn lost it and stranded the task.

An `Inquiry` makes that request durable and associated with a task: it's written
to a store the moment it's raised (status PENDING) and resolved in place when the
user answers — from the live channel OR, out of band, from any other channel /
the GUI / a REST call. The `DurableAsker` wraps any transport `Asker` to provide
this without changing callers, and because it persists *every* `Question`
(kind="question"|"permission"|"confirmation"), permission prompts become durable
inquiries for free — one coherent model for all human-in-the-loop interaction.
"""

import asyncio
import contextlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from assistant.hitl.base import Asker, Question
from assistant.storage import SerialStore, new_id, now_iso

_PREFIX = "/inquiries/"
_DEFAULT_TIMEOUT = 300.0


class InquiryStatus:
    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"  # timed out unanswered
    CANCELLED = "cancelled"  # the owning task was cancelled

    TERMINAL = frozenset({ANSWERED, EXPIRED, CANCELLED})


@dataclass
class Inquiry:
    """One human-in-the-loop request, persisted and tied to a task."""

    id: str
    text: str
    kind: str = "question"  # "question" | "permission" | "confirmation"
    task_id: str | None = None
    options: list[str] = field(default_factory=list)
    detail: str | None = None
    resource: str | None = None  # for permission: the folder/command at stake
    channel: str | None = None  # the surface it was raised on
    status: str = InquiryStatus.PENDING
    answer: str | None = None
    created_at: str = field(default_factory=now_iso)
    answered_at: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in InquiryStatus.TERMINAL

    def to_question(self) -> Question:
        return Question(
            text=self.text,
            options=self.options or None,
            detail=self.detail,
            kind=self.kind,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Inquiry":
        fields = {f for f in cls.__dataclass_fields__}  # tolerate unknown keys
        return cls(**{k: v for k, v in data.items() if k in fields})


class InquiryStore:
    """CRUD over persisted inquiries, with in-process wakeups on resolution."""

    def __init__(self, path: Path | None = None, store=None, on_change=None) -> None:
        if store is not None:
            self._store = store
        else:
            from autogen.beta.knowledge import SqliteKnowledgeStore

            path = path or (Path.home() / ".ag2assistant" / "inquiries.db")
            path.parent.mkdir(parents=True, exist_ok=True)
            self._store = SerialStore(SqliteKnowledgeStore(str(path)))
        self._events: dict[str, asyncio.Event] = {}
        # Async (inquiry, kind) hook — "raised" on create, the status on resolve —
        # so the durable-HITL lifecycle can ride the AG2 stream as events.
        self._on_change = on_change

    async def _notify(self, inquiry: Inquiry, kind: str) -> None:
        if self._on_change is not None:
            try:
                res = self._on_change(inquiry, kind)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass

    def _wake(self, inquiry_id: str) -> None:
        ev = self._events.get(inquiry_id)
        if ev is not None:
            ev.set()

    async def create(
        self,
        text: str,
        kind: str = "question",
        *,
        task_id: str | None = None,
        options: list[str] | None = None,
        detail: str | None = None,
        resource: str | None = None,
        channel: str | None = None,
    ) -> Inquiry:
        inq = Inquiry(
            id=new_id("inq"),
            text=text,
            kind=kind,
            task_id=task_id,
            options=list(options or []),
            detail=detail,
            resource=resource,
            channel=channel,
        )
        await self._save(inq)
        await self._notify(inq, "raised")
        return inq

    async def get(self, inquiry_id: str) -> Inquiry | None:
        raw = await self._store.read(f"{_PREFIX}{inquiry_id}.json")
        if not raw:
            return None
        return Inquiry.from_dict(json.loads(raw) if isinstance(raw, str) else raw)

    async def _save(self, inq: Inquiry) -> None:
        await self._store.write(f"{_PREFIX}{inq.id}.json", json.dumps(inq.to_dict()))

    async def list_all(self) -> list[Inquiry]:
        out = []
        for name in await self._store.list(_PREFIX):
            raw = await self._store.read(f"{_PREFIX}{name.split('/')[-1]}")
            if raw:
                out.append(Inquiry.from_dict(json.loads(raw) if isinstance(raw, str) else raw))
        return out

    async def list_pending(self, task_id: str | None = None) -> list[Inquiry]:
        items = [i for i in await self.list_all() if i.status == InquiryStatus.PENDING]
        if task_id is not None:
            items = [i for i in items if i.task_id == task_id]
        return sorted(items, key=lambda i: i.created_at)

    async def _resolve(self, inquiry_id: str, status: str, answer: str | None) -> Inquiry | None:
        inq = await self.get(inquiry_id)
        if inq is None or inq.is_terminal:
            return inq  # idempotent: first writer wins
        inq.status = status
        inq.answer = answer
        inq.answered_at = now_iso()
        await self._save(inq)
        self._wake(inquiry_id)
        await self._notify(inq, status)
        return inq

    async def answer(self, inquiry_id: str, text: str) -> Inquiry | None:
        """Resolve an inquiry with the user's answer (from any channel / the GUI)."""
        return await self._resolve(inquiry_id, InquiryStatus.ANSWERED, text)

    async def expire(self, inquiry_id: str) -> Inquiry | None:
        return await self._resolve(inquiry_id, InquiryStatus.EXPIRED, None)

    async def cancel(self, inquiry_id: str) -> Inquiry | None:
        return await self._resolve(inquiry_id, InquiryStatus.CANCELLED, None)

    async def cancel_for_task(self, task_id: str) -> int:
        """Cancel every pending inquiry owned by a task (e.g. the task was cancelled)."""
        n = 0
        for inq in await self.list_pending(task_id):
            await self.cancel(inq.id)
            n += 1
        return n

    async def wait(self, inquiry_id: str, timeout: float | None = None) -> Inquiry | None:
        """Block until the inquiry reaches a terminal state (or `timeout`).

        Wakes immediately on an in-process resolution; falls back to polling so a
        cross-process answer (another worker, a restarted gateway) still resolves.
        """
        ev = self._events.setdefault(inquiry_id, asyncio.Event())
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while True:
                inq = await self.get(inquiry_id)
                if inq is None or inq.is_terminal:
                    return inq
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                if remaining == 0.0:
                    return inq  # caller decides what an unresolved inquiry means
                ev.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(ev.wait(), timeout=min(remaining or 1.0, 1.0))
        finally:
            self._events.pop(inquiry_id, None)


class DurableAsker:
    """Wraps a transport `Asker` so every prompt is persisted as an `Inquiry`.

    `ask()` races the live transport against an out-of-band answer to the stored
    inquiry, so the user can answer on the channel the question arrived on OR from
    anywhere else. The answer is recorded on the inquiry either way. Bind it to a
    task with `rebind(task_id)` so each (sub)task's prompts are tagged with its id
    — the inner transport is shared, so a sub-agent's question still bubbles up to
    the same surface.
    """

    def __init__(
        self,
        inner: Asker,
        store: InquiryStore,
        *,
        task_id: str | None = None,
        channel: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._inner = inner
        self._store = store
        self.task_id = task_id
        self.channel = channel
        self._timeout = timeout

    def rebind(self, task_id: str) -> "DurableAsker":
        """A copy bound to `task_id`, sharing the same transport + store."""
        return DurableAsker(
            self._inner,
            self._store,
            task_id=task_id,
            channel=self.channel,
            timeout=self._timeout,
        )

    async def ask(self, question: Question, timeout: float | None = None) -> str:
        to = timeout or self._timeout
        inq = await self._store.create(
            text=question.text,
            kind=getattr(question, "kind", "question") or "question",
            task_id=self.task_id,
            options=list(question.options or []),
            detail=getattr(question, "detail", None),
            channel=self.channel,
        )

        async def via_transport() -> None:
            ans = await self._inner.ask(question, timeout=to)
            await self._store.answer(inq.id, ans)  # reflect the live answer

        inner_t = asyncio.ensure_future(via_transport())
        wait_t = asyncio.ensure_future(self._store.wait(inq.id, timeout=to))
        try:
            await asyncio.wait({inner_t, wait_t}, return_when=asyncio.FIRST_COMPLETED)
            resolved = await self._store.get(inq.id)
            # if the transport finished without an answer (raised), give the
            # out-of-band path the rest of the window before giving up
            if (
                resolved is None or resolved.status != InquiryStatus.ANSWERED
            ) and not wait_t.done():
                await wait_t
                resolved = await self._store.get(inq.id)
        finally:
            for t in (inner_t, wait_t):
                if not t.done():
                    t.cancel()
            if inner_t.done() and not inner_t.cancelled():
                with contextlib.suppress(Exception):
                    inner_t.result()  # swallow transport errors

        if resolved is not None and resolved.status == InquiryStatus.ANSWERED:
            return resolved.answer or ""
        await self._store.expire(inq.id)
        from assistant.permissions import DENY

        return DENY if inq.kind == "permission" else ""

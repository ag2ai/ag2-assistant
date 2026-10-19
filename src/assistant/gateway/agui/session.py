"""AG-UI runs and subscriptions over a Chat's event stream.

A run starts (or joins) a Turn and projects its events until the Turn ends or asks an
Inquiry; the Turn itself is a gateway task and outlives the HTTP exchange.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from ag2.events import BaseEvent
from ag_ui.core import (
    Event,
    Interrupt,
    ResumeEntry,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunFinishedSuccessOutcome,
    RunStartedEvent,
    TextPart,
)

from assistant.events import Attachment, InquiryRaised
from assistant.gateway.agui.projection import Projector
from assistant.gateway.history import event_cursor
from assistant.gateway.wire import as_drawn

_log = logging.getLogger(__name__)

_POLL_S = 0.2


@dataclass
class TurnRequest:
    """What a client may ask of the server for one Turn; the rest the server resolves."""

    text: str
    asker: Any = None
    attachments: list = field(default_factory=list)
    surface: str = ""
    uploads: tuple[tuple[str, str], ...] = ()
    chat_model: str = ""


def user_text(body: RunAgentInput) -> str:
    """The text of the newest user message; earlier ones are the server's to know."""
    for message in reversed(body.messages):
        if message.role != "user":
            continue
        content = message.content
        if isinstance(content, str):
            return content
        return "\n".join(part.text for part in content or [] if isinstance(part, TextPart))
    return ""


def answer_of(entry: ResumeEntry) -> tuple[str, int | None]:
    """The text, or tapped option index, a resume entry answers an Inquiry with."""
    payload = entry.payload
    if isinstance(payload, dict):
        option = payload.get("option")
        return str(payload.get("text") or ""), option if isinstance(option, int) else None
    return ("" if payload is None else str(payload)), None


def interrupt_of(event: InquiryRaised) -> Interrupt:
    return Interrupt(
        id=event.inquiry_id,
        reason=event.kind,
        message=event.question,
        metadata={
            "detail": event.detail,
            "options": event.options,
            "task_id": event.task_id,
            "expires_at": event.expires_at,
        },
    )


class ChatListener:
    """A subscription to a Chat's stream that primes a Projector with the log so far.

    Events already in the log are never projected again; later ones arrive in order.
    """

    def __init__(self, gateway: Any, chat_id: str) -> None:
        self._gateway = gateway
        self._chat_id = chat_id
        self._queue: asyncio.Queue[BaseEvent] = asyncio.Queue()
        self._seen: set[str] = set()
        self._stream: Any = None
        self._sub: Any = None
        self.projector = Projector()

    def feed(self, event: BaseEvent) -> list[Event]:
        """The AG-UI events ``event`` becomes, with Cards expanded as a client draws them."""
        return self.projector.feed(
            as_drawn(event, self._gateway.catalog, self._gateway.card_sources)
        )

    async def __aenter__(self) -> "ChatListener":
        self._stream = await self._gateway.stream_for(self._chat_id)
        self._sub = self._stream.subscribe(self._receive)
        for event in await self._gateway.replay_events(self._chat_id):
            self._seen.add(event_cursor(event))
            self.feed(event)
        return self

    async def __aexit__(self, *exc: object) -> None:
        with contextlib.suppress(Exception):
            self._stream.unsubscribe(self._sub)

    async def _receive(self, event: BaseEvent) -> None:
        self._queue.put_nowait(event)

    async def next(self, timeout: float = _POLL_S) -> BaseEvent | None:
        """The next event not already in the log, or None after ``timeout``."""
        while True:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout)
            except TimeoutError:
                return None
            if event_cursor(event) not in self._seen:
                return event


def started(thread_id: str, run_id: str) -> Event:
    return RunStartedEvent(thread_id=thread_id, run_id=run_id)


def finished(thread_id: str, run_id: str) -> Event:
    return RunFinishedEvent(thread_id=thread_id, run_id=run_id, outcome=RunFinishedSuccessOutcome())


class ChatRuns:
    """Starts Turns for one profile and keeps them alive past their HTTP exchange."""

    def __init__(self) -> None:
        self._turns: set[asyncio.Task] = set()

    async def _turn(self, gateway: Any, chat_id: str, request: TurnRequest) -> Any:
        """Log the uploaded files, then run the Turn that carries them."""
        for path, name in request.uploads:
            with contextlib.suppress(Exception):
                await gateway.emit_event(chat_id, Attachment(path, name=name))
        return await gateway.send_message(
            request.text,
            chat_id=chat_id,
            asker=request.asker,
            attachments=request.attachments or None,
            surface=request.surface,
            attachment_names=tuple(name for _, name in request.uploads),
            chat_model=request.chat_model,
        )

    def start(self, gateway: Any, chat_id: str, request: TurnRequest) -> asyncio.Task:
        turn = asyncio.ensure_future(self._turn(gateway, chat_id, request))
        self._turns.add(turn)
        turn.add_done_callback(self._ended)
        return turn

    def keep(self, task: asyncio.Task) -> None:
        """Hold a background task until it ends, so it is not collected mid-flight."""
        self._turns.add(task)
        task.add_done_callback(self._ended)

    def _ended(self, turn: asyncio.Task) -> None:
        self._turns.discard(turn)
        if not turn.cancelled() and (error := turn.exception()) is not None:
            _log.debug("turn ended with %r", error)

    async def run(
        self,
        gateway: Any,
        chat_id: str,
        body: RunAgentInput,
        request: TurnRequest | None,
        answer: Callable[[ResumeEntry], Any],
    ) -> AsyncIterator[Event]:
        """One AG-UI run: a new Turn from ``request``, or the running one after ``resume``."""
        run_id = body.run_id or uuid.uuid4().hex
        async with ChatListener(gateway, chat_id) as listener:
            yield started(chat_id, run_id)
            turn: asyncio.Task | None = None
            if body.resume:
                for entry in body.resume:
                    await answer(entry)
            elif request is not None:
                turn = self.start(gateway, chat_id, request)
            while True:
                event = await listener.next()
                if event is None:
                    idle = turn.done() if turn is not None else not gateway.is_running(chat_id)
                    if idle:
                        break
                    continue
                if isinstance(event, InquiryRaised):
                    for out in listener.projector.close():
                        yield out
                    yield RunFinishedEvent(
                        thread_id=chat_id,
                        run_id=run_id,
                        outcome=RunFinishedInterruptOutcome(interrupts=[interrupt_of(event)]),
                    )
                    return
                for out in listener.feed(event):
                    yield out
            while (event := await listener.next(0)) is not None:
                for out in listener.feed(event):
                    yield out
            for out in listener.projector.close():
                yield out
            if turn is not None and not turn.cancelled() and (error := turn.exception()):
                yield RunErrorEvent(message=str(error) or repr(error))
            else:
                yield finished(chat_id, run_id)

    async def watch(
        self, gateway: Any, chat_id: str, stop: asyncio.Event | None = None
    ) -> AsyncIterator[Event]:
        """Events on a Chat outside any run, with each server-started Turn framed as a run.
        Ends once ``stop`` is set."""
        async with ChatListener(gateway, chat_id) as listener:
            run_id: str | None = None
            if gateway.is_running(chat_id):
                run_id = uuid.uuid4().hex
                yield started(chat_id, run_id)
            while stop is None or not stop.is_set():
                event = await listener.next()
                if event is None:
                    if run_id is not None and not gateway.is_running(chat_id):
                        for out in listener.projector.close():
                            yield out
                        yield finished(chat_id, run_id)
                        run_id = None
                    continue
                projected = listener.feed(event)
                if projected and run_id is None:
                    run_id = uuid.uuid4().hex
                    yield started(chat_id, run_id)
                for item in projected:
                    yield item

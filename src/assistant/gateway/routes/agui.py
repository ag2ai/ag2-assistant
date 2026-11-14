"""AG-UI runs and subscriptions, with REST actions on the same Chat's stream."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Mapping
from typing import Any

from ag_ui.core import Event, ResumeEntry, RunAgentInput
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from assistant import feedback as feedback_learner
from assistant.a2ui_click import InvalidClick, parse_click, submit_click
from assistant.events import (
    Attachment,
    FeedbackCleared,
    FeedbackGiven,
)
from assistant.gateway.agui.session import ChatRuns, TurnRequest, answer_of, user_text
from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.routes.common import chat_asker
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import Ok
from assistant.gateway.schemas.agui import FeedResponse
from assistant.gateway.uploads import decode_attachments, persist_uploads

_STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class FeedRequest(BaseModel):
    text: str
    attachments: list[dict[str, Any]] | None = None


class FeedbackRequest(BaseModel):
    target_id: str
    target_kind: str = "message"
    sentiment: str = "up"
    reason: str = ""
    content: str = ""
    request: str = ""


class FeedbackClearRequest(BaseModel):
    target_id: str
    target_kind: str = "message"


class A2UIActionRequest(BaseModel):
    message: dict[str, Any]
    state: dict[str, Any] | None = None


async def _sse(events: AsyncIterator[Event]) -> AsyncIterator[str]:
    encoder = EventEncoder()
    async for event in events:
        yield encoder.encode(event)


def build_profile_router(d: GatewayDeps, get_runtime, *, surfaces: Mapping[str, str]) -> APIRouter:
    """The /api/p/{pid}/chats/{chat_id}/agui endpoints."""
    r = APIRouter()
    runs = ChatRuns()

    async def resolve(runtime: ProfileRuntime, chat_id: str, body: RunAgentInput) -> TurnRequest:
        """The server's own turn from a client's input: uploads saved, surface chosen."""
        props = body.forwarded_props if isinstance(body.forwarded_props, dict) else {}
        raw = props.get("attachments")
        attachments = decode_attachments(raw)
        text = user_text(body)
        if not text and attachments:
            text = "Here is a file I'm sharing with you."
        surface = surfaces.get(str(props.get("surface") or ""), "")
        saved = persist_uploads(runtime.require_config().workspace_dir, raw)
        if saved:
            surface = (surface + "\n\n" if surface else "") + (
                "The user attached file(s), saved in the workspace at: "
                + ", ".join(path for path, _ in saved)
                + ". To edit an uploaded image, call generate_image with "
                "source_image set to its path; to read an uploaded document, "
                "read_file that path."
            )
        return TurnRequest(
            text=text,
            attachments=attachments,
            surface=surface,
            uploads=tuple(saved),
            chat_model=str(props.get("model") or ""),
        )

    @r.post("/chats/{chat_id}/agui")
    async def run(
        chat_id: str, body: RunAgentInput, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Run a Turn on this Chat and stream it as AG-UI events; ``resume`` answers the
        Inquiry that ended the previous run and follows the Turn it unblocks."""
        gateway = runtime.require_gateway()
        request = None
        if not body.resume:
            request = await resolve(runtime, chat_id, body)
            if not request.text:
                raise HTTPException(status_code=422, detail="No user message to run")
            if not gateway.can_accept_turn(chat_id):
                raise HTTPException(status_code=409, detail="A turn is already running")
            request.asker = chat_asker(runtime, chat_id)

        async def answer(entry: ResumeEntry) -> None:
            inquiries = runtime.tasks.inquiries if runtime.tasks is not None else None
            if entry.status == "cancelled":
                if inquiries is not None:
                    with contextlib.suppress(Exception):
                        await inquiries.cancel(entry.interrupt_id)
                return
            text, option = answer_of(entry)
            if option is None and runtime.hitl.answer(entry.interrupt_id, text):
                return
            await gateway.answer_inquiry(entry.interrupt_id, text, option=option)

        events = runs.run(gateway, chat_id, body, request, answer)
        return StreamingResponse(
            _sse(events), media_type=EventEncoder().get_content_type(), headers=_STREAM_HEADERS
        )

    @r.get("/chats/{chat_id}/agui/events")
    async def watch(chat_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """Events on this Chat outside a run: Tasks, Peers, Turns the server started."""
        closed = asyncio.Event()

        async def on_close() -> None:
            closed.set()

        async def events() -> AsyncIterator[Event]:
            runtime.on_close(on_close)
            try:
                async for event in runs.watch(runtime.require_gateway(), chat_id, closed):
                    yield event
            finally:
                runtime.off_close(on_close)

        return StreamingResponse(
            _sse(events()), media_type=EventEncoder().get_content_type(), headers=_STREAM_HEADERS
        )

    # ---- What a client does to a Chat besides running a Turn ----

    @r.post("/chats/{chat_id}/cancel", response_model=Ok)
    async def cancel(chat_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """Stop the Turn running on this Chat; a no-op when none is."""
        await runtime.require_gateway().cancel_turn(chat_id)
        return {"ok": True}

    @r.post("/chats/{chat_id}/feed", response_model=FeedResponse)
    async def feed(chat_id: str, req: FeedRequest, runtime: ProfileRuntime = Depends(get_runtime)):
        """Steer the running Turn with one more message. ``fed`` is False when no Turn is
        running, and the client then runs it as a new one."""
        attachments = decode_attachments(req.attachments)
        gateway = runtime.require_gateway()
        if not await gateway.feed_message(req.text, chat_id, attachments):
            return {"fed": False}
        for path, name in persist_uploads(runtime.require_config().workspace_dir, req.attachments):
            await gateway.emit_event(chat_id, Attachment(path, name=name))
        return {"fed": True}

    @r.post("/chats/{chat_id}/feedback", response_model=Ok)
    async def feedback(
        chat_id: str, req: FeedbackRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """A 👍/👎 and its reason on a generated item: recorded on the Chat's stream, and
        distilled into memory when a reason is given."""
        sentiment = "down" if req.sentiment == "down" else "up"
        reason = req.reason.strip()
        with contextlib.suppress(Exception):
            await runtime.require_gateway().emit_event(
                chat_id,
                FeedbackGiven(
                    req.target_id,
                    target_kind=req.target_kind,
                    sentiment=sentiment,
                    reason=reason,
                    content=req.content[:2000],
                    request=req.request[:2000],
                ),
            )
        if reason:
            # One cheap-model call and a memory write: tracked as work so a sleep host
            # does not stop the process before the preference is saved.
            runtime.require_gateway().background_task(
                feedback_learner.learn(
                    runtime.require_config(),
                    sentiment=sentiment,
                    reason=reason,
                    content=req.content,
                    request=req.request,
                )
            )
        return {"ok": True}

    @r.post("/chats/{chat_id}/feedback/clear", response_model=Ok)
    async def clear_feedback(
        chat_id: str, req: FeedbackClearRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Retract a rating: the visible thumb goes, the memory it taught stays."""
        with contextlib.suppress(Exception):
            await runtime.require_gateway().emit_event(
                chat_id, FeedbackCleared(req.target_id, target_kind=req.target_kind)
            )
        return {"ok": True}

    @r.post("/chats/{chat_id}/a2ui", response_model=Ok)
    async def a2ui_action(
        chat_id: str,
        req: A2UIActionRequest,
        surface: str = "",
        runtime: ProfileRuntime = Depends(get_runtime),
    ):
        """A click on an A2UI surface. A registered server action runs here; any other
        Button action is described to the agent as a new Turn."""
        gateway = runtime.require_gateway()
        try:
            outcome = await submit_click(
                gateway,
                chat_id,
                parse_click(req.message),
                req.state,
            )
        except InvalidClick as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if outcome.turn_text is not None:
            request = TurnRequest(
                text=outcome.turn_text,
                asker=chat_asker(runtime, chat_id),
                surface=surfaces.get(surface, ""),
            )
            runs.start(gateway, chat_id, request)
        if outcome.oversized:  # the click still reaches the agent; only the model is refused
            raise HTTPException(status_code=413, detail="A2UI state too large to store.")
        return {"ok": True}

    return r

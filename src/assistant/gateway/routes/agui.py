"""AG-UI: a Chat's run (POST -> SSE) and its subscription outside runs (GET -> SSE).

The thread id is the Chat id. Pairs with gateway/agui/ (the projection and sessions) and
ADR 0048; both endpoints answer a stream, so neither names a response model.
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

from ag2.a2ui.incoming import A2UIIncomingAction, A2UIIncomingActionResult, parse_incoming_message
from ag2.a2ui.server_action import build_server_action_context, run_server_action
from ag_ui.core import Event, ResumeEntry, RunAgentInput
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from assistant import feedback as feedback_learner
from assistant.a2ui import A2UI_SERVER_ACTIONS
from assistant.events import (
    A2UIActionSubmitted,
    A2UISurfaceDataUpdated,
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

# Cap on the data model a click carries, bounding any client-supplied body.
_MAX_A2UI_STATE_BYTES = 256 * 1024


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


def _clicked_instance_model(state, surface_id: str) -> dict | None:
    """The data model a click sends for the Card instance `surface_id` — None when it
    sends none, or names a different instance."""
    if not isinstance(state, dict) or state.get("surfaceId") != surface_id:
        return None
    data = state.get("data")
    return data if isinstance(data, dict) else None


def _oversized_model(model: dict) -> bool:
    """Whether a client-supplied data model exceeds the bound on a persisted body."""
    try:
        return len(json.dumps(model, ensure_ascii=False).encode()) > _MAX_A2UI_STATE_BYTES
    except (TypeError, ValueError):
        return True


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
            if gateway.is_running(chat_id):
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
        return {"fed": await gateway.feed_message(req.text, chat_id, attachments)}

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
            learner = feedback_learner.learn(
                runtime.require_config(),
                sentiment=sentiment,
                reason=reason,
                content=req.content,
                request=req.request,
            )
            runs.keep(asyncio.ensure_future(learner))
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
        parsed = parse_incoming_message(req.message)
        if not isinstance(parsed, A2UIIncomingActionResult):
            raise HTTPException(status_code=422, detail="Invalid A2UI action.")
        click = parsed.action
        if not click.surface_id or not click.source_component_id:
            raise HTTPException(status_code=422, detail="Unsupported A2UI action.")
        action = A2UI_SERVER_ACTIONS.get(click.name)
        if action is None:
            model = _clicked_instance_model(req.state, click.surface_id)
            oversized = model is not None and _oversized_model(model)
            if model is not None and not oversized:
                await gateway.emit_event(
                    chat_id, A2UISurfaceDataUpdated(click.surface_id, data=model)
                )
            await gateway.emit_event(
                chat_id, A2UIActionSubmitted(click.surface_id, action_name=click.name)
            )
            text = (
                f"[[A2UI_ACTION]] The user clicked the A2UI action '{click.name}' on surface "
                f"'{click.surface_id}'. Its current values are: {click.context}. "
                "Carry out the requested action and respond to the user."
            )
            request = TurnRequest(
                text=text, asker=chat_asker(runtime, chat_id), surface=surfaces.get(surface, "")
            )
            runs.start(gateway, chat_id, request)
            if oversized:  # the click still reaches the agent; only the model is refused
                raise HTTPException(status_code=413, detail="A2UI state too large to store.")
            return {"ok": True}
        click = A2UIIncomingAction(
            name=click.name,
            surface_id=click.surface_id,
            source_component_id=click.source_component_id,
            timestamp=click.timestamp,
            context={**click.context, "surface_id": click.surface_id},
            response_request=click.response_request,
        )
        messages = await run_server_action(
            action,
            click,
            version=req.message.get("version", "v1.0"),
            context=build_server_action_context(gateway.require_agent()),
        )
        for message in messages:
            update = message.get("updateDataModel")
            if isinstance(update, dict) and update.get("surfaceId") == click.surface_id:
                value = update.get("value")
                if update.get("path", "/") == "/" and isinstance(value, dict):
                    await gateway.emit_event(
                        chat_id, A2UISurfaceDataUpdated(click.surface_id, data=value)
                    )
        return {"ok": True}

    return r

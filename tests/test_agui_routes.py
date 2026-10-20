"""The AG-UI endpoints: a run is a POST answered with SSE; an Inquiry ends it as an interrupt."""

import asyncio
import base64

import pytest
from ag2.context import ConversationContext
from ag2.events import ModelMessage, ModelMessageChunk, ModelRequest, ModelResponse
from ag_ui.core import RunAgentInput
from fastapi.testclient import TestClient

from assistant.config import Config
from assistant.gateway.agui.session import ChatRuns, TurnRequest
from assistant.gateway.core import Gateway
from tests.support.apps import api, make_profile_app
from tests.support.chat import logged, run, run_input
from tests.support.fakes import FakeReply, FakeRunMixin, fake_agent_factory


class _Agent(FakeRunMixin):
    """Echo the question; ask an Inquiry first when the message says so."""

    def __init__(self, runtime, *, partial=False, stream_reply=True):
        self.runtime = runtime
        self.partial = partial
        self.stream_reply = stream_reply
        self.tools = []

    async def ask(self, *msg, stream, **kwargs):
        context = ConversationContext(stream=stream)
        await context.send(ModelRequest.ensure_request([msg[0]]))
        reply = f"echo: {msg[0]}"
        if msg[0] == "ask me":
            if self.partial:
                await context.send(ModelMessageChunk("Before "))
            inquiries = self.runtime["value"].tasks.inquiries
            inquiry = await inquiries.create("Proceed?", options=["yes", "no"], chat="c1")
            answered = await inquiries.wait(inquiry.id, timeout=5)
            reply = f"you said {answered.answer}"
        if self.stream_reply:
            await context.send(ModelMessageChunk(reply))
        if self.partial:
            reply = "Before " + reply
        await context.send(ModelResponse(message=ModelMessage(reply)))
        return FakeReply(reply)


def make_client(paths, *, partial=False, stream_reply=True):
    box: dict = {}
    app, pid = make_profile_app(
        paths,
        persist=True,
        agent_factory=fake_agent_factory(
            lambda config, **kw: _Agent(box, partial=partial, stream_reply=stream_reply)
        ),
    )
    return app, pid, box


def test_a_run_streams_the_turn_between_run_started_and_run_finished(paths):
    app, pid, box = make_client(paths)
    with TestClient(app) as client:
        box["value"] = app.state.profiles.get(pid)
        events = run(client, pid, text="hello")
    types = [event["type"] for event in events]
    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    assert events[-1]["outcome"]["type"] == "success"
    said = "".join(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert "echo: hello" in said


def test_an_inquiry_ends_the_run_as_an_interrupt_and_resume_finishes_the_turn(paths):
    app, pid, box = make_client(paths)
    with TestClient(app) as client:
        box["value"] = app.state.profiles.get(pid)
        first = run(client, pid, text="ask me")
        outcome = first[-1]["outcome"]
        assert first[-1]["type"] == "RUN_FINISHED"
        assert outcome["type"] == "interrupt"
        [interrupt] = outcome["interrupts"]
        assert interrupt["message"] == "Proceed?"
        assert interrupt["metadata"]["options"] == ["yes", "no"]

        second = run(
            client,
            pid,
            run_id="r2",
            resume=[{"interruptId": interrupt["id"], "status": "resolved", "payload": "yes"}],
        )
    assert second[-1]["outcome"]["type"] == "success"
    said = "".join(e["delta"] for e in second if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert "you said yes" in said


def test_a_second_message_while_a_turn_waits_is_refused(paths):
    app, pid, box = make_client(paths)
    with TestClient(app) as client:
        box["value"] = app.state.profiles.get(pid)
        run(client, pid, text="ask me")
        response = client.post(
            api(pid, "/chats/c1/agui"), json=run_input(text="another", run_id="r3")
        )
    assert response.status_code == 409


@pytest.mark.parametrize("stream_reply", [True, False])
def test_resume_restores_a_partially_written_answer_under_the_same_id(paths, stream_reply):
    app, pid, box = make_client(paths, partial=True, stream_reply=stream_reply)
    with TestClient(app) as client:
        box["value"] = app.state.profiles.get(pid)
        first = run(client, pid, text="ask me")
        interrupt = first[-1]["outcome"]["interrupts"][0]
        second = run(
            client,
            pid,
            run_id="r2",
            resume=[{"interruptId": interrupt["id"], "status": "resolved", "payload": "yes"}],
        )
        history = client.get(api(pid, "/chats/c1/events")).json()["events"]

    message_id = next(
        e["messageId"]
        for e in first
        if e["type"] == "TEXT_MESSAGE_START" and e["role"] == "assistant"
    )
    text = None
    for event in second:
        if event.get("messageId") != message_id:
            continue
        if event["type"] == "TEXT_MESSAGE_START":
            text = ""
        elif event["type"] == "TEXT_MESSAGE_CONTENT":
            assert text is not None, "resume sent content without reopening the message"
            text += event["delta"]
    assert text == "Before you said yes"
    end = next(e for e in reversed(second) if e["type"] == "TEXT_MESSAGE_END")
    stored_end = next(e for e in reversed(history) if e["type"] == "TEXT_MESSAGE_END")
    assert end["messageId"] == stored_end["messageId"] == message_id
    assert end["rawEvent"]["created_at"] == stored_end["rawEvent"]["created_at"]


def test_a_file_fed_to_a_running_turn_is_saved_and_logged_once(paths):
    app, pid, box = make_client(paths)
    upload = {"name": "fed.txt", "mime": "text/plain", "data": base64.b64encode(b"fed").decode()}
    with TestClient(app) as client:
        runtime = app.state.profiles.get(pid)
        box["value"] = runtime
        run(client, pid, text="ask me")
        response = client.post(
            api(pid, "/chats/c1/feed"), json={"text": "also this", "attachments": [upload]}
        )
        assert response.json() == {"fed": True}
        attachments = [e for e in logged(client, app, pid) if e["type"].endswith("Attachment")]
        assert len(attachments) == 1
        attachment = attachments[0]["data"]
        assert attachment["name"] == "fed.txt"
        assert (runtime.require_config().workspace_dir / attachment["path"]).read_bytes() == b"fed"
        assert len(list(runtime.require_config().workspace_dir.rglob("fed*.txt"))) == 1


def test_an_idle_feed_leaves_attachments_for_the_new_run(paths):
    app, pid, box = make_client(paths)
    upload = {"name": "idle.txt", "mime": "text/plain", "data": base64.b64encode(b"idle").decode()}
    with TestClient(app) as client:
        runtime = app.state.profiles.get(pid)
        box["value"] = runtime
        response = client.post(
            api(pid, "/chats/c1/feed"), json={"text": "also this", "attachments": [upload]}
        )
        assert response.json() == {"fed": False}
        assert not any(e["type"].endswith("Attachment") for e in logged(client, app, pid))
        assert not list(runtime.require_config().workspace_dir.rglob("idle*.txt"))


class _Slow(FakeRunMixin):
    """A turn that takes a moment, so a client can leave while it runs."""

    tools: list = []

    def __init__(self, gate):
        self.gate = gate

    async def ask(self, *msg, stream, **kwargs):
        context = ConversationContext(stream=stream)
        await context.send(ModelRequest.ensure_request([msg[0]]))
        await self.gate.wait()
        await context.send(ModelResponse(message=ModelMessage("finished anyway")))
        return FakeReply("finished anyway")


async def _gateway(paths, agent):
    gateway = Gateway(
        Config.for_paths(paths), memory=False, agent_factory=fake_agent_factory(agent)
    )
    await gateway.start()
    return gateway


async def test_a_turn_the_server_started_is_framed_as_a_run_on_the_subscription(paths):
    gateway = await _gateway(paths, _Slow(asyncio.Event()))
    gate = gateway.require_agent().gate
    runs = ChatRuns()
    seen: list = []

    async def collect():
        async for event in runs.watch(gateway, "c1"):
            seen.append(event.type)
            if event.type == "RUN_FINISHED":
                return

    try:
        watcher = asyncio.ensure_future(collect())
        await asyncio.sleep(0.1)
        turn = asyncio.ensure_future(gateway.send_message("from a peer", chat_id="c1"))
        await asyncio.sleep(0.1)
        gate.set()
        await turn
        await asyncio.wait_for(watcher, 5)
    finally:
        await gateway.close()
    assert seen[0] == "RUN_STARTED"
    assert seen[-1] == "RUN_FINISHED"
    assert "TEXT_MESSAGE_CONTENT" in seen


async def test_a_client_leaving_mid_run_does_not_stop_the_turn(paths):
    gateway = await _gateway(paths, _Slow(asyncio.Event()))
    gate = gateway.require_agent().gate
    runs = ChatRuns()
    body = RunAgentInput(
        thread_id="c1", run_id="r1", state={}, messages=[], tools=[], context=[], forwarded_props={}
    )
    try:
        run = runs.run(gateway, "c1", body, TurnRequest(text="work"), answer=None)
        assert (await anext(run)).type == "RUN_STARTED"
        types = [(await anext(run)).type for _ in range(2)]
        assert "TEXT_MESSAGE_START" in types
        await run.aclose()
        assert gateway.is_running("c1")
        gate.set()
        for _ in range(50):
            if not gateway.is_running("c1"):
                break
            await asyncio.sleep(0.05)
        events = await gateway.replay_events("c1")
    finally:
        await gateway.close()
    assert not gateway.is_running("c1")
    assert any(getattr(event, "content", "") == "finished anyway" for event in events)


def _attachment_events(events):
    return [e for e in events if e["type"] == "CUSTOM" and e["name"].endswith("Attachment")]


def test_an_uploaded_file_reaches_the_run_stream_once_before_the_user_message(paths):
    app, pid, box = make_client(paths)
    upload = {"name": "pic.png", "mime": "image/png", "data": base64.b64encode(b"png").decode()}
    with TestClient(app) as client:
        box["value"] = app.state.profiles.get(pid)
        live = run(client, pid, text="look", props={"attachments": [upload]})
        history = logged(client, app, pid)
    assert len(_attachment_events(live)) == 1
    stored = [e for e in history if e["type"].endswith("Attachment")]
    assert len(stored) == 1
    kinds = [e["name"] for e in live if e["type"] == "CUSTOM"]
    assert kinds[0].endswith("Attachment")


class _Chunked(FakeRunMixin):
    """A turn that streams half a reply, waits, then streams the rest."""

    tools: list = []

    def __init__(self, gate):
        self.gate = gate

    async def ask(self, *msg, stream, **kwargs):
        context = ConversationContext(stream=stream)
        await context.send(ModelRequest.ensure_request([msg[0]]))
        await context.send(ModelMessageChunk("Hello "))
        await context.send(ModelMessageChunk("wor"))
        await self.gate.wait()
        await context.send(ModelMessageChunk("ld"))
        await context.send(ModelResponse(message=ModelMessage("Hello world")))
        return FakeReply("Hello world")


async def test_a_client_joining_mid_stream_gets_the_text_written_so_far(paths):
    gateway = await _gateway(paths, _Chunked(asyncio.Event()))
    gate = gateway.require_agent().gate
    runs = ChatRuns()
    text: list[str] = []

    async def collect():
        async for event in runs.watch(gateway, "c1"):
            if event.type == "TEXT_MESSAGE_CONTENT":
                text.append(event.delta)
            if event.type == "RUN_FINISHED":
                return

    try:
        turn = asyncio.ensure_future(gateway.send_message("hi", chat_id="c1"))
        await asyncio.sleep(0.2)
        watcher = asyncio.ensure_future(collect())
        await asyncio.sleep(0.2)
        gate.set()
        await turn
        await asyncio.wait_for(watcher, 5)
    finally:
        await gateway.close()
    assert "".join(text) == "Hello world"

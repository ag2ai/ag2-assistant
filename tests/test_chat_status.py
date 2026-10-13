"""Chat activity survives replay and read receipts acknowledge only one outcome."""

import asyncio
import json

import pytest

from assistant.config import Config
from assistant.events import InquiryAnswered, InquiryRaised, TurnStarted
from assistant.gateway.core import Gateway
from tests.support.apps import api
from tests.support.fakes import FakeAgent, fake_title_factory


class HeldAgent(FakeAgent):
    """A Turn that waits for the test to release it, optionally failing."""

    def __init__(self, *, fail=False):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = fail

    async def ask(self, *msg, stream=None, **kwargs):
        self.entered.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("Provider unavailable")
        return await super().ask(*msg, stream=stream, **kwargs)


async def gateway(paths, agent=None):
    current = agent or FakeAgent()
    gw = Gateway(
        config=Config.for_paths(paths),
        memory=False,
        onboard=False,
        agent_factory=lambda *args, **kwargs: current,
        title_factory=fake_title_factory(),
    )
    await gw.start()
    return gw


async def row(gw, chat="chat"):
    return next(item for item in await gw.list_chats() if item["chat_id"] == chat)


async def test_running_read_does_not_consume_future_result_and_read_survives_restart(paths):
    agent = HeldAgent()
    gw = await gateway(paths, agent)
    turn = asyncio.create_task(gw.send_message("Hello", chat_id="chat"))
    try:
        await asyncio.wait_for(agent.entered.wait(), 2)
        active = await row(gw)
        assert active["status"] == "running" and not active["unread"]
        assert (await gw.mark_chat_seen("chat", active["status_version"]))["status"] == "running"
        agent.release.set()
        await turn
        done = await row(gw)
        assert done["status"] == "completed" and done["unread"]
        assert (await gw.mark_chat_seen("chat", "stale"))["unread"]
        seen = await gw.mark_chat_seen("chat", done["status_version"])
        assert seen["status"] is None and not seen["unread"]
    finally:
        agent.release.set()
        await asyncio.gather(turn, return_exceptions=True)
        await gw.close()
    restarted = await gateway(paths)
    try:
        assert (await row(restarted))["status"] is None
        await restarted.send_message("Again", chat_id="chat")
        later = await row(restarted)
        assert later["unread"] and later["status_version"] != done["status_version"]
        assert (await restarted.mark_chat_seen("chat", done["status_version"]))["unread"]
    finally:
        await restarted.close()


async def test_unread_failure_survives_restart_and_can_be_read(paths):
    agent = HeldAgent(fail=True)
    gw = await gateway(paths, agent)
    agent.release.set()
    try:
        with pytest.raises(RuntimeError, match="Provider unavailable"):
            await gw.send_message("Hello", chat_id="chat")
        failed = await row(gw)
        assert failed["status"] == "error" and failed["unread"]
    finally:
        await gw.close()
    restarted = await gateway(paths)
    try:
        assert (await row(restarted))["status"] == "error"
        seen = await restarted.mark_chat_seen("chat", failed["status_version"])
        assert seen["status"] is None and not seen["unread"]
    finally:
        await restarted.close()


async def test_hitl_remains_visible_until_all_questions_are_resolved(paths):
    agent = HeldAgent()
    gw = await gateway(paths, agent)
    turn = asyncio.create_task(gw.send_message("Ask", chat_id="chat"))
    try:
        await asyncio.wait_for(agent.entered.wait(), 2)
        await gw.emit_event("chat", InquiryRaised("first", question="Continue?"))
        await gw.emit_event("chat", InquiryRaised("second", question="Which option?"))
        waiting = await row(gw)
        assert waiting["status"] == "waiting" and not waiting["unread"]
        assert (await gw.mark_chat_seen("chat", waiting["status_version"]))["status"] == "waiting"
        await gw.emit_event("chat", InquiryAnswered("first", answer="Yes"))
        assert (await row(gw))["status"] == "waiting"
        await gw.emit_event("chat", InquiryAnswered("second", answer="A"))
        assert (await row(gw))["status"] == "running"
        agent.release.set()
        await turn
        assert (await row(gw))["status"] == "completed"
    finally:
        agent.release.set()
        await asyncio.gather(turn, return_exceptions=True)
        await gw.close()


async def test_stopped_turn_has_no_status(paths):
    agent = HeldAgent()
    gw = await gateway(paths, agent)
    turn = asyncio.create_task(gw.send_message("Ask", chat_id="chat"))
    try:
        await asyncio.wait_for(agent.entered.wait(), 2)
        await gw.emit_event("chat", InquiryRaised("first", question="Continue?"))
        assert await gw.cancel_turn("chat")
        await turn
        stopped = await row(gw)
        assert stopped["status"] is None and not stopped["unread"]
    finally:
        agent.release.set()
        await asyncio.gather(turn, return_exceptions=True)
        await gw.close()


async def test_old_chats_have_no_unread_badge_and_abandoned_turn_is_error(paths):
    gw = await gateway(paths)
    try:
        await gw._event_store.write(
            "/transcript/legacy.json",
            json.dumps({"chat_id": "legacy", "messages": [], "updated": "2026-01-01"}),
        )
        assert (await row(gw, "legacy"))["status"] is None
        await gw.send_message("Hello", chat_id="chat")
        await gw.emit_event("chat", TurnStarted("chat", turn_id="interrupted"))
        assert (await row(gw))["status"] == "error"
    finally:
        await gw.close()
    restarted = await gateway(paths)
    try:
        status = await row(restarted)
        assert status["status"] == "error" and status["status_version"] == "interrupted"
    finally:
        await restarted.close()


def test_seen_route_acknowledges_exact_outcome_and_is_idempotent(profile_app):
    client, pid = profile_app
    client.post(api(pid, "/message"), json={"text": "Hello", "chat_id": "chat"})
    done = client.get(api(pid, "/chats")).json()["chats"][0]
    assert done["status"] == "completed" and done["unread"]
    seen_url = api(pid, "/chats/chat/seen")
    seen = client.post(seen_url, json={"status_version": done["status_version"]})
    assert seen.status_code == 200
    assert seen.json()["status"] is None and not seen.json()["unread"]
    assert (
        client.post(seen_url, json={"status_version": done["status_version"]}).json() == seen.json()
    )
    client.post(api(pid, "/message"), json={"text": "Again", "chat_id": "chat"})
    assert client.post(seen_url, json={"status_version": done["status_version"]}).json()["unread"]
    assert (
        client.post(api(pid, "/chats/missing/seen"), json={"status_version": "x"}).status_code
        == 404
    )
    assert client.post(seen_url, json={}).status_code == 422

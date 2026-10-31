"""A host's ``stream_observers`` see every event on every Chat's stream."""

import asyncio

from ag2.events import ModelResponse

from assistant.events import InquiryRaised, TurnCompleted, TurnStarted
from assistant.gateway.routes.common import chat_asker
from assistant.hitl.base import Question
from assistant.profiles import ProfileRegistry
from tests.support.apps import make_manager
from tests.support.fakes import ScriptedModels


def _manager(paths, *observers):
    pid = ProfileRegistry(paths).create_profile("Work", "#109e91").id
    models = ScriptedModels(lambda _config, _model: ["reply"])
    return make_manager(paths, model_factory=models, stream_observers=observers), pid


class Seen:
    def __init__(self):
        self.events = []

    async def __call__(self, chat_id, event):
        self.events.append((chat_id, event))

    def of(self, chat_id):
        return [event for seen, event in self.events if seen == chat_id]


async def test_an_observer_sees_a_turn_on_its_chat(paths):
    seen = Seen()
    manager, pid = _manager(paths, seen)
    await manager.start()
    try:
        await manager.get(pid).gateway.send_message("hi", chat_id="room")
    finally:
        await manager.close()
    kinds = {type(event) for event in seen.of("room")}
    assert {TurnStarted, ModelResponse, TurnCompleted} <= kinds


async def test_a_chat_is_observed_once_across_turns(paths):
    seen = Seen()
    manager, pid = _manager(paths, seen)
    await manager.start()
    try:
        gateway = manager.get(pid).gateway
        await gateway.send_message("one", chat_id="room")
        await gateway.send_message("two", chat_id="room")
    finally:
        await manager.close()
    assert sum(isinstance(event, TurnStarted) for event in seen.of("room")) == 2


async def test_an_observer_can_answer_a_question_it_sees(paths):
    runtimes = []
    answers = []

    async def auto_answer(_chat_id, event):
        if isinstance(event, InquiryRaised):
            gateway = runtimes[0].gateway
            answers.append(
                asyncio.ensure_future(gateway.answer_inquiry(event.inquiry_id, "no user"))
            )

    manager, pid = _manager(paths, auto_answer)
    await manager.start()
    runtime = manager.get(pid)
    runtimes.append(runtime)
    try:
        answer = await chat_asker(runtime, "room").ask(Question(text="Which one?"))
        assert await asyncio.gather(*answers) == [True]
    finally:
        await manager.close()
    assert answer == "no user"


async def test_an_event_outside_a_turn_reaches_the_observer(paths):
    seen = Seen()
    manager, pid = _manager(paths, seen)
    await manager.start()
    try:
        gateway = manager.get(pid).gateway
        await gateway.emit_event("room", TurnStarted("room", turn_id="t1"))
    finally:
        await manager.close()
    assert [type(event) for event in seen.of("room")] == [TurnStarted]


async def test_a_failing_observer_leaves_the_turn_and_the_next_observer_alone(paths):
    async def broken(_chat_id, _event):
        raise RuntimeError("log is down")

    seen = Seen()
    manager, pid = _manager(paths, broken, seen)
    await manager.start()
    try:
        reply = await manager.get(pid).gateway.send_message("hi", chat_id="room")
    finally:
        await manager.close()
    assert reply == "reply"
    assert seen.of("room")

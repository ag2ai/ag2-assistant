"""``record_in_chat`` adds a message to a Chat's history with no turn."""

import asyncio

from ag2.config.gemini.mappers import convert_messages
from ag2.events import ModelRequest, ModelResponse
from fast_depends.pydantic import PydanticSerializer

from assistant.gateway.core import Gateway
from tests.support.fakes import ScriptedModels, fake_title_factory

RESULT = "Task nightly finished: 3 new items."


def _gateway(config, models):
    return Gateway(
        config,
        memory=False,
        onboard=False,
        title_factory=fake_title_factory(),
        model_factory=models,
    )


def _spoken(events):
    """(role, text) of each request and reply, in history order."""
    spoken = []
    for event in events:
        if isinstance(event, ModelRequest):
            spoken.append(("user", event.parts[0].content))
        elif isinstance(event, ModelResponse) and event.message:
            spoken.append(("agent", event.message.content))
    return spoken


async def test_the_next_turn_reads_the_message_and_no_model_call_happens(config):
    models = ScriptedModels(lambda _config, _model: ["hello back"])
    gateway = _gateway(config, models)
    await gateway.start()
    try:
        await gateway.send_message("hi", chat_id="room")
        await gateway.record_in_chat("room", RESULT)
        assert len(models.requests) == 1
        await gateway.send_message("thanks", chat_id="room")
    finally:
        await gateway.close()
    assert [m for m in _spoken(models.requests[1][3]) if m[0] == "agent"] == [
        ("agent", "hello back"),
        ("agent", RESULT),
    ]


async def test_the_transcript_shows_it_and_a_restart_keeps_it(config):
    models = ScriptedModels(lambda _config, _model: ["hello back"])
    gateway = _gateway(config, models)
    await gateway.start()
    try:
        await gateway.send_message("hi", chat_id="room")
        await gateway.record_in_chat("room", RESULT)
    finally:
        await gateway.close()

    restarted = _gateway(config, models)
    await restarted.start()
    try:
        assert (await restarted.transcript("room"))[-1] == {"role": "agent", "text": RESULT}
        stream = await restarted.stream_for("room")
        assert _spoken(await stream.history.get_events())[-1] == ("agent", RESULT)
    finally:
        await restarted.close()


async def test_a_new_chat_still_starts_with_a_user_message(config):
    models = ScriptedModels(lambda _config, _model: ["ok"])
    gateway = _gateway(config, models)
    await gateway.start()
    try:
        await gateway.record_in_chat("room", RESULT)
        await gateway.send_message("thanks", chat_id="room")
    finally:
        await gateway.close()
    contents = convert_messages(models.requests[0][3], PydanticSerializer())
    assert [content.role for content in contents] == ["user", "model", "user"]


async def test_it_waits_for_a_running_turn(config):
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold(_messages, _context):
        entered.set()
        await release.wait()

    models = ScriptedModels(lambda _config, _model: ["turn reply"], before_call=hold)
    gateway = _gateway(config, models)
    await gateway.start()
    try:
        turn = asyncio.ensure_future(gateway.send_message("hi", chat_id="room"))
        await entered.wait()
        record = asyncio.ensure_future(gateway.record_in_chat("room", RESULT))
        await asyncio.sleep(0.05)
        assert not record.done()
        release.set()
        assert await turn == "turn reply"
        await record
        stream = await gateway.stream_for("room")
        spoken = _spoken(await stream.history.get_events())
    finally:
        await gateway.close()
    assert spoken[-2:] == [("agent", "turn reply"), ("agent", RESULT)]

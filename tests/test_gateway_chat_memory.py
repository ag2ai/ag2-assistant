"""Idle chat streams leave memory without losing history, status, or a live viewer."""

import gc

from ag2.context import ConversationContext
from ag2.events import ModelMessage, ModelResponse

from assistant.config import Config, resolve_config
from assistant.gateway.core import Gateway
from tests.support.fakes import FakeReply, FakeRunMixin, fake_agent_factory, fake_title_factory


class ReplyingAgent(FakeRunMixin):
    """Puts its reply on the stream, as a real agent's ModelResponse would be."""

    tools = []

    async def ask(self, *msg, stream=None, **kwargs) -> FakeReply:
        reply = f"re: {msg[0]}"
        await ConversationContext(stream=stream).send(
            ModelResponse(message=ModelMessage(content=reply))
        )
        return FakeReply(reply)


async def _gateway(paths, tmp_path, cache_size):
    config = Config.for_paths(paths, data_dir=tmp_path)
    config.gateway.chat_cache_size = cache_size
    gw = Gateway(
        config=config,
        memory=False,
        onboard=False,
        agent_factory=fake_agent_factory(ReplyingAgent()),
        title_factory=fake_title_factory(),
    )
    await gw.start()
    return gw


async def _replies(gw, chat_id):
    events = await (await gw.stream_for(chat_id)).history.get_events()
    return [e.message.content for e in events if isinstance(e, ModelResponse)]


def _live_chats(gw) -> int:
    gc.collect()
    return gw.status()["chats"]


async def test_a_freed_chat_keeps_its_history_across_the_next_turn(paths, tmp_path):
    gw = await _gateway(paths, tmp_path, cache_size=0)
    try:
        await gw.send_message("hello", chat_id="c1")
        assert _live_chats(gw) == 0
        await gw.send_message("weather?", chat_id="c1")
        assert await _replies(gw, "c1") == ["re: hello", "re: weather?"]
    finally:
        await gw.close()

    fresh = await _gateway(paths, tmp_path, cache_size=0)
    try:
        assert await _replies(fresh, "c1") == ["re: hello", "re: weather?"]
    finally:
        await fresh.close()


async def test_only_the_most_recent_chats_stay_in_memory(paths, tmp_path):
    gw = await _gateway(paths, tmp_path, cache_size=2)
    try:
        for chat_id in ("a", "b", "c"):
            await gw.send_message("hi", chat_id=chat_id)
        assert _live_chats(gw) == 2
        assert await _replies(gw, "a") == ["re: hi"]
    finally:
        await gw.close()


async def test_a_task_run_leaves_memory_when_it_finishes(paths, tmp_path):
    gw = await _gateway(paths, tmp_path, cache_size=16)
    try:
        await gw.send_message("run", chat_id="task-run:r1")
        assert _live_chats(gw) == 0
        status = await gw.chat_status("task-run:r1")
        assert status["status"] == "completed" and _live_chats(gw) == 0
        assert await _replies(gw, "task-run:r1") == ["re: run"]
    finally:
        await gw.close()


async def test_the_chat_list_reports_status_without_loading_streams(paths, tmp_path):
    gw = await _gateway(paths, tmp_path, cache_size=0)
    try:
        await gw.send_message("hello", chat_id="c1")
        (row,) = [item for item in await gw.list_chats() if item["chat_id"] == "c1"]
        assert row["status"] == "completed" and row["unread"]
        assert _live_chats(gw) == 0

        seen = await gw.mark_chat_seen("c1", row["status_version"])
        assert seen is not None and seen["status"] is None and not seen["unread"]
        (row,) = [item for item in await gw.list_chats() if item["chat_id"] == "c1"]
        assert row["status"] is None and not row["unread"]
    finally:
        await gw.close()


async def test_a_held_stream_sees_every_later_turn(paths, tmp_path):
    gw = await _gateway(paths, tmp_path, cache_size=0)
    try:
        viewer = await gw.stream_for("c1")
        await gw.send_message("hello", chat_id="c1")
        gc.collect()
        await gw.send_message("again", chat_id="c1")
        events = await viewer.history.get_events()
        replies = [e.message.content for e in events if isinstance(e, ModelResponse)]
        assert replies == ["re: hello", "re: again"]
    finally:
        await gw.close()


def test_the_cache_size_is_read_from_the_environment(paths):
    assert resolve_config({}, paths).gateway.chat_cache_size == 16
    env = {"AG2ASSISTANT_CHAT_CACHE_SIZE": "4"}
    assert resolve_config(env, paths).gateway.chat_cache_size == 4
    env = {"AG2ASSISTANT_CHAT_CACHE_SIZE": "-1"}
    assert resolve_config(env, paths).gateway.chat_cache_size == 0

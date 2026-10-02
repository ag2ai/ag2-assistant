"""Two Gateways over one data dir: the web app and an ACP stdio process share a chat.

Each one caches a chat's stream in memory, so turns the other one wrote must still
reach a viewer, and a new turn must not overwrite them on disk.
"""

from ag2.context import ConversationContext
from ag2.events import ModelMessage, ModelResponse

from assistant.config import Config
from assistant.gateway.core import Gateway
from tests.support.fakes import FakeReply, FakeRunMixin, fake_agent_factory


class ReplyingAgent(FakeRunMixin):
    """Puts its reply on the stream, as a real agent's ModelResponse would be."""

    tools = []

    async def ask(self, *msg, stream=None, **kwargs) -> FakeReply:
        reply = f"re: {msg[0]}"
        await ConversationContext(stream=stream).send(
            ModelResponse(message=ModelMessage(content=reply))
        )
        return FakeReply(reply)


async def _gateway(paths, tmp_path):
    gw = Gateway(
        config=Config.for_paths(paths, data_dir=tmp_path),
        memory=False,
        agent_factory=fake_agent_factory(ReplyingAgent()),
    )
    await gw.start()
    return gw


async def _replies(gw, chat_id):
    events = await (await gw.stream_for(chat_id)).history.get_events()
    return [e.message.content for e in events if isinstance(e, ModelResponse)]


async def test_viewer_sees_a_turn_another_process_wrote(paths, tmp_path):
    web, acp = await _gateway(paths, tmp_path), await _gateway(paths, tmp_path)
    try:
        await acp.send_message("hello", chat_id="c1")
        assert await _replies(web, "c1") == ["re: hello"]

        await acp.send_message("weather?", chat_id="c1")
        assert await _replies(web, "c1") == ["re: hello", "re: weather?"]
    finally:
        await web.close()
        await acp.close()


async def test_a_new_turn_keeps_turns_another_process_wrote(paths, tmp_path):
    web, acp = await _gateway(paths, tmp_path), await _gateway(paths, tmp_path)
    try:
        await acp.send_message("hello", chat_id="c1")
        await _replies(web, "c1")  # the web app opens the chat after the first turn
        await acp.send_message("weather?", chat_id="c1")
        await web.send_message("thanks", chat_id="c1")
    finally:
        await web.close()
        await acp.close()

    fresh = await _gateway(paths, tmp_path)
    try:
        assert await _replies(fresh, "c1") == ["re: hello", "re: weather?", "re: thanks"]
    finally:
        await fresh.close()

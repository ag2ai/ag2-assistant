"""The background profile update that runs after a turn's reply."""

import asyncio

import pytest
from ag2.events import ModelMessage, ModelRequest, ModelResponse, Usage

from assistant.config import Config
from assistant.gateway.core import Gateway
from assistant.memory import PROFILE_PATH
from tests.support.fakes import FakeAgent, fake_agent_factory


class MergeModel:
    """A model config whose client records each merge prompt and answers from a script."""

    model = "cheap-model"

    def __init__(self, *answers, before=None):
        self.answers = list(answers) or ["merged"]
        self.before = before
        self.prompts: list[str] = []

    def __call__(self, config, model=None):
        return self

    def create(self):
        return self._merge

    async def _merge(self, messages, context, **options):
        self.prompts.append(messages[0].parts[0].content)
        if self.before is not None:
            await self.before(len(self.prompts))
        answer = self.answers[min(len(self.prompts), len(self.answers)) - 1]
        if isinstance(answer, BaseException):
            raise answer
        usage = Usage(prompt_tokens=100, completion_tokens=10, total_tokens=110)
        return ModelResponse(ModelMessage(answer), usage=usage, model=self.model)


async def memory_gateway(paths, tmp_path, model, **memory):
    config = Config.for_paths(paths, data_dir=tmp_path)
    for field, value in memory.items():
        setattr(config.memory, field, value)
    gw = Gateway(
        config=config,
        memory=True,
        agent_factory=fake_agent_factory(FakeAgent()),
        model_factory=model,
    )
    await gw.start()
    return gw


async def turn(gw, chat_id="s1", earlier=3):
    """One turn on a chat whose history already holds `earlier` requests plus this one."""
    stream = await gw.stream_for(chat_id)
    requests = [ModelRequest.ensure_request([f"{chat_id} ask {n}"]) for n in range(earlier + 1)]
    await stream.history.replace(requests)
    return await gw.send_message("hello", chat_id=chat_id)


async def settled(gw):
    await asyncio.gather(*list(gw._background))


async def profile(gw):
    return await gw._knowledge_store.read(PROFILE_PATH)


async def test_the_reply_returns_before_the_update_finishes(paths, tmp_path):
    release = asyncio.Event()

    async def hold(_):
        await release.wait()

    model = MergeModel("merged", before=hold)
    gw = await memory_gateway(paths, tmp_path, model)
    try:
        reply = await asyncio.wait_for(turn(gw), 2)
        assert reply.startswith("echo")
        assert await profile(gw) is None
        release.set()
        await settled(gw)
        assert await profile(gw) == "merged"
    finally:
        await gw.close()


@pytest.mark.parametrize(("earlier", "merges"), [(2, 0), (3, 1), (4, 0), (7, 1)])
async def test_an_update_runs_on_every_nth_turn_of_a_chat(paths, tmp_path, earlier, merges):
    model = MergeModel()
    gw = await memory_gateway(paths, tmp_path, model)
    try:
        await turn(gw, earlier=earlier)
        await settled(gw)
    finally:
        await gw.close()
    assert len(model.prompts) == merges


async def test_zero_turns_the_update_off(paths, tmp_path):
    model = MergeModel()
    gw = await memory_gateway(paths, tmp_path, model, aggregate_every_n_turns=0)
    try:
        await turn(gw)
        await settled(gw)
    finally:
        await gw.close()
    assert model.prompts == []


async def test_updates_from_two_chats_take_turns_and_both_land(paths, tmp_path):
    running = []
    peak = []

    async def track(_):
        running.append(1)
        peak.append(len(running))
        await asyncio.sleep(0.01)
        running.pop()

    model = MergeModel("first", "second", before=track)
    gw = await memory_gateway(paths, tmp_path, model)
    try:
        await asyncio.gather(turn(gw, "a"), turn(gw, "b"))
        await settled(gw)
        assert await profile(gw) == "second"
    finally:
        await gw.close()
    assert max(peak) == 1
    assert "first" in model.prompts[1]


async def test_a_profile_edited_during_the_merge_is_merged_again(paths, tmp_path):
    gateway = {}

    async def edit_once(call):
        if call == 1:
            await gateway["gw"]._knowledge_store.write(PROFILE_PATH, "remember this")

    model = MergeModel("stale merge", "fresh merge", before=edit_once)
    gateway["gw"] = gw = await memory_gateway(paths, tmp_path, model)
    try:
        await turn(gw)
        await settled(gw)
        assert await profile(gw) == "fresh merge"
    finally:
        await gw.close()
    assert "remember this" in model.prompts[1]


async def test_a_profile_that_keeps_changing_is_left_alone(paths, tmp_path):
    gateway = {}

    async def edit_always(call):
        await gateway["gw"]._knowledge_store.write(PROFILE_PATH, f"edit {call}")

    model = MergeModel(before=edit_always)
    gateway["gw"] = gw = await memory_gateway(paths, tmp_path, model)
    try:
        await turn(gw)
        await settled(gw)
        assert await profile(gw) == "edit 2"
    finally:
        await gw.close()


async def test_a_failed_update_leaves_the_reply_and_the_profile_alone(paths, tmp_path):
    model = MergeModel(RuntimeError("model down"))
    gw = await memory_gateway(paths, tmp_path, model)
    try:
        assert (await turn(gw)).startswith("echo")
        await settled(gw)
        assert await profile(gw) is None
    finally:
        await gw.close()


async def test_a_slow_update_is_stopped_by_its_own_timeout(paths, tmp_path):
    async def hang(_):
        await asyncio.Event().wait()

    model = MergeModel(before=hang)
    gw = await memory_gateway(paths, tmp_path, model, aggregate_timeout_s=0.01)
    try:
        await turn(gw)
        await asyncio.wait_for(settled(gw), 2)
        assert await profile(gw) is None
    finally:
        await gw.close()


async def test_a_chat_can_update_again_after_a_failed_update(paths, tmp_path):
    model = MergeModel(RuntimeError("model down"), "merged")
    gw = await memory_gateway(paths, tmp_path, model)
    try:
        await turn(gw)
        await settled(gw)
        await turn(gw, earlier=7)
        await settled(gw)
        assert await profile(gw) == "merged"
    finally:
        await gw.close()


async def test_the_update_is_recorded_as_usage(paths, tmp_path):
    gw = await memory_gateway(paths, tmp_path, MergeModel())
    try:
        await turn(gw)
        await settled(gw)
        usage = gw.usage_today()
    finally:
        await gw.close()
    assert usage["prompt"] == 100
    assert usage["completion"] == 10

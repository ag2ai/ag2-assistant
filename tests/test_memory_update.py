"""The background profile update that runs after a turn's reply."""

import asyncio

import pytest
from ag2.compact import CompactionSummary
from ag2.events import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallEvent,
    ToolCallsEvent,
    ToolResult,
    ToolResultEvent,
    ToolResultsEvent,
    Usage,
)

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


def exchanges(label, first, last) -> list:
    """Finished turns numbered first..last-1: a request and its final response."""
    events = []
    for n in range(first, last):
        events.append(ModelRequest.ensure_request([f"{label} ask {n}"]))
        events.append(ModelResponse(ModelMessage(f"{label} answer {n}")))
    return events


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
    await stream.history.replace(exchanges(chat_id, 0, earlier + 1))
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


async def update(gw, chat_id, events):
    """Replace the chat's history with `events` and run one profile update over it."""
    stream = await gw.stream_for(chat_id)
    await stream.history.replace(events)
    await gw._update_memory(chat_id)


async def test_a_second_update_reads_only_what_is_new(paths, tmp_path):
    model = MergeModel()
    gw = await memory_gateway(paths, tmp_path, model)
    history = exchanges("chat", 0, 4)
    try:
        await update(gw, "s1", history)
        await update(gw, "s1", history + exchanges("chat", 4, 8))
    finally:
        await gw.close()
    assert "chat ask 3" in model.prompts[0]
    assert "chat ask 3" not in model.prompts[1]
    assert "chat ask 4" in model.prompts[1]
    assert "chat ask 7" in model.prompts[1]


async def test_a_chat_with_no_bookmark_reads_its_last_n_turns(paths, tmp_path):
    model = MergeModel()
    gw = await memory_gateway(paths, tmp_path, model)
    try:
        await update(gw, "s1", exchanges("chat", 0, 8))
    finally:
        await gw.close()
    assert "chat ask 3" not in model.prompts[0]
    assert "chat ask 4" in model.prompts[0]


async def test_a_bookmark_lost_to_compaction_falls_back_to_the_last_n_turns(paths, tmp_path):
    model = MergeModel()
    gw = await memory_gateway(paths, tmp_path, model)
    summary = CompactionSummary(summary="{}", event_count=8)
    try:
        await update(gw, "s1", exchanges("old", 0, 4))
        await update(gw, "s1", [summary, *exchanges("new", 0, 8)])
    finally:
        await gw.close()
    assert "new ask 3" not in model.prompts[1]
    assert "new ask 4" in model.prompts[1]


async def test_a_turn_still_running_is_left_for_the_next_update(paths, tmp_path):
    model = MergeModel()
    gw = await memory_gateway(paths, tmp_path, model)
    history = exchanges("chat", 0, 3)
    running = [
        ModelRequest.ensure_request(["chat ask running"]),
        ModelResponse(
            ModelMessage("calling"),
            tool_calls=ToolCallsEvent([ToolCallEvent(id="c1", name="lookup", arguments="{}")]),
        ),
    ]
    try:
        await update(gw, "s1", [*history, *running])
        finished = [
            *running,
            ToolResultsEvent(
                [ToolResultEvent(parent_id="c1", name="lookup", result=ToolResult("found"))]
            ),
            ModelResponse(ModelMessage("chat answer running")),
        ]
        await update(gw, "s1", [*history, *finished, *exchanges("chat", 4, 8)])
    finally:
        await gw.close()
    assert "chat ask running" not in model.prompts[0]
    assert "chat ask running" in model.prompts[1]
    assert "chat answer running" in model.prompts[1]


async def test_nothing_finished_since_the_bookmark_makes_no_call(paths, tmp_path):
    model = MergeModel()
    gw = await memory_gateway(paths, tmp_path, model)
    history = exchanges("chat", 0, 4)
    try:
        await update(gw, "s1", history)
        await update(gw, "s1", history)
    finally:
        await gw.close()
    assert len(model.prompts) == 1


async def test_a_failed_update_leaves_the_bookmark_so_the_events_are_read_again(paths, tmp_path):
    model = MergeModel("merged", RuntimeError("model down"), "merged again")
    gw = await memory_gateway(paths, tmp_path, model)
    history = exchanges("chat", 0, 4)
    try:
        await update(gw, "s1", history)
        await update(gw, "s1", history + exchanges("chat", 4, 8))
        await update(gw, "s1", history + exchanges("chat", 4, 12))
    finally:
        await gw.close()
    assert "chat ask 3" not in model.prompts[2]
    assert "chat ask 4" in model.prompts[2]


async def test_a_declined_update_leaves_the_bookmark_too(paths, tmp_path):
    gateway = {}

    async def edit_on_the_second_update(call):
        if call in (2, 3):
            await gateway["gw"]._knowledge_store.write(PROFILE_PATH, f"edit {call}")

    model = MergeModel(before=edit_on_the_second_update)
    gateway["gw"] = gw = await memory_gateway(paths, tmp_path, model)
    history = exchanges("chat", 0, 4)
    try:
        await update(gw, "s1", history)
        await update(gw, "s1", history + exchanges("chat", 4, 8))
        await update(gw, "s1", history + exchanges("chat", 4, 12))
    finally:
        await gw.close()
    assert "chat ask 4" in model.prompts[3]


async def test_chats_keep_separate_bookmarks(paths, tmp_path):
    model = MergeModel()
    gw = await memory_gateway(paths, tmp_path, model)
    history = exchanges("a", 0, 4)
    try:
        await update(gw, "a", history)
        await update(gw, "b", exchanges("b", 0, 4))
        await update(gw, "a", history + exchanges("a", 4, 12))
    finally:
        await gw.close()
    assert "a ask 4" in model.prompts[2]


async def test_a_large_tool_result_is_capped_and_conversation_text_is_not(paths, tmp_path):
    model = MergeModel()
    gw = await memory_gateway(paths, tmp_path, model, aggregate_tool_result_chars=30)
    question = "q" * 200
    events = exchanges("chat", 0, 3)
    events.append(ModelRequest.ensure_request([question]))
    events.append(
        ModelResponse(
            ModelMessage("calling"),
            tool_calls=ToolCallsEvent([ToolCallEvent(id="c1", name="lookup", arguments="{}")]),
        )
    )
    events.append(
        ToolResultsEvent(
            [
                ToolResultEvent(parent_id="c1", name="lookup", result=ToolResult("a" * 50)),
                ToolResultEvent(parent_id="c1", name="lookup", result=ToolResult("short")),
            ]
        )
    )
    events.append(ModelResponse(ModelMessage("done")))
    try:
        await update(gw, "s1", events)
    finally:
        await gw.close()
    assert "a" * 31 not in model.prompts[0]
    assert "a" * 15 in model.prompts[0]
    assert "more characters cut]" in model.prompts[0]
    assert "short" in model.prompts[0]
    assert question in model.prompts[0]


async def test_a_chat_shorter_than_n_turns_reads_all_of_them(paths, tmp_path):
    model = MergeModel()
    gw = await memory_gateway(paths, tmp_path, model, aggregate_every_n_turns=2)
    try:
        await update(gw, "s1", exchanges("chat", 0, 2))
    finally:
        await gw.close()
    assert "chat ask 0" in model.prompts[0]

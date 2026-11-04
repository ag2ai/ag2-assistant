"""TurnSummarized: one cheap-model summary per successful chat Turn, when turned on."""

import asyncio
import json

import pytest
from ag2 import Agent
from ag2.events import ModelMessage, ModelResponse, Usage
from ag2.testing import TestClient, TestConfig
from ag_ui.core import RunAgentInput

from assistant.config import Config, resolve_config
from assistant.events import TurnStarted, TurnSummarized
from assistant.gateway.agui.session import ChatRuns, TurnRequest
from assistant.gateway.core import SUMMARY_QUEUE_MAX, Gateway
from assistant.turn_summary import (
    OUTCOME_AIM,
    OUTCOME_MAX,
    TurnSummary,
    build_prompt,
    parent_of,
)
from assistant.yamlio import write_yaml
from tests.support.fakes import FakeReply, FakeRunMixin, fake_agent_factory


class _Turns(FakeRunMixin):
    """A turn agent: "fail" raises, "empty" ends with no text, "hold" waits for ``gate``."""

    def __init__(self):
        self.tools = []
        self.gate = asyncio.Event()

    async def ask(self, *msg, stream=None, **kwargs):
        if msg[0] == "fail":
            raise RuntimeError("provider down")
        if msg[0] == "empty":
            return FakeReply(None)
        if msg[0] == "hold":
            await self.gate.wait()
        return FakeReply(f"did {msg[0]}")


class _Summaries:
    """A summarizer factory over real AG2 agents answering scripted turns; records prompts."""

    def __init__(self, *outs, gate=None):
        self.outs = list(outs)
        self.prompts: list[str] = []
        self.gate = gate

    def __call__(self, config):
        out = self.outs.pop(0) if self.outs else {"title": "Do it", "outcome": "Did it"}
        if isinstance(out, dict):
            out = json.dumps(out)
        return Agent("turn-summarizer", config=_Scripted(self, out))


class _Scripted(TestConfig):
    def __init__(self, summaries, out):
        self.summaries, self.out = summaries, out
        super().__init__(out)

    def create(self):
        return _Recording(self.summaries, self.out)


class _Recording(TestClient):
    def __init__(self, summaries, out):
        self.summaries = summaries
        super().__init__(out)

    async def __call__(self, messages, context, **kwargs):
        texts = [part.content for m in messages for part in getattr(m, "parts", ())]
        self.summaries.prompts.append(" ".join(t for t in texts if isinstance(t, str)))
        if self.summaries.gate is not None:
            await self.summaries.gate.wait()
        return await super().__call__(messages, context, **kwargs)


def _reply(out: dict, *, prompt=0, completion=0) -> ModelResponse:
    usage = Usage(prompt_tokens=prompt, completion_tokens=completion)
    return ModelResponse(ModelMessage(json.dumps(out)), usage=usage)


async def _gateway(paths, tmp_path, summaries, *, on=True, observers=(), timeout=None):
    config = Config.for_paths(paths, data_dir=tmp_path)
    config.gateway.turn_summaries = on
    config.gateway.chat_titles = False
    if timeout is not None:
        config.gateway.turn_summary_timeout_s = timeout
    gateway = Gateway(
        config=config,
        memory=False,
        agent_factory=fake_agent_factory(_Turns()),
        turn_summary_factory=summaries,
        stream_observers=tuple(observers),
    )
    await gateway.start()
    return gateway


async def _settle(gateway):
    async with asyncio.timeout(10):
        while gateway._background:
            await asyncio.gather(*list(gateway._background), return_exceptions=True)


async def _logged(gateway, chat_id, kind):
    events = await (await gateway.stream_for(chat_id)).history.get_events()
    return [e for e in events if isinstance(e, kind)]


async def test_off_by_default_nothing_runs(paths, tmp_path):
    assert Config.for_paths(paths).gateway.turn_summaries is False
    summaries = _Summaries()
    gateway = await _gateway(paths, tmp_path, summaries, on=False)
    try:
        await gateway.send_message("plan a trip", chat_id="c1")
        assert not gateway._background
        assert await _logged(gateway, "c1", TurnSummarized) == []
    finally:
        await gateway.close()
    assert summaries.prompts == []


async def test_a_successful_turn_is_summarized_under_its_turn_id(paths, tmp_path):
    summaries = _Summaries({"title": "Plan a trip", "outcome": "Drafted a three-day plan"})
    gateway = await _gateway(paths, tmp_path, summaries)
    try:
        await gateway.send_message("plan a trip", chat_id="c1")
        await _settle(gateway)
        (started,) = await _logged(gateway, "c1", TurnStarted)
        (summary,) = await _logged(gateway, "c1", TurnSummarized)
    finally:
        await gateway.close()
    assert summary.chat_id == "c1"
    assert summary.turn_id == started.turn_id
    assert (summary.title, summary.outcome) == ("Plan a trip", "Drafted a three-day plan")
    assert summary.parent_turn_id is None
    assert "plan a trip" in summaries.prompts[0] and "did plan a trip" in summaries.prompts[0]


async def test_title_and_outcome_are_capped_in_code(paths, tmp_path):
    long = {"title": "Word " * 30 + ".", "outcome": "x" * 400, "parent_turn_id": None}
    gateway = await _gateway(paths, tmp_path, _Summaries(long))
    try:
        await gateway.send_message("go", chat_id="c1")
        await _settle(gateway)
        (summary,) = await _logged(gateway, "c1", TurnSummarized)
    finally:
        await gateway.close()
    assert len(summary.title) <= 60 and summary.title.endswith("…")
    assert len(summary.outcome) == 120 and summary.outcome.endswith("…")


@pytest.mark.parametrize("text", ["fail", "empty"])
async def test_failed_and_empty_turns_get_no_summary(paths, tmp_path, text):
    summaries = _Summaries()
    gateway = await _gateway(paths, tmp_path, summaries)
    try:
        with pytest.raises(Exception):
            await gateway.send_message(text, chat_id="c1")
        await _settle(gateway)
        assert await _logged(gateway, "c1", TurnSummarized) == []
    finally:
        await gateway.close()
    assert summaries.prompts == []


async def test_a_cancelled_turn_gets_no_summary(paths, tmp_path):
    summaries = _Summaries()
    gateway = await _gateway(paths, tmp_path, summaries)
    try:
        turn = asyncio.ensure_future(gateway.send_message("hold", chat_id="c1"))
        while not gateway.is_running("c1"):
            await asyncio.sleep(0.01)
        assert await gateway.cancel_turn("c1")
        assert await turn == ""
        await _settle(gateway)
        assert await _logged(gateway, "c1", TurnSummarized) == []
    finally:
        await gateway.close()
    assert summaries.prompts == []


async def test_task_run_chats_are_skipped(paths, tmp_path):
    summaries = _Summaries()
    gateway = await _gateway(paths, tmp_path, summaries)
    try:
        await gateway.send_message("run it", chat_id="task-run:r1")
        await _settle(gateway)
    finally:
        await gateway.close()
    assert summaries.prompts == []


def test_a_parent_is_kept_only_when_offered_and_flattened_to_its_root():
    root = TurnSummarized("c1", turn_id="t1", title="A", outcome="a")
    child = TurnSummarized("c1", turn_id="t2", title="B", outcome="b", parent_turn_id="t1")
    recent = [root, child]
    assert parent_of("t1", recent) == "t1"
    assert parent_of("t2", recent) == "t1"
    assert parent_of("t9", recent) is None
    assert parent_of(None, recent) is None
    assert parent_of("", recent) is None


async def test_turns_are_summarized_in_order_and_offered_the_last_five(paths, tmp_path):
    outs = [{"title": f"Step {i}", "outcome": f"Did {i}"} for i in range(6)]
    outs.append({"title": "Step 6", "outcome": "Did 6", "parent_turn_id": "pick"})
    summaries = _Summaries(*outs)
    gateway = await _gateway(paths, tmp_path, summaries)
    try:
        for i in range(6):
            await gateway.send_message(f"step {i}", chat_id="c1")
        await _settle(gateway)
        first = await _logged(gateway, "c1", TurnSummarized)
        summaries.outs[0]["parent_turn_id"] = first[2].turn_id
        await gateway.send_message("step 6", chat_id="c1")
        await _settle(gateway)
        logged = await _logged(gateway, "c1", TurnSummarized)
    finally:
        await gateway.close()
    assert [s.title for s in logged] == [f"Step {i}" for i in range(7)]
    last = summaries.prompts[-1]
    assert all(s.turn_id in last for s in first[1:]) and first[0].turn_id not in last
    assert logged[-1].parent_turn_id == first[2].turn_id


async def test_two_quick_turns_are_summarized_in_order_and_the_second_sees_the_first(
    paths, tmp_path
):
    gate = asyncio.Event()
    summaries = _Summaries(
        {"title": "First", "outcome": "One"}, {"title": "Second", "outcome": "Two"}, gate=gate
    )
    gateway = await _gateway(paths, tmp_path, summaries)
    try:
        await gateway.send_message("one", chat_id="c1")
        await gateway.send_message("two", chat_id="c1")
        assert len(summaries.prompts) == 1
        gate.set()
        await _settle(gateway)
        logged = await _logged(gateway, "c1", TurnSummarized)
    finally:
        await gateway.close()
    assert [s.title for s in logged] == ["First", "Second"]
    assert logged[0].turn_id in summaries.prompts[1]


async def test_a_slow_summary_does_not_delay_the_next_reply(paths, tmp_path):
    gate = asyncio.Event()
    gateway = await _gateway(paths, tmp_path, _Summaries(gate=gate))
    try:
        await gateway.send_message("one", chat_id="c1")
        reply = await asyncio.wait_for(gateway.send_message("two", chat_id="c1"), 2)
        assert reply == "did two"
        gate.set()
        await _settle(gateway)
    finally:
        await gateway.close()


async def test_a_full_queue_drops_the_oldest_pending_turn(paths, tmp_path):
    gate = asyncio.Event()
    outs = [{"title": f"T{i}", "outcome": "ok"} for i in range(SUMMARY_QUEUE_MAX + 2)]
    gateway = await _gateway(paths, tmp_path, _Summaries(*outs, gate=gate))
    try:
        for i in range(SUMMARY_QUEUE_MAX + 2):
            await gateway.send_message(f"m{i}", chat_id="c1")
        gate.set()
        await _settle(gateway)
        logged = await _logged(gateway, "c1", TurnSummarized)
        started = await _logged(gateway, "c1", TurnStarted)
    finally:
        await gateway.close()
    assert len(logged) == SUMMARY_QUEUE_MAX + 1
    assert [s.turn_id for s in logged] == [started[0].turn_id] + [t.turn_id for t in started[2:]]


@pytest.mark.parametrize("failure", ["timeout", "error"])
async def test_a_failed_summary_emits_nothing_and_leaves_the_chat_working(paths, tmp_path, failure):
    gate = asyncio.Event() if failure == "timeout" else None
    first = RuntimeError("quota") if failure == "error" else {"title": "Never", "outcome": "x"}
    summaries = _Summaries(first, {"title": "Next", "outcome": "Fine"}, gate=gate)
    gateway = await _gateway(paths, tmp_path, summaries, timeout=0.2)
    try:
        await gateway.send_message("one", chat_id="c1")
        await _settle(gateway)
        assert await _logged(gateway, "c1", TurnSummarized) == []
        summaries.gate = None
        assert await gateway.send_message("two", chat_id="c1") == "did two"
        await _settle(gateway)
        logged = await _logged(gateway, "c1", TurnSummarized)
    finally:
        await gateway.close()
    assert [s.title for s in logged] == ["Next"]


async def test_stream_observers_receive_the_summary(paths, tmp_path):
    seen = []

    async def observer(chat_id, event):
        if isinstance(event, TurnSummarized):
            seen.append((chat_id, event.title))

    gateway = await _gateway(paths, tmp_path, _Summaries(), observers=[observer])
    try:
        await gateway.send_message("go", chat_id="c1")
        await _settle(gateway)
    finally:
        await gateway.close()
    assert seen == [("c1", "Do it")]


async def test_the_summary_call_is_recorded_in_usage(paths, tmp_path):
    summaries = _Summaries(_reply({"title": "Go", "outcome": "Went"}, prompt=70, completion=9))
    gateway = await _gateway(paths, tmp_path, summaries)
    try:
        await gateway.send_message("go", chat_id="c1")
        await _settle(gateway)
        today = gateway._usage.today()
    finally:
        await gateway.close()
    assert (today["prompt"], today["completion"]) == (70, 9)


async def test_agui_carries_turn_started_in_the_run_and_the_summary_unframed_after_it(
    paths, tmp_path
):
    gate = asyncio.Event()
    gateway = await _gateway(paths, tmp_path, _Summaries(gate=gate))
    runs = ChatRuns()
    watched: list = []
    stop = asyncio.Event()

    async def watch():
        async for event in runs.watch(gateway, "c1", stop):
            watched.append(event)
            if getattr(event, "name", "") == "assistant.events.TurnSummarized":
                stop.set()

    body = RunAgentInput(
        thread_id="c1", run_id="r1", state={}, messages=[], tools=[], context=[], forwarded_props={}
    )
    try:
        run = [e async for e in runs.run(gateway, "c1", body, TurnRequest(text="go"), answer=None)]
        watcher = asyncio.ensure_future(watch())
        await asyncio.sleep(0.3)
        gate.set()
        await asyncio.wait_for(watcher, 5)
    finally:
        await gateway.close()
    started = [e for e in run if getattr(e, "name", "") == "assistant.events.TurnStarted"]
    assert len(started) == 1 and started[0].value["turn_id"]
    assert run[-1].type == "RUN_FINISHED"
    assert [e.type for e in watched] == ["CUSTOM"]
    assert watched[0].value["turn_id"] == started[0].value["turn_id"]


def test_the_setting_comes_from_the_file_or_the_env(paths):
    assert resolve_config({}, paths).gateway.turn_summaries is False
    assert resolve_config({"AG2ASSISTANT_TURN_SUMMARIES": "1"}, paths).gateway.turn_summaries
    write_yaml(paths.config_yaml, {"gateway": {"turn_summaries": True}})
    assert resolve_config({}, paths).gateway.turn_summaries is True
    for off in ("0", "false", "no", "off"):
        env = {"AG2ASSISTANT_TURN_SUMMARIES": off}
        assert resolve_config(env, paths).gateway.turn_summaries is False


def test_the_prompt_and_schema_aim_the_outcome_below_its_cap():
    prompt = build_prompt("ask", "answer", [])
    assert f"at most {OUTCOME_AIM} characters" in prompt
    assert (
        f"at most {OUTCOME_AIM}"
        in TurnSummary.model_json_schema()["properties"]["outcome"]["description"]
    )
    assert OUTCOME_AIM < OUTCOME_MAX

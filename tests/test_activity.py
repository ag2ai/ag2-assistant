"""The activity registry: open holds a sleep/wake host reads, fed by turns, task runs,
post-turn work, voice, channels and ACP; the state file written on flips only."""

import asyncio
import contextlib
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from ag2 import Agent, Plugin
from ag2.middleware import Middleware
from ag2.middleware.base import BaseMiddleware
from ag2.testing import TestConfig
from fastapi.testclient import TestClient

from assistant.activity import Activity, ActivityMiddleware, atomic_write
from assistant.config import Config
from assistant.gateway.core import Gateway
from assistant.gateway.repair import wait_reply
from assistant.gateway.tasks_service import TaskService
from assistant.hitl import DurableAsker, InquiryStore, Question
from assistant.profiles import ProfileRegistry
from assistant.tasks.store import TaskStore
from tests.support.apps import api, make_acp_agent, make_manager, make_profile_app
from tests.support.chat import wait_for
from tests.support.fakes import (
    FakeAgent,
    FakeReply,
    FakeRunMixin,
    failing_agent_factory,
    fake_agent_factory,
    fake_summary_factory,
    fake_title_factory,
)

T0 = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)


class _Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now


def _work(activity) -> dict:
    return {h["detail"]: h["count"] for h in activity.holds() if h["kind"] == "work"}


# --- the registry ---------------------------------------------------------------


def test_work_holds_tally_by_detail_and_idle_since_follows_them():
    clock = _Clock()
    a = Activity(clock=clock)
    assert a.snapshot() == {
        "ts": "2026-11-07T10:00:00Z",
        "idle_since": "2026-11-07T10:00:00Z",
        "holds": [],
    }
    with a.track("turn"), a.track("turn"), a.track("background"):
        assert _work(a) == {"background": 1, "turn": 2}
        assert a.busy and a.snapshot()["idle_since"] is None
    clock.now = T0 + timedelta(seconds=5)
    with a.track("run"):
        pass
    assert a.holds() == [] and not a.busy
    assert a.snapshot()["idle_since"] == "2026-11-07T10:00:05Z"


def test_a_hold_released_twice_is_released_once():
    a = Activity()
    release = a.acquire("turn")
    release()
    release()
    assert a.holds() == []


def test_human_watch_opens_a_hold_while_the_asker_waits():
    a = Activity()
    pending = {"on": False}
    watch = a.human_watch(lambda: pending["on"], lambda: 1_800_000_000.0, fallback_s=60)
    assert watch() is False and a.holds() == []
    pending["on"] = True
    assert watch() is True
    assert a.holds() == [{"kind": "human", "until": "2027-01-15T08:00:00Z"}]
    pending["on"] = False
    assert watch() is False and a.holds() == []


def test_scheduled_hold_sums_profiles_takes_the_earliest_slot_and_hides_no_unknown():
    a = Activity()
    a.set_scheduled("p1", 2, "2026-11-08T09:00:00Z")
    a.set_scheduled("p2", 1, "2026-11-07T18:00:00Z")
    assert a.holds() == [{"kind": "scheduled", "count": 3, "next": "2026-11-07T18:00:00Z"}]
    a.set_scheduled_unknown("p2")
    assert a.holds() == [{"kind": "scheduled"}]  # p2 may hold an earlier slot: no claim
    a.set_scheduled("p2", 0, None)
    a.set_scheduled("p1", 0, None)
    assert a.holds() == []


# --- the file -------------------------------------------------------------------


async def test_the_file_is_written_on_changes_and_idle_only_after_the_grace(tmp_path):
    path = tmp_path / "state" / "activity.json"
    a = Activity(path, idle_grace_s=0.05)
    assert json.loads(path.read_text())["holds"] == []  # the start state, at once
    release = a.acquire("turn")
    assert json.loads(path.read_text())["holds"] == [{"kind": "work", "count": 1, "detail": "turn"}]
    release()
    assert json.loads(path.read_text())["holds"] != []  # the idle write waits for the grace
    release = a.acquire("turn")  # back-to-back: the idle write never lands
    await asyncio.sleep(0.1)
    assert json.loads(path.read_text())["holds"] == [{"kind": "work", "count": 1, "detail": "turn"}]
    release()
    await asyncio.sleep(0.1)
    written = json.loads(path.read_text())
    assert written["holds"] == [] and written["idle_since"] is not None


async def test_polling_during_the_grace_does_not_postpone_the_idle_write(tmp_path):
    path = tmp_path / "activity.json"
    a = Activity(path, idle_grace_s=0.1)
    with a.track("turn"):
        pass
    for _ in range(8):  # GET /api/activity refreshes every profile's schedule
        a.set_scheduled("p", 0, None)
        await asyncio.sleep(0.02)
    assert json.loads(path.read_text())["holds"] == []


async def test_a_schedule_saved_while_idle_reaches_the_file_at_once(tmp_path):
    path = tmp_path / "activity.json"
    a = Activity(path, idle_grace_s=5)
    a.set_scheduled("p", 1, "2026-11-08T09:00:00Z")
    assert json.loads(path.read_text())["holds"][0]["next"] == "2026-11-08T09:00:00Z"


def test_a_new_process_replaces_a_predecessor_s_busy_file(tmp_path):
    path = tmp_path / "activity.json"
    path.write_text(json.dumps({"ts": "x", "idle_since": None, "holds": [{"kind": "work"}]}))
    Activity(path, clock=_Clock())
    assert json.loads(path.read_text())["holds"] == []


async def test_a_failed_write_takes_the_stale_file_away_and_is_retried(tmp_path):
    path = tmp_path / "activity.json"
    broken = {"now": False}

    def write(target, text):
        if broken["now"]:
            raise OSError("disk full")
        atomic_write(target, text)

    a = Activity(path, idle_grace_s=0, retry_s=0.05, write=write)
    broken["now"] = True
    release = a.acquire("turn")  # the busy write fails
    assert not path.exists()  # no file: a host must not take the old idle one for truth
    broken["now"] = False
    await asyncio.sleep(0.2)  # the retry lands
    assert json.loads(path.read_text())["holds"] == [{"kind": "work", "count": 1, "detail": "turn"}]
    release()
    assert json.loads(path.read_text())["holds"] == []


async def test_the_file_beats_while_the_process_runs_and_stops_at_shutdown(tmp_path):
    path = tmp_path / "activity.json"
    clock = _Clock()
    a = Activity(path, idle_grace_s=0, heartbeat_s=0.05, clock=clock)
    with a.track("turn"):
        pass
    clock.now = T0 + timedelta(seconds=30)
    await asyncio.sleep(0.12)
    assert json.loads(path.read_text())["ts"] == "2026-11-07T10:00:30Z"  # rewritten, unchanged
    a.shutdown()
    clock.now = T0 + timedelta(seconds=60)
    await asyncio.sleep(0.12)
    assert json.loads(path.read_text())["ts"] == "2026-11-07T10:00:30Z"


def test_no_path_means_no_file(tmp_path):
    with Activity(idle_grace_s=0).track("turn"):
        pass
    assert list(tmp_path.iterdir()) == []


# --- chat turns through the gateway ----------------------------------------------


class _Gated(FakeRunMixin):
    """A turn that waits on a gate the test holds."""

    tools: list = []

    def __init__(self):
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def ask(self, *msg, stream=None, **kwargs):
        self.started.set()
        await self.gate.wait()
        return FakeReply("done")


class _Raising(FakeRunMixin):
    tools: list = []

    async def ask(self, *msg, stream=None, **kwargs):
        raise RuntimeError("provider down")


async def _gateway(paths, agent, activity, *, chat_titles=False, **config_overrides):
    config = Config.for_paths(paths)
    config.gateway.chat_titles = chat_titles
    for key, value in config_overrides.items():
        setattr(config.gateway, key, value)
    gateway = Gateway(
        config=config,
        memory=False,
        agent_factory=fake_agent_factory(agent),
        title_factory=fake_title_factory(),
        activity=activity,
    )
    await gateway.start()
    return gateway


async def _settled(activity, timeout=5.0):
    deadline = time.monotonic() + timeout
    while activity.busy:
        assert time.monotonic() < deadline, activity.holds()
        await asyncio.sleep(0.01)


async def test_a_turn_holds_work_from_send_to_reply(paths):
    activity = Activity()
    agent = _Gated()
    gateway = await _gateway(paths, agent, activity)
    try:
        turn = asyncio.ensure_future(gateway.send_message("hi", chat_id="c1"))
        await asyncio.wait_for(agent.started.wait(), 5)
        assert _work(activity) == {"turn": 1}
        agent.gate.set()
        assert await asyncio.wait_for(turn, 5) == "done"
        assert _work(activity) == {}
    finally:
        await gateway.close()


async def test_post_turn_work_holds_background_until_it_ends(paths):
    activity = Activity()
    agent = _Gated()
    agent.gate.set()
    gateway = await _gateway(paths, agent, activity, chat_titles=True)
    try:
        await gateway.send_message("hi", chat_id="c1")
        assert _work(activity) == {"background": 1}  # the chat title, spawned by the turn
        await _settled(activity)
        assert (await gateway.list_chats())[0]["title"] == "Fake Title"
    finally:
        await gateway.close()


async def test_cancelled_failed_and_timed_out_turns_release_their_hold(paths):
    activity = Activity()
    agent = _Gated()
    gateway = await _gateway(paths, agent, activity, reply_timeout_s=0.3)
    try:
        turn = asyncio.ensure_future(gateway.send_message("hi", chat_id="c1"))
        await asyncio.wait_for(agent.started.wait(), 5)
        assert await gateway.cancel_turn("c1")
        await asyncio.wait_for(turn, 5)
        assert _work(activity) == {}
        agent.started.clear()
        with pytest.raises(asyncio.TimeoutError):
            await gateway.send_message("hi", chat_id="c2")  # the gate never opens
        assert _work(activity) == {}
    finally:
        await gateway.close()
    gateway = await _gateway(paths, _Raising(), activity)
    try:
        with pytest.raises(RuntimeError, match="provider down"):
            await gateway.send_message("hi", chat_id="c3")
        assert _work(activity) == {}
    finally:
        await gateway.close()


async def test_closing_the_gateway_before_post_turn_work_starts_releases_its_hold(paths):
    activity = Activity()
    agent = _Gated()
    agent.gate.set()
    gateway = await _gateway(paths, agent, activity, chat_titles=True)
    await gateway.send_message("hi", chat_id="c1")
    assert _work(activity) == {"background": 1}  # the title job, not yet started
    await gateway.close()  # cancels it before its first step
    assert activity.holds() == []


# --- a turn parked on a human ------------------------------------------------------


async def test_wait_reply_parks_a_turn_as_a_human_hold():
    activity = Activity()
    pending = {"on": False}
    done = asyncio.Event()

    async def turn():
        await done.wait()
        return "ok"

    watch = activity.human_watch(lambda: pending["on"], fallback_s=30)
    waiting = asyncio.ensure_future(
        wait_reply(turn(), timeout=5, hitl_pending=watch, slice_seconds=0.02)
    )
    await asyncio.sleep(0.05)
    assert activity.holds() == []
    pending["on"] = True
    await asyncio.sleep(0.05)
    assert [h["kind"] for h in activity.holds()] == ["human"]
    pending["on"] = False
    done.set()
    assert await waiting == "ok"
    watch.close()
    assert activity.holds() == []


async def test_a_durable_asker_reports_its_inquiry_deadline_while_parked(tmp_path):
    class _Blocking:
        async def ask(self, question, timeout=None):
            await asyncio.Event().wait()

    asker = DurableAsker(_Blocking(), InquiryStore(path=tmp_path / "inq.db"), timeout=0.3)
    before = time.time()
    ask = asyncio.ensure_future(asker.ask(Question(text="?")))
    await asyncio.sleep(0.05)
    assert asker.has_pending() and before + 0.3 <= asker.pending_until <= time.time() + 0.3
    await ask
    assert asker.pending_until is None and not asker.has_pending()


# --- task runs and schedules -----------------------------------------------------


def _tasks(config, tmp_path, activity, **kwargs) -> TaskService:
    return TaskService(
        config=config,
        store=kwargs.pop("store", None) or TaskStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        summary_factory=fake_summary_factory(),
        activity=activity,
        **kwargs,
    )


async def test_a_run_holds_work_while_queued_and_until_delivered(paths, tmp_path):
    activity = Activity()
    agent = _Gated()
    gateway = await _gateway(paths, agent, activity)
    tasks = _tasks(gateway.config, tmp_path, activity, max_concurrent=1)
    tasks.set_gateway(gateway)
    await tasks.start(scheduler=False)
    try:
        task = await tasks.create_task(name="nightly", prompt="report")
        first = await tasks.start_run(task["id"])
        second = await tasks.start_run(task["id"])
        await asyncio.wait_for(agent.started.wait(), 5)
        assert _work(activity) == {"run": 2, "turn": 1}  # one running, one queued
        agent.gate.set()
        await asyncio.wait_for(tasks._jobs_done(), 10)
        assert _work(activity) == {}
        assert {(await tasks.get_run(r.id))["status"] for r in (first, second)} == {"completed"}
    finally:
        await tasks.close()
        await gateway.close()


async def test_closing_the_task_service_settles_queued_and_running_holds(paths, tmp_path):
    activity = Activity()
    agent = _Gated()
    gateway = await _gateway(paths, agent, activity)
    tasks = _tasks(gateway.config, tmp_path, activity, max_concurrent=1)
    tasks.set_gateway(gateway)
    await tasks.start(scheduler=False)
    task = await tasks.create_task(name="nightly", prompt="report")
    await tasks.start_run(task["id"])
    await tasks.start_run(task["id"])
    await asyncio.wait_for(agent.started.wait(), 5)
    await tasks.close()  # the gate never opens: both jobs are cancelled and awaited
    assert _work(activity) == {}
    await gateway.close()


async def test_the_scheduled_hold_counts_unpaused_tasks_with_a_slot(paths, tmp_path):
    activity = Activity()
    tasks = _tasks(Config.for_paths(paths), tmp_path, activity)
    await tasks.start(scheduler=False)
    daily = await tasks.create_task(
        name="A", prompt="p", schedule={"kind": "cron", "cron": "@daily"}
    )
    await tasks.create_task(name="B", prompt="p")  # manual: no slot
    paused = await tasks.create_task(
        name="C", prompt="p", schedule={"kind": "cron", "cron": "@hourly"}
    )
    await tasks.update_task(paused["id"], paused=True)
    (hold,) = activity.holds()
    slot = datetime.fromisoformat(daily["next_run_at"]).astimezone(timezone.utc)
    assert hold == {
        "kind": "scheduled",
        "count": 1,
        "next": slot.isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    await tasks.delete_task(daily["id"])
    assert activity.holds() == []
    await tasks.create_task(name="D", prompt="p", schedule={"kind": "cron", "cron": "@daily"})
    await tasks.close()
    assert activity.holds()[0]["count"] == 1  # still armed on disk: the next wake survives a stop


async def test_a_fired_slot_is_work_before_its_schedule_is_disarmed(paths, tmp_path):
    activity = Activity()
    seen: list = []

    class _Probing(TaskStore):
        async def update_task(self, task_id, **fields):
            seen.append(_work(activity))  # what a poll would see during the re-arm
            return await super().update_task(task_id, **fields)

    agent = _Gated()
    agent.gate.set()
    gateway = await _gateway(paths, agent, activity)
    tasks = _tasks(gateway.config, tmp_path, activity, store=_Probing(path=tmp_path / "tasks.db"))
    tasks.set_gateway(gateway)
    await tasks.start(scheduler=False)
    try:
        soon = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
        task = await tasks.create_task(
            name="once", prompt="p", schedule={"kind": "once", "at": soon}
        )
        seen.clear()
        await tasks._fire(task["id"])
        assert seen == [{"run": 1}]
        await asyncio.wait_for(tasks._jobs_done(), 10)
        assert activity.holds() == []
    finally:
        await tasks.close()
        await gateway.close()


async def test_a_failed_schedule_read_fails_closed_and_recovers_on_its_own(paths, tmp_path):
    from ag2.knowledge import SqliteKnowledgeStore

    from assistant.storage import SerialStore

    failures = {"left": 0}

    class _FlakyBacking(SerialStore):
        async def read(self, path):  # one record's read fails, the rest are fine
            if failures["left"]:
                failures["left"] -= 1
                raise OSError("disk hiccup")
            return await super().read(path)

    activity = Activity()
    backing = _FlakyBacking(SqliteKnowledgeStore(str(tmp_path / "tasks.db")))
    tasks = _tasks(
        Config.for_paths(paths),
        tmp_path,
        activity,
        store=TaskStore(store=backing),
        refresh_retry_s=0.05,
    )
    await tasks.start(scheduler=False)
    failures["left"] = 2
    await tasks.create_task(name="A", prompt="p", schedule={"kind": "cron", "cron": "@daily"})
    assert activity.holds() == [{"kind": "scheduled"}]  # unknown, and sleep-blocking
    await asyncio.sleep(0.3)  # no poll: the retry alone recovers
    (hold,) = activity.holds()
    assert hold["count"] == 1 and "next" in hold
    await backing.write("/tasks/task-corrupt.json", "null")
    await tasks.refresh_scheduled()
    assert activity.holds() == [{"kind": "scheduled"}]  # a corrupt record is not "fewer tasks"
    await tasks.close()


# --- profiles: boot, archive, startup ---------------------------------------------


async def test_a_profile_that_fails_to_boot_keeps_an_unknown_hold_until_archived(paths):
    registry = ProfileRegistry(paths)
    registry.create_profile("Healthy", "#109e91")
    broken = registry.create_profile("Broken", "#ffa000").id
    manager = make_manager(paths, agent_factory=failing_agent_factory({broken}))
    await manager.start()
    try:
        assert broken in manager.boot_errors
        assert manager.activity.holds() == [{"kind": "scheduled"}]  # never read: no claim
        await manager.archive(broken)
        assert manager.activity.holds() == []
    finally:
        await manager.close()


async def test_startup_work_lasts_until_the_message_listeners_are_up(paths):
    from assistant.connections import ConnectionStore
    from tests.support.fakes import FakeChannel

    gate = asyncio.Event()
    connecting = asyncio.Event()

    class _SlowChannel(FakeChannel):
        async def start(self, router):
            connecting.set()
            await gate.wait()
            await super().start(router)

    ProfileRegistry(paths).create_profile("Work", "#109e91")
    ConnectionStore(paths).create_connection("telegram", "Work", {"TELEGRAM_BOT_TOKEN": "t"})
    manager = make_manager(paths, channel_factory=lambda *a, **k: _SlowChannel(*a, **k))
    assert _work(manager.activity) == {"startup": 1}
    starting = asyncio.ensure_future(manager.start())
    await asyncio.wait_for(connecting.wait(), 5)
    assert _work(manager.activity) == {"startup": 1}
    gate.set()
    await asyncio.wait_for(starting, 5)
    try:
        assert _work(manager.activity) == {}
    finally:
        await manager.close()


async def test_an_orderly_stop_writes_the_settled_file_with_the_next_wake_kept(paths, tmp_path):
    path = tmp_path / "activity.json"
    pid = ProfileRegistry(paths).create_profile("Work", "#109e91").id
    manager = make_manager(paths, env={"AG2ASSISTANT_ACTIVITY_FILE": str(path)})
    assert json.loads(path.read_text())["holds"] == [
        {"kind": "work", "count": 1, "detail": "startup"}
    ]
    await manager.start()
    await (
        manager.get(pid)
        .require_tasks()
        .create_task(name="A", prompt="p", schedule={"kind": "cron", "cron": "@daily"})
    )
    await manager.get(pid).require_gateway().send_message("hi", chat_id="c1")
    await manager.close()  # well inside the 2.5 s grace
    (hold,) = json.loads(path.read_text())["holds"]
    assert hold["kind"] == "scheduled" and hold["count"] == 1
    assert make_manager(paths, env={}).activity.holds() == [
        {"kind": "work", "count": 1, "detail": "startup"}
    ]


# --- the other ways work arrives --------------------------------------------------


async def test_the_middleware_holds_work_for_a_whole_agent_turn():
    activity = Activity()
    during: list = []

    class _Probe(BaseMiddleware):
        async def on_llm_call(self, call_next, events, context):
            during.append(_work(activity))
            return await call_next(events, context)

    agent = Agent(
        "acp",
        config=TestConfig("42"),
        plugins=[Plugin(middleware=[ActivityMiddleware(activity)])],
        middleware=[Middleware(_Probe)],
    )
    assert (await agent.ask("hi")).body == "42"
    assert during == [{"acp_turn": 1}]
    assert activity.holds() == []


async def test_an_acp_request_is_work_until_its_answer_is_delivered(paths):
    import acp
    from ag2.acp.testing import RecordingClient

    delivering = asyncio.Event()
    gate = asyncio.Event()

    class _SlowClient(RecordingClient):
        async def session_update(self, *, session_id, update, **kwargs):
            await super().session_update(session_id=session_id, update=update, **kwargs)
            delivering.set()
            await gate.wait()

    activity = Activity()
    pid = ProfileRegistry(paths).create_profile("ACP", "#109e91").id
    served, _storage, _ = make_acp_agent(paths, pid, activity=activity)
    scope = served.bind(_SlowClient())
    await scope.initialize(protocol_version=1)
    session = await scope.new_session(cwd=".", mcp_servers=[])
    turn = asyncio.ensure_future(scope.prompt(session.session_id, [acp.text_block("hi")]))
    await asyncio.wait_for(delivering.wait(), 5)
    assert _work(activity) == {"acp_prompt": 1}  # the agent is done; delivery is not
    gate.set()
    await asyncio.wait_for(turn, 5)
    assert activity.holds() == []
    await scope.aclose()


async def test_the_acp_agent_the_gateway_serves_carries_the_middleware(paths):
    built: dict = {}

    def agent(config, **kwargs):
        built.update(kwargs)
        return FakeAgent()

    gateway = Gateway(
        Config.for_paths(paths), memory=False, agent_factory=fake_agent_factory(agent)
    )
    await gateway.start()
    try:
        await gateway.acp_agent()
    finally:
        await gateway.close()
    factories = [m for plugin in built["extra_plugins"] for m in plugin._middleware]
    assert any(isinstance(m, ActivityMiddleware) for m in factories)


async def test_delivering_queued_acp_outcomes_on_reconnect_is_work(tmp_path):
    from assistant.acp.outbound import Outbound

    activity = Activity()
    sending = asyncio.Event()
    gate = asyncio.Event()

    class _SlowReceiver:
        async def ext_notification(self, method, params):
            sending.set()
            await gate.wait()

    out = Outbound("cn-1", tmp_path / "outbox", activity=activity)
    await out.notify("room", "a result nobody was there for")
    assert activity.holds() == []  # queued, not delivering
    out.attach(_SlowReceiver())  # a receiver reconnects: the drain starts
    await asyncio.wait_for(sending.wait(), 5)
    assert _work(activity) == {"acp_outbox": 1}
    gate.set()
    await asyncio.wait_for(out.flush(), 5)
    await asyncio.sleep(0)
    assert activity.holds() == []
    await out.aclose()


class _FakeLiveAgent:
    """A realtime session that opens and waits: enough for the voice socket to run."""

    def __init__(self):
        from ag2.context import ConversationContext
        from ag2.stream import MemoryStream

        self.context = ConversationContext(stream=MemoryStream())

    @contextlib.asynccontextmanager
    async def run(self):
        yield self.context


def test_a_voice_call_holds_work_until_the_socket_closes(paths):
    app, pid = make_profile_app(paths, voice_factory=lambda *args, **kwargs: _FakeLiveAgent())
    with TestClient(app) as client:

        def work():
            return [h for h in client.get("/api/activity").json()["holds"] if h["kind"] == "work"]

        with client.websocket_connect(api(pid, "/voice")) as ws:
            assert ws.receive_json() == {"type": "ready"}
            assert work() == [{"kind": "work", "count": 1, "detail": "voice"}]
        wait_for(lambda: work() == [])


async def test_a_channel_message_is_work_until_its_reply_is_delivered():
    from types import SimpleNamespace

    from assistant.channels.discord import DiscordChannel
    from assistant.channels.router import Reply

    activity = Activity()
    sending = asyncio.Event()
    gate = asyncio.Event()

    class _Router:
        def hold(self):
            return activity.track("channel")

        def accepts(self, inbound):
            return True

        async def handle(self, inbound, **kwargs):
            return Reply("hello")

    @contextlib.asynccontextmanager
    async def typing():
        yield

    async def send(chunk):
        sending.set()
        await gate.wait()  # the platform is slow to take the reply

    channel = DiscordChannel(token="fake-token")
    channel._bot_user_id = 999
    channel._router = _Router()
    message = SimpleNamespace(
        content="hi",
        guild=None,
        channel=SimpleNamespace(id=42, typing=typing, send=send),
        author=SimpleNamespace(id=7, bot=False, display_name="Test User"),
        mentions=[],
        attachments=[],
    )
    handling = asyncio.ensure_future(channel.on_message(message))
    await asyncio.wait_for(sending.wait(), 5)
    assert _work(activity) == {"channel": 1}  # the turn is over; delivery is not
    gate.set()
    await asyncio.wait_for(handling, 5)
    assert activity.holds() == []


async def test_an_agui_run_is_work_until_its_last_event_is_taken(paths):
    from ag2.context import ConversationContext
    from ag2.events import ModelMessage, ModelMessageChunk, ModelRequest, ModelResponse
    from ag_ui.core import RunAgentInput

    from assistant.gateway.agui.session import ChatRuns, TurnRequest

    class _Streaming(FakeRunMixin):
        tools: list = []

        def __init__(self):
            self.gate = asyncio.Event()

        async def ask(self, *msg, stream, **kwargs):
            context = ConversationContext(stream=stream)
            await context.send(ModelRequest.ensure_request([msg[0]]))
            await context.send(ModelMessageChunk("Hello "))
            await self.gate.wait()
            await context.send(ModelMessageChunk("world"))
            await context.send(ModelResponse(message=ModelMessage("Hello world")))
            return FakeReply("Hello world")

    activity = Activity()
    agent = _Streaming()
    gateway = await _gateway(paths, agent, activity)
    body = RunAgentInput(
        thread_id="c1", run_id="r1", state={}, messages=[], tools=[], context=[], forwarded_props={}
    )
    try:
        run = ChatRuns().run(gateway, "c1", body, TurnRequest(text="work"), answer=None)
        assert (await anext(run)).type == "RUN_STARTED"
        while (await anext(run)).type != "TEXT_MESSAGE_CONTENT":
            pass
        assert _work(activity) == {"agui": 1, "turn": 1}
        agent.gate.set()
        for _ in range(500):  # the turn ends (and persists) while the client has not read on
            if _work(activity) == {"agui": 1}:
                break
            await asyncio.sleep(0.01)
        assert _work(activity) == {"agui": 1}  # the answer is still being streamed
        assert [event.type async for event in run][-1] == "RUN_FINISHED"
        assert activity.holds() == []
    finally:
        await gateway.close()


# --- the route ------------------------------------------------------------------


def test_the_activity_route_answers_the_snapshot_and_a_poll_is_not_work(paths):
    app, pid = make_profile_app(paths)
    with TestClient(app) as client:
        body = client.get("/api/activity").json()
        assert body["holds"] == [] and body["idle_since"] is not None
        for _ in range(3):
            client.get("/api/health")
        assert client.get("/api/activity").json()["idle_since"] == body["idle_since"]
        client.post(
            f"/api/p/{pid}/tasks",
            json={"name": "A", "prompt": "p", "schedule": {"kind": "cron", "cron": "@daily"}},
        )
        (hold,) = client.get("/api/activity").json()["holds"]
        assert set(hold) == {"kind", "count", "next"} and hold["count"] == 1

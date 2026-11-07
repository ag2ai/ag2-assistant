"""The activity registry: open holds a sleep/wake host reads, fed by turns, task runs,
post-turn work and ACP turns; the state file written on flips only."""

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

from assistant.activity import Activity, ActivityMiddleware
from assistant.config import Config
from assistant.gateway.core import Gateway
from assistant.gateway.repair import wait_reply
from assistant.gateway.tasks_service import TaskService
from assistant.hitl import DurableAsker, InquiryStore
from assistant.tasks.store import TaskStore
from tests.support.apps import make_profile_app
from tests.support.fakes import (
    FakeAgent,
    FakeReply,
    FakeRunMixin,
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
    with a.track("turn"):
        with a.track("turn"), a.track("background"):
            assert _work(a) == {"background": 1, "turn": 2}
            assert a.busy and a.snapshot()["idle_since"] is None
        assert _work(a) == {"turn": 1}
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
    deadline = {"at": 1_800_000_000.0}
    watch = a.human_watch(lambda: pending["on"], lambda: deadline["at"], fallback_s=60)
    assert watch() is False and a.holds() == []
    pending["on"] = True
    assert watch() is True
    assert a.holds() == [{"kind": "human", "until": "2027-01-15T08:00:00Z"}]
    assert watch() is True and len(a.holds()) == 1  # still the same hold
    pending["on"] = False
    assert watch() is False and a.holds() == []
    # no deadline from the asker: the fallback window bounds the hold
    deadline["at"] = None
    pending["on"] = True
    before = time.time()
    watch()
    until = datetime.fromisoformat(a.holds()[0]["until"].replace("Z", "+00:00")).timestamp()
    assert before + 59 <= until <= time.time() + 61
    watch.close()
    assert a.holds() == []


def test_scheduled_hold_sums_profiles_and_takes_the_earliest_slot():
    a = Activity()
    a.set_scheduled("p1", 2, "2026-11-08T09:00:00Z")
    a.set_scheduled("p2", 1, "2026-11-07T18:00:00Z")
    assert a.holds() == [{"kind": "scheduled", "count": 3, "next": "2026-11-07T18:00:00Z"}]
    a.set_scheduled("p2", 0, None)
    assert a.holds() == [{"kind": "scheduled", "count": 2, "next": "2026-11-08T09:00:00Z"}]
    a.set_scheduled("p1", 0, None)
    assert a.holds() == []


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
    stamp = path.stat().st_mtime_ns
    a.set_scheduled("p", 0, None)  # no change in the holds: no write
    assert path.stat().st_mtime_ns == stamp
    release()
    await asyncio.sleep(0.1)
    written = json.loads(path.read_text())
    assert written["holds"] == [] and written["idle_since"] is not None


def test_a_write_that_fails_does_not_raise(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    a = Activity(blocker / "activity.json", idle_grace_s=0)  # a parent that is a file
    with a.track("turn"):
        pass
    assert a.holds() == []


def test_no_path_means_no_file(tmp_path):
    a = Activity(idle_grace_s=0)
    with a.track("turn"):
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
    """A gateway over a fake agent; post-turn work (titles) is off unless asked for."""
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
    """Wait for every work hold to close."""
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


async def test_a_cancelled_turn_releases_its_hold(paths):
    activity = Activity()
    agent = _Gated()
    gateway = await _gateway(paths, agent, activity)
    try:
        turn = asyncio.ensure_future(gateway.send_message("hi", chat_id="c1"))
        await asyncio.wait_for(agent.started.wait(), 5)
        assert await gateway.cancel_turn("c1")
        await asyncio.wait_for(turn, 5)
        assert _work(activity) == {}
    finally:
        await gateway.close()


async def test_a_failed_turn_releases_its_hold(paths):
    activity = Activity()
    gateway = await _gateway(paths, _Raising(), activity)
    try:
        with pytest.raises(RuntimeError, match="provider down"):
            await gateway.send_message("hi", chat_id="c1")
        assert _work(activity) == {}
    finally:
        await gateway.close()


async def test_a_timed_out_turn_releases_its_hold(paths):
    activity = Activity()
    gateway = await _gateway(paths, _Gated(), activity, reply_timeout_s=0.2)
    try:
        with pytest.raises(asyncio.TimeoutError):
            await gateway.send_message("hi", chat_id="c1")
        assert _work(activity) == {}
    finally:
        await gateway.close()


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
    assert [h["kind"] for h in activity.holds()] == []
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

    store = InquiryStore(path=tmp_path / "inq.db")
    asker = DurableAsker(_Blocking(), store, task_id="t1", timeout=0.3)
    assert asker.pending_until is None
    before = time.time()
    ask = asyncio.ensure_future(
        asker.ask(__import__("assistant.hitl", fromlist=["Question"]).Question(text="?"))
    )
    await asyncio.sleep(0.05)
    assert asker.has_pending() and before + 0.3 <= asker.pending_until <= time.time() + 0.3
    await ask
    assert asker.pending_until is None and not asker.has_pending()


# --- task runs ------------------------------------------------------------------


async def test_a_run_holds_work_while_queued_and_until_delivered(paths, tmp_path):
    activity = Activity()
    agent = _Gated()
    gateway = await _gateway(paths, agent, activity)
    tasks = TaskService(
        config=gateway.config,
        store=TaskStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        max_concurrent=1,
        summary_factory=fake_summary_factory(),
        activity=activity,
    )
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


async def test_the_scheduled_hold_counts_unpaused_tasks_with_a_slot(paths, tmp_path):
    activity = Activity()
    tasks = TaskService(
        config=Config.for_paths(paths),
        store=TaskStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        activity=activity,
    )
    await tasks.start(scheduler=False)
    assert activity.holds() == []
    daily = await tasks.create_task(
        name="A", prompt="p", schedule={"kind": "cron", "cron": "@daily"}
    )
    await tasks.create_task(name="B", prompt="p")  # manual: no slot
    paused = await tasks.create_task(
        name="C", prompt="p", schedule={"kind": "cron", "cron": "@hourly"}
    )
    await tasks.update_task(paused["id"], paused=True)
    (hold,) = activity.holds()
    assert hold["kind"] == "scheduled" and hold["count"] == 1
    slot = datetime.fromisoformat(daily["next_run_at"]).astimezone(timezone.utc)
    assert hold["next"] == slot.isoformat(timespec="seconds").replace("+00:00", "Z")
    await tasks.delete_task(daily["id"])
    assert activity.holds() == []


# --- ACP turns through the middleware ----------------------------------------------


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
    reply = await agent.ask("hi")
    assert reply.body == "42"
    assert during == [{"acp_turn": 1}]
    assert activity.holds() == []


async def test_the_acp_agent_the_gateway_serves_carries_the_middleware(paths):
    activity = Activity()
    built: dict = {}

    def agent(config, **kwargs):
        built.update(kwargs)
        return FakeAgent()

    gateway = Gateway(
        Config.for_paths(paths),
        memory=False,
        agent_factory=fake_agent_factory(agent),
        activity=activity,
    )
    await gateway.start()
    try:
        await gateway.acp_agent()
    finally:
        await gateway.close()
    factories = [m for plugin in built["extra_plugins"] for m in plugin._middleware]
    assert any(isinstance(m, ActivityMiddleware) for m in factories)


# --- the route ------------------------------------------------------------------


def test_the_activity_route_answers_the_snapshot(paths):
    app, pid = make_profile_app(paths)
    with TestClient(app) as client:
        body = client.get("/api/activity").json()
        assert body["holds"] == [] and body["idle_since"] is not None
        client.post(
            f"/api/p/{pid}/tasks",
            json={"name": "A", "prompt": "p", "schedule": {"kind": "cron", "cron": "@daily"}},
        )
        (hold,) = client.get("/api/activity").json()["holds"]
        assert hold["kind"] == "scheduled" and hold["count"] == 1 and "next" in hold
        assert set(hold) == {"kind", "count", "next"}


async def test_the_env_names_the_file_the_install_mirrors_its_activity_to(paths, tmp_path):
    from assistant.profiles import ProfileRegistry
    from tests.support.apps import make_manager

    path = tmp_path / "activity.json"
    manager = make_manager(paths, env={"AG2ASSISTANT_ACTIVITY_FILE": str(path)})
    pid = ProfileRegistry(paths).create_profile("Work", "#109e91").id
    await manager.start()
    try:
        assert manager.activity.holds() == []  # the file follows after the idle grace
        await manager.get(pid).require_gateway().send_message("hi", chat_id="c1")
        assert json.loads(path.read_text())["holds"] != []  # the turn (and its title job)
        await _settled(manager.activity)
        await asyncio.sleep(3)  # the idle grace
        assert json.loads(path.read_text())["holds"] == []
    finally:
        await manager.close()
    unstarted = make_manager(paths, env={})
    assert unstarted.activity.holds() == [{"kind": "work", "count": 1, "detail": "startup"}]


async def test_polling_during_the_grace_does_not_postpone_the_idle_write(tmp_path):
    path = tmp_path / "activity.json"
    a = Activity(path, idle_grace_s=0.1)
    with a.track("turn"):
        pass
    for _ in range(8):  # GET /api/activity refreshes every profile's schedule
        a.set_scheduled("p", 0, None)
        await asyncio.sleep(0.02)
    assert json.loads(path.read_text())["holds"] == []


async def test_a_turn_starting_during_the_grace_keeps_the_file_busy_without_a_rewrite(tmp_path):
    path = tmp_path / "activity.json"
    a = Activity(path, idle_grace_s=0.1)
    release = a.acquire("turn")
    stamp = path.stat().st_mtime_ns
    release()
    release = a.acquire("turn")
    await asyncio.sleep(0.2)
    assert json.loads(path.read_text())["holds"] == [{"kind": "work", "count": 1, "detail": "turn"}]
    assert path.stat().st_mtime_ns == stamp
    release()


async def test_archiving_a_profile_drops_its_scheduled_hold(paths):
    from assistant.profiles import ProfileRegistry
    from tests.support.apps import make_manager

    registry = ProfileRegistry(paths)
    work = registry.create_profile("Work", "#109e91").id
    personal = registry.create_profile("Personal", "#f95339").id
    manager = make_manager(paths)
    await manager.start()
    try:
        await (
            manager.get(work)
            .require_tasks()
            .create_task(name="A", prompt="p", schedule={"kind": "cron", "cron": "@daily"})
        )
        assert [h["kind"] for h in manager.activity.holds()] == ["scheduled"]
        await manager.archive(work, new_default=personal)
        assert manager.activity.holds() == []
    finally:
        await manager.close()


def test_a_new_process_replaces_a_predecessor_s_busy_file(tmp_path):
    path = tmp_path / "activity.json"
    path.write_text(json.dumps({"ts": "x", "idle_since": None, "holds": [{"kind": "work"}]}))
    clock = _Clock()
    Activity(path, clock=clock)
    assert json.loads(path.read_text()) == {
        "ts": "2026-11-07T10:00:00Z",
        "idle_since": "2026-11-07T10:00:00Z",
        "holds": [],
    }


def test_a_fresh_process_with_nothing_to_do_still_writes_its_file(tmp_path):
    path = tmp_path / "state" / "activity.json"
    Activity(path)
    assert json.loads(path.read_text())["holds"] == []


async def test_closing_the_gateway_before_post_turn_work_starts_releases_its_hold(paths):
    activity = Activity()
    agent = _Gated()
    agent.gate.set()
    gateway = await _gateway(paths, agent, activity, chat_titles=True)
    await gateway.send_message("hi", chat_id="c1")
    assert _work(activity) == {"background": 1}  # the title job, not yet started
    await gateway.close()  # cancels it before its first step
    assert activity.holds() == []


async def test_a_failed_write_is_retried_until_the_file_tells_the_truth(tmp_path):
    blocker = tmp_path / "state"
    blocker.write_text("")  # the parent is a file, so every write fails
    path = blocker / "activity.json"
    a = Activity(path, idle_grace_s=0, retry_s=0.05)
    release = a.acquire("turn")
    assert not path.exists() and a.busy
    blocker.unlink()  # storage recovers; nothing else changes
    await asyncio.sleep(0.2)
    assert json.loads(path.read_text())["holds"] == [{"kind": "work", "count": 1, "detail": "turn"}]
    release()
    assert json.loads(path.read_text())["holds"] == []


def test_a_poll_flushes_a_failed_write(tmp_path):
    blocker = tmp_path / "state"
    blocker.write_text("")
    path = blocker / "activity.json"
    a = Activity(path, idle_grace_s=0)
    a.acquire("turn")
    blocker.unlink()
    a.flush()
    assert json.loads(path.read_text())["holds"] == [{"kind": "work", "count": 1, "detail": "turn"}]
    stamp = path.stat().st_mtime_ns
    a.flush()  # nothing to recover: no rewrite
    assert path.stat().st_mtime_ns == stamp


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
    from tests.support.apps import api
    from tests.support.chat import wait_for

    app, pid = make_profile_app(paths, voice_factory=lambda *args, **kwargs: _FakeLiveAgent())
    with TestClient(app) as client:

        def voice_holds():
            return [h for h in client.get("/api/activity").json()["holds"] if h["kind"] == "work"]

        with client.websocket_connect(api(pid, "/voice")) as ws:
            assert ws.receive_json() == {"type": "ready"}
            assert voice_holds() == [{"kind": "work", "count": 1, "detail": "voice"}]
        wait_for(lambda: voice_holds() == [])


async def test_a_schedule_saved_while_idle_reaches_the_file_at_once(tmp_path):
    path = tmp_path / "activity.json"
    a = Activity(path, idle_grace_s=5)
    a.set_scheduled("p", 1, "2026-11-08T09:00:00Z")
    assert json.loads(path.read_text())["holds"] == [
        {"kind": "scheduled", "count": 1, "next": "2026-11-08T09:00:00Z"}
    ]
    a.set_scheduled("p", 1, "2026-11-07T18:00:00Z")  # an earlier slot: also at once
    assert json.loads(path.read_text())["holds"][0]["next"] == "2026-11-07T18:00:00Z"


async def test_a_fired_slot_is_work_before_its_schedule_is_disarmed(paths, tmp_path):
    """The scheduler's callback holds work through the re-arm and run creation, so a
    poll in between never sees a once-task gone from the schedule with no run yet."""
    activity = Activity()
    seen: list = []

    class _Probing(TaskStore):
        async def update_task(self, task_id, **fields):
            seen.append(_work(activity))  # what a poll would see during the re-arm
            return await super().update_task(task_id, **fields)

    agent = _Gated()
    agent.gate.set()
    gateway = await _gateway(paths, agent, activity)
    tasks = TaskService(
        config=gateway.config,
        store=_Probing(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        summary_factory=fake_summary_factory(),
        activity=activity,
    )
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
        assert _work(activity) == {"run": 1}  # the job owns the hold before its first step
        await asyncio.wait_for(tasks._jobs_done(), 10)
        assert activity.holds() == []
    finally:
        await tasks.close()
        await gateway.close()


async def test_a_fire_on_a_paused_task_opens_nothing(paths, tmp_path):
    activity = Activity()
    tasks = TaskService(
        config=Config.for_paths(paths),
        store=TaskStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        activity=activity,
    )
    await tasks.start(scheduler=False)
    task = await tasks.create_task(
        name="A", prompt="p", schedule={"kind": "cron", "cron": "@daily"}
    )
    await tasks.update_task(task["id"], paused=True)
    await tasks._fire(task["id"])
    assert activity.holds() == []
    assert await tasks.start_run("no-such-task") is None and activity.holds() == []


def test_an_unknown_schedule_is_a_scheduled_hold_without_a_count():
    a = Activity()
    a.set_scheduled("p1", 2, "2026-11-08T09:00:00Z")
    a.set_scheduled_unknown("p2")
    assert a.holds() == [{"kind": "scheduled"}]  # p2 may hold an earlier slot: no claim
    a.set_scheduled("p1", 0, None)
    assert a.holds() == [{"kind": "scheduled"}]
    a.set_scheduled("p2", 0, None)
    assert a.holds() == []


async def test_a_failed_schedule_read_fails_closed_and_recovers_on_its_own(paths, tmp_path):
    from ag2.knowledge import SqliteKnowledgeStore

    from assistant.storage import SerialStore

    activity = Activity()
    failures = {"left": 0}

    class _FlakyBacking(SerialStore):
        async def read(self, path):  # one record's read fails, the rest are fine
            if failures["left"]:
                failures["left"] -= 1
                raise OSError("disk hiccup")
            return await super().read(path)

    tasks = TaskService(
        config=Config.for_paths(paths),
        store=TaskStore(store=_FlakyBacking(SqliteKnowledgeStore(str(tmp_path / "tasks.db")))),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        activity=activity,
        refresh_retry_s=0.05,
    )
    await tasks.start(scheduler=False)
    failures["left"] = 2
    await tasks.create_task(name="A", prompt="p", schedule={"kind": "cron", "cron": "@daily"})
    assert activity.holds() == [{"kind": "scheduled"}]  # unknown, and sleep-blocking
    await asyncio.sleep(0.3)  # no poll: the retry alone recovers
    (hold,) = activity.holds()
    assert hold["count"] == 1 and "next" in hold
    await tasks.close()
    assert activity.holds() == [hold]  # still armed on disk: the next wake survives a stop


async def test_a_profile_that_fails_to_boot_keeps_an_unknown_scheduled_hold(paths):
    from assistant.profiles import ProfileRegistry
    from tests.support.apps import make_manager
    from tests.support.fakes import failing_agent_factory

    registry = ProfileRegistry(paths)
    healthy = registry.create_profile("Healthy", "#109e91").id
    broken = registry.create_profile("Broken", "#ffa000").id
    manager = make_manager(paths, agent_factory=failing_agent_factory({broken}))
    await manager.start()
    try:
        assert broken in manager.boot_errors
        assert manager.activity.holds() == [{"kind": "scheduled"}]  # never read: no claim
        await manager.archive(broken)
        assert manager.activity.holds() == []
        assert manager.get(healthy).require_tasks() is not None
    finally:
        await manager.close()


async def test_the_file_holds_startup_work_until_every_schedule_is_read(paths, tmp_path):
    from assistant.profiles import ProfileRegistry
    from tests.support.apps import make_manager

    path = tmp_path / "activity.json"
    ProfileRegistry(paths).create_profile("Work", "#109e91")
    manager = make_manager(paths, env={"AG2ASSISTANT_ACTIVITY_FILE": str(path)})
    written = json.loads(path.read_text())
    assert written["holds"] == [{"kind": "work", "count": 1, "detail": "startup"}]
    assert written["idle_since"] is None
    await manager.start()
    try:
        await asyncio.sleep(3)  # the idle grace
        assert json.loads(path.read_text())["holds"] == []
    finally:
        await manager.close()


async def test_a_null_task_record_keeps_the_schedule_unknown(paths, tmp_path):
    from ag2.knowledge import SqliteKnowledgeStore

    from assistant.storage import SerialStore

    backing = SerialStore(SqliteKnowledgeStore(str(tmp_path / "tasks.db")))
    activity = Activity()
    tasks = TaskService(
        config=Config.for_paths(paths),
        store=TaskStore(store=backing),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        activity=activity,
        refresh_retry_s=0.05,
    )
    await tasks.start(scheduler=False)
    await backing.write("/tasks/task-corrupt.json", "null")
    await tasks.create_task(name="A", prompt="p", schedule={"kind": "cron", "cron": "@daily"})
    assert activity.holds() == [{"kind": "scheduled"}]  # not "one task": unknown
    await backing.delete("/tasks/task-corrupt.json")
    await asyncio.sleep(0.3)
    (hold,) = activity.holds()
    assert hold["count"] == 1
    await tasks.close()


async def test_restoring_a_profile_is_work_with_an_unknown_schedule_until_it_is_read(paths):
    from assistant.profiles import ProfileRegistry
    from tests.support.apps import make_manager
    from tests.support.fakes import FakeAgent

    registry = ProfileRegistry(paths)
    work = registry.create_profile("Work", "#109e91").id
    personal = registry.create_profile("Personal", "#f95339").id
    seen: list = []

    def agent(config, **kwargs):  # built during boot, before the schedule is read
        seen.append((config.data_dir.name, manager.activity.holds()))
        return FakeAgent()

    manager = make_manager(paths, agent_factory=agent)
    await manager.start()
    try:
        await (
            manager.get(work)
            .require_tasks()
            .create_task(name="A", prompt="p", schedule={"kind": "cron", "cron": "@daily"})
        )
        await manager.archive(work, new_default=personal)
        assert manager.activity.holds() == []
        seen.clear()
        await manager.restore(work)
        assert seen == [
            (work, [{"kind": "work", "count": 1, "detail": "boot"}, {"kind": "scheduled"}])
        ]
        (hold,) = manager.activity.holds()
        assert hold["kind"] == "scheduled" and hold["count"] == 1
    finally:
        await manager.close()


async def test_a_restore_that_fails_leaves_no_hold_behind(paths):
    from assistant.profiles import ProfileRegistry
    from tests.support.apps import make_manager
    from tests.support.fakes import failing_agent_factory

    registry = ProfileRegistry(paths)
    work = registry.create_profile("Work", "#109e91").id
    personal = registry.create_profile("Personal", "#f95339").id
    registry.archive_profile(work)
    manager = make_manager(paths, agent_factory=failing_agent_factory({work}))
    await manager.start()
    try:
        with pytest.raises(RuntimeError):
            await manager.restore(work)
        assert manager.activity.holds() == []
        assert manager.get(personal).require_gateway() is not None
    finally:
        await manager.close()


async def test_naming_a_new_task_is_work_until_it_is_saved(paths, tmp_path):
    from tests.support.fakes import FakeStructuredAgent, _canned

    gate = asyncio.Event()
    asked = asyncio.Event()

    class _GatedNamer(FakeStructuredAgent):
        async def ask(self, prompt, response_schema=None):
            asked.set()
            await gate.wait()
            return await super().ask(prompt, response_schema)

    activity = Activity()
    tasks = TaskService(
        config=Config.for_paths(paths),
        store=TaskStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        summary_factory=lambda config: _GatedNamer(_canned(name="Named", description="")),
        activity=activity,
    )
    await tasks.start(scheduler=False)
    creating = asyncio.ensure_future(tasks.create_task(name="", prompt="p"))
    await asyncio.wait_for(asked.wait(), 5)
    assert _work(activity) == {"task_create": 1}
    gate.set()
    assert (await asyncio.wait_for(creating, 5))["name"] == "Named"
    assert _work(activity) == {}
    await tasks.close()


async def test_arming_a_task_is_work_until_the_schedule_is_published(paths, tmp_path):
    gate = asyncio.Event()
    entered = asyncio.Event()

    class _GatedStore(TaskStore):
        async def update_task(self, task_id, **fields):
            entered.set()
            await gate.wait()
            return await super().update_task(task_id, **fields)

    activity = Activity()
    tasks = TaskService(
        config=Config.for_paths(paths),
        store=_GatedStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        activity=activity,
    )
    await tasks.start(scheduler=False)
    task = await tasks.create_task(name="A", prompt="p")  # manual: nothing scheduled
    assert activity.holds() == []
    arming = asyncio.ensure_future(
        tasks.update_task(task["id"], schedule={"kind": "cron", "cron": "@daily"})
    )
    await asyncio.wait_for(entered.wait(), 5)
    assert _work(activity) == {"task_update": 1}
    gate.set()
    await asyncio.wait_for(arming, 5)
    assert _work(activity) == {} and activity.holds()[0]["count"] == 1
    await tasks.close()


async def test_an_orderly_stop_inside_the_grace_writes_the_settled_file(paths, tmp_path):
    from assistant.profiles import ProfileRegistry
    from tests.support.apps import make_manager

    path = tmp_path / "activity.json"
    pid = ProfileRegistry(paths).create_profile("Work", "#109e91").id
    manager = make_manager(paths, env={"AG2ASSISTANT_ACTIVITY_FILE": str(path)})
    await manager.start()
    await (
        manager.get(pid)
        .require_tasks()
        .create_task(name="A", prompt="p", schedule={"kind": "cron", "cron": "@daily"})
    )
    await manager.get(pid).require_gateway().send_message("hi", chat_id="c1")
    await manager.close()  # well inside the 2.5 s grace
    (hold,) = json.loads(path.read_text())["holds"]
    assert hold["kind"] == "scheduled" and hold["count"] == 1  # no work left, next wake kept


async def test_closing_the_task_service_settles_queued_and_running_holds(paths, tmp_path):
    activity = Activity()
    agent = _Gated()
    gateway = await _gateway(paths, agent, activity)
    tasks = TaskService(
        config=gateway.config,
        store=TaskStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        max_concurrent=1,
        summary_factory=fake_summary_factory(),
        activity=activity,
    )
    tasks.set_gateway(gateway)
    await tasks.start(scheduler=False)
    task = await tasks.create_task(name="nightly", prompt="report")
    await tasks.start_run(task["id"])
    await tasks.start_run(task["id"])  # queued behind the first
    await asyncio.wait_for(agent.started.wait(), 5)
    assert _work(activity) == {"run": 2, "turn": 1}
    await tasks.close()  # the gate never opens: both jobs are cancelled and awaited
    assert _work(activity) == {}
    await gateway.close()


async def test_a_refresh_that_lands_after_a_close_does_not_resurrect_the_hold(paths, tmp_path):
    gate = asyncio.Event()

    class _GatedStore(TaskStore):
        async def list_tasks(self, **kwargs):
            await gate.wait()
            return await super().list_tasks(**kwargs)

    activity = Activity()
    tasks = TaskService(
        config=Config.for_paths(paths),
        store=_GatedStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        activity=activity,
    )
    gate.set()
    await tasks.start(scheduler=False)
    await tasks.create_task(name="A", prompt="p", schedule={"kind": "cron", "cron": "@daily"})
    gate.clear()
    pending = asyncio.ensure_future(tasks.refresh_scheduled())  # a poll's refresh, parked
    await asyncio.sleep(0.01)
    await tasks.close()
    activity.set_scheduled(tasks._config.data_dir.name, 0, None)  # what archive does next
    gate.set()
    await pending
    assert activity.holds() == []


@pytest.mark.parametrize("change", ["create", "update"])
async def test_a_cancelled_schedule_change_leaves_an_unknown_hold_that_republishes(
    paths, tmp_path, change
):
    """A turn that saves a scheduled task and is stopped before the publish: the task is
    on disk, so the hold stays (unknown) and the retry publishes it with no poll."""
    saved = asyncio.Event()
    gate = asyncio.Event()

    class _GatedStore(TaskStore):
        async def create_task(self, **fields):
            task = await super().create_task(**fields)
            saved.set()
            await gate.wait()  # cancelled here: after persistence, before publication
            return task

        async def update_task(self, task_id, **fields):
            task = await super().update_task(task_id, **fields)
            saved.set()
            await gate.wait()
            return task

    activity = Activity()
    tasks = TaskService(
        config=Config.for_paths(paths),
        store=_GatedStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        activity=activity,
        refresh_retry_s=0.05,
    )
    await tasks.start(scheduler=False)
    daily = {"kind": "cron", "cron": "@daily"}
    if change == "create":
        changing = asyncio.ensure_future(tasks.create_task(name="A", prompt="p", schedule=daily))
    else:
        gate.set()
        task = await tasks.create_task(name="A", prompt="p")
        gate.clear()
        saved.clear()
        changing = asyncio.ensure_future(tasks.update_task(task["id"], schedule=daily))
    await asyncio.wait_for(saved.wait(), 5)
    changing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await changing
    assert activity.holds() == [{"kind": "scheduled"}]  # on disk, not yet published
    await asyncio.sleep(0.3)
    (hold,) = activity.holds()
    assert hold["count"] == 1
    await tasks.close()


async def test_a_task_saved_by_a_turn_cancelled_at_shutdown_is_still_published(paths, tmp_path):
    """Runtime close order: the task service closes, then the gateway cancels turns, then
    the service's final read publishes what those turns left on disk."""
    saved = asyncio.Event()
    gate = asyncio.Event()

    class _GatedStore(TaskStore):
        async def create_task(self, **fields):
            task = await super().create_task(**fields)
            saved.set()
            await gate.wait()
            return task

    activity = Activity()
    tasks = TaskService(
        config=Config.for_paths(paths),
        store=_GatedStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        activity=activity,
    )
    await tasks.start(scheduler=False)
    creating = asyncio.ensure_future(
        tasks.create_task(name="A", prompt="p", schedule={"kind": "cron", "cron": "@daily"})
    )
    await asyncio.wait_for(saved.wait(), 5)
    await tasks.close()  # 1. the service freezes
    creating.cancel()  # 2. the gateway cancels the turn that was saving
    with pytest.raises(asyncio.CancelledError):
        await creating
    assert activity.holds() == [{"kind": "scheduled"}]  # unknown rather than idle
    await tasks.refresh_scheduled(final=True)  # 3. the runtime's last word
    (hold,) = activity.holds()
    assert hold["count"] == 1 and "next" in hold


async def test_an_acp_request_is_work_until_its_answer_is_delivered(paths):
    import acp
    from ag2.acp.testing import RecordingClient

    from assistant.profiles import ProfileRegistry
    from tests.support.apps import make_acp_agent

    delivering = asyncio.Event()
    gate = asyncio.Event()

    class _SlowClient(RecordingClient):
        async def session_update(self, *, session_id, update, **kwargs):
            await super().session_update(session_id=session_id, update=update, **kwargs)
            delivering.set()
            await gate.wait()  # the client is slow to take the answer

    activity = Activity()
    pid = ProfileRegistry(paths).create_profile("ACP", "#109e91").id
    served, storage, _ = make_acp_agent(paths, pid, activity=activity)
    client = _SlowClient()
    scope = served.bind(client)
    await scope.initialize(protocol_version=1)
    session = await scope.new_session(cwd=".", mcp_servers=[])
    turn = asyncio.ensure_future(scope.prompt(session.session_id, [acp.text_block("hi")]))
    await asyncio.wait_for(delivering.wait(), 5)
    assert _work(activity) == {"acp_prompt": 1}  # the agent is done; delivery is not
    gate.set()
    await asyncio.wait_for(turn, 5)
    assert activity.holds() == []
    await scope.aclose()


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
    await asyncio.sleep(0)  # the drain task's done callback
    assert activity.holds() == []
    await out.aclose()


def test_learning_from_feedback_is_work_until_the_preference_is_saved(paths):
    import threading

    from tests.support.apps import api
    from tests.support.chat import wait_for

    started = threading.Event()
    gate = asyncio.Event()

    async def learn(config, **kwargs):
        started.set()
        await gate.wait()

    app, pid = make_profile_app(paths, feedback_learn=learn)
    with TestClient(app) as client:
        activity = app.state.profiles.activity
        body = {"target_id": "m1", "sentiment": "down", "reason": "too long"}
        assert client.post(api(pid, "/chats/c1/feedback"), json=body).json() == {"ok": True}
        assert started.wait(5)
        assert _work(activity) == {"background": 1}
        client.portal.call(gate.set)  # the Event lives on the app's loop
        wait_for(lambda: activity.holds() == [])


async def test_an_older_schedule_read_cannot_overwrite_a_newer_hold(paths, tmp_path):
    """A poll's scan starts before a task is armed and finishes after: the armed
    task's own publication is the last word, not the stale scan."""
    gates: list = []

    class _GatedStore(TaskStore):
        async def list_tasks(self, **kwargs):
            tasks = await super().list_tasks(**kwargs)
            if gates:
                await gates.pop(0).wait()  # the first scan is slow to finish
            return tasks

    activity = Activity()
    tasks = TaskService(
        config=Config.for_paths(paths),
        store=_GatedStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        activity=activity,
    )
    await tasks.start(scheduler=False)
    slow = asyncio.Event()
    gates.append(slow)
    stale_poll = asyncio.ensure_future(tasks.refresh_scheduled())  # sees no tasks
    await asyncio.sleep(0.01)
    arming = asyncio.ensure_future(
        tasks.create_task(name="A", prompt="p", schedule={"kind": "cron", "cron": "@daily"})
    )
    await asyncio.sleep(0.01)
    slow.set()
    await asyncio.gather(stale_poll, arming)
    (hold,) = activity.holds()
    assert hold["kind"] == "scheduled" and hold["count"] == 1
    await tasks.close()


async def test_a_stale_scan_cannot_clear_a_cancelled_mutation_s_safeguard(paths, tmp_path):
    """A poll reads the schedule, then a task is armed and its publish is cancelled
    while the poll still holds the lock: the poll's stale result is rejected, the
    unknown hold stays, and the retry publishes the armed task."""
    gates: list = []

    class _GatedStore(TaskStore):
        async def list_tasks(self, **kwargs):
            tasks = await super().list_tasks(**kwargs)  # read first ...
            if gates:
                await gates.pop(0).wait()  # ... then stall before publishing
            return tasks

    activity = Activity()
    tasks = TaskService(
        config=Config.for_paths(paths),
        store=_GatedStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        activity=activity,
        refresh_retry_s=0.05,
    )
    await tasks.start(scheduler=False)
    task = await tasks.create_task(name="A", prompt="p")  # manual
    stall = asyncio.Event()
    gates.append(stall)
    stale_poll = asyncio.ensure_future(tasks.refresh_scheduled())
    await asyncio.sleep(0.01)  # it has read "nothing scheduled" and holds the lock
    arming = asyncio.ensure_future(
        tasks.update_task(task["id"], schedule={"kind": "cron", "cron": "@daily"})
    )
    await asyncio.sleep(0.01)  # persisted; now waiting for the lock to publish
    arming.cancel()
    with pytest.raises(asyncio.CancelledError):
        await arming
    assert activity.holds() == [{"kind": "scheduled"}]
    stall.set()
    await stale_poll
    assert activity.holds() == [{"kind": "scheduled"}]  # the stale result was rejected
    await asyncio.sleep(0.3)
    (hold,) = activity.holds()
    assert hold["count"] == 1
    await tasks.close()

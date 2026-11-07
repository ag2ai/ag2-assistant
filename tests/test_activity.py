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
        assert json.loads(path.read_text())["holds"] == []
        await manager.get(pid).require_gateway().send_message("hi", chat_id="c1")
        assert json.loads(path.read_text())["holds"] != []  # the turn (and its title job)
        await _settled(manager.activity)
        await asyncio.sleep(3)  # the idle grace
        assert json.loads(path.read_text())["holds"] == []
    finally:
        await manager.close()
    assert make_manager(paths, env={}).activity.snapshot()["holds"] == []


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

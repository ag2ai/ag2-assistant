"""TaskStore v2: task docs + run docs, next_run_at derivation, run summaries."""

from datetime import datetime

from assistant.tasks.model import RunStatus, manual_schedule
from assistant.tasks.scheduling import compute_next_run, schedule_text
from assistant.tasks.store import TaskStore


def _store(tmp_path) -> TaskStore:
    return TaskStore(path=tmp_path / "tasks.db")


async def test_task_crud_and_listing(tmp_path):
    st = _store(tmp_path)
    a = await st.create_task("A", "prompt a")
    b = await st.create_task("B", "prompt b", model="cfg_1")
    assert a.id.startswith("task-") and a.schedule == manual_schedule()
    got = await st.get_task(b.id)
    assert got.model == "cfg_1" and got.created_at
    names = [t.name for t in await st.list_tasks()]
    assert names == ["B", "A"]  # newest first
    await st.delete_task(a.id)
    assert await st.get_task(a.id) is None


async def test_update_task_recomputes_next_run(tmp_path):
    st = _store(tmp_path)
    t = await st.create_task("T", "p", schedule={"kind": "cron", "at": None, "cron": "0 9 * * *"})
    assert t.next_run_at is not None  # armed on create
    paused = await st.update_task(t.id, paused=True)
    assert paused.next_run_at is None  # paused disarms
    resumed = await st.update_task(t.id, paused=False)
    assert resumed.next_run_at is not None
    # explicit next_run_at wins over recompute (the scheduler's re-arm path)
    pinned = await st.update_task(t.id, next_run_at="2099-01-01T00:00:00+00:00")
    assert pinned.next_run_at == "2099-01-01T00:00:00+00:00"
    # protected fields are ignored
    same = await st.update_task(t.id, id="task_hack", created_at="1970-01-01")
    assert same.id == t.id and same.created_at == t.created_at


async def test_runs_lifecycle_and_summaries(tmp_path):
    st = _store(tmp_path)
    t = await st.create_task("T", "p")
    r1 = await st.create_run(t.id, trigger="manual")
    assert r1.id.startswith("run-") and r1.status == RunStatus.RUNNING and r1.started_at
    await st.set_run_status(r1.id, RunStatus.COMPLETED, summary="did the thing")
    done = await st.get_run(r1.id)
    assert done.ended_at is not None and done.summary == "did the thing"
    # terminal is sticky: a late transition must not overwrite it
    await st.set_run_status(r1.id, RunStatus.FAILED, error="late")
    assert (await st.get_run(r1.id)).status == RunStatus.COMPLETED
    r2 = await st.create_run(t.id, trigger="schedule")
    await st.set_run_status(r2.id, RunStatus.COMPLETED, summary="second")
    r3 = await st.create_run(t.id, trigger="schedule")  # current, excluded via before=
    assert [r.id for r in await st.list_runs(t.id)] == [r3.id, r2.id, r1.id]
    assert await st.last_summaries(t.id, n=2, before=r3.id) == ["did the thing", "second"]
    await st.delete_run(r3.id)
    assert await st.get_run(r3.id) is None


def test_compute_next_run_and_schedule_text():
    now = datetime.fromisoformat("2026-07-18T10:00:00+03:00")
    assert compute_next_run(manual_schedule(), now) is None
    once = {"kind": "once", "at": "2026-08-01T09:00:00+03:00", "cron": None}
    assert compute_next_run(once, now) == "2026-08-01T09:00:00+03:00"
    assert compute_next_run(once, now, after_fire=True) is None
    cron = {"kind": "cron", "at": None, "cron": "0 9 * * *"}
    nxt = compute_next_run(cron, now)
    assert nxt is not None and nxt > now.isoformat()
    assert schedule_text(manual_schedule()) == "manual"
    assert schedule_text(once).startswith("once at ")
    assert "09:00" in schedule_text(cron)

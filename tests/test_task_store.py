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
    # oldest-first, so a prompt reads them chronologically
    recent = await st.recent_runs(t.id, n=2, before=r3.id)
    assert [r.summary for r in recent] == ["did the thing", "second"]
    await st.delete_run(r3.id)
    assert await st.get_run(r3.id) is None


async def test_recent_runs_depth_and_settled_only(tmp_path):
    """0 none · -1 all · n last-n, and a failed run is kept (it may have committed
    work before settling, ADR 0027) while a still-running one is not."""
    st = _store(tmp_path)
    t = await st.create_task("T", "p")
    ok1 = await st.create_run(t.id)
    await st.set_run_status(ok1.id, RunStatus.COMPLETED, summary="first")
    bad = await st.create_run(t.id)
    await st.set_run_status(bad.id, RunStatus.FAILED, error="boom")
    ok2 = await st.create_run(t.id)
    await st.set_run_status(ok2.id, RunStatus.COMPLETED, summary="third")
    live = await st.create_run(t.id)  # still RUNNING

    assert await st.recent_runs(t.id, n=0) == []
    assert [r.id for r in await st.recent_runs(t.id, n=-1)] == [ok1.id, bad.id, ok2.id]
    assert [r.id for r in await st.recent_runs(t.id, n=2)] == [bad.id, ok2.id]
    assert live.id not in [r.id for r in await st.recent_runs(t.id, n=-1)]
    # n beyond the run count is not an error
    assert len(await st.recent_runs(t.id, n=99)) == 3


async def test_backfill_recall_stamps_cron_tasks_once(tmp_path):
    """Records written before the field existed inherit the look-back they had;
    re-running never overwrites a value the user has since chosen."""
    import json

    from assistant.tasks.store import _TASKS

    st = _store(tmp_path)
    cron = await st.create_task(
        "C", "p", schedule={"kind": "cron", "at": None, "cron": "0 9 * * *"}
    )
    manual = await st.create_task("M", "p")
    for tid in (cron.id, manual.id):  # strip the key, as a pre-0027 record has it
        path = f"{_TASKS}{tid}.json"
        data = json.loads(await st._store.read(path))
        data.pop("recall_depth")
        await st._store.write(path, json.dumps(data))

    assert await st.backfill_recall(3) == 2
    assert (await st.get_task(cron.id)).recall_depth == 3
    assert (await st.get_task(manual.id)).recall_depth == 0

    await st.update_task(cron.id, recall_depth=0)  # the user turns it off
    assert await st.backfill_recall(3) == 0  # idempotent: no second stamp
    assert (await st.get_task(cron.id)).recall_depth == 0


def test_strip_workdirs_pops_legacy_fields_and_reports_them(tmp_path):
    import asyncio
    import json

    from assistant.tasks.store import _TASKS

    async def _run():
        store = TaskStore(path=tmp_path / "tasks.db")
        t1 = await store.create_task(name="A", prompt="p")
        t2 = await store.create_task(name="B", prompt="p")
        # legacy records: workdir lives only in the raw JSON (Task has no such field)
        for tid, wd in ((t1.id, "/data/media"), (t2.id, None)):
            raw = json.loads(await store._store.read(f"{_TASKS}{tid}.json"))
            if wd:
                raw["workdir"] = wd
                raw["workdir_access"] = "read_write"
            await store._store.write(f"{_TASKS}{tid}.json", json.dumps(raw))
        moved = await store.strip_workdirs()
        assert moved == [(t1.id, "/data/media", "read_write")]
        raw = json.loads(await store._store.read(f"{_TASKS}{t1.id}.json"))
        assert "workdir" not in raw and "workdir_access" not in raw
        assert await store.strip_workdirs() == []  # idempotent

    asyncio.run(_run())


async def test_rekey_origin_channels_moves_a_platform_origin_onto_its_connection(tmp_path):
    """A task queued before Connections existed points at "telegram"; the outcome is
    delivered through a Connection id, so the origin has to move with the rekeying."""
    st = _store(tmp_path)
    legacy = await st.create_task("A", "p", origin_channel="telegram", origin_chat="7")
    already = await st.create_task("B", "p", origin_channel="cn_other", origin_chat="8")
    homeless = await st.create_task("C", "p", origin_channel="slack", origin_chat="9")

    assert await st.rekey_origin_channels({"telegram": "cn_tg"}) == 1
    assert (await st.get_task(legacy.id)).origin_channel == "cn_tg"
    assert (await st.get_task(already.id)).origin_channel == "cn_other"
    assert (await st.get_task(homeless.id)).origin_channel == "slack"
    assert await st.rekey_origin_channels({"telegram": "cn_tg"}) == 0  # idempotent


def test_strip_workdirs_skips_id_less_record_from_moved(tmp_path):
    """A record missing its id (corrupt/legacy) must still be stripped on disk, but
    must NOT be reported in `moved` — an empty task_id would otherwise mint a
    PROFILE-scope grant downstream (privilege widening), not a task-scope one."""
    import asyncio
    import json

    from assistant.tasks.store import _TASKS

    async def _run():
        store = TaskStore(path=tmp_path / "tasks.db")
        t = await store.create_task(name="A", prompt="p")
        raw = json.loads(await store._store.read(f"{_TASKS}{t.id}.json"))
        raw["workdir"] = "/data/media"
        raw["workdir_access"] = "read_write"
        del raw["id"]
        await store._store.write(f"{_TASKS}{t.id}.json", json.dumps(raw))

        moved = await store.strip_workdirs()
        assert moved == []  # id-less → not reported, no grant should be minted

        raw_after = json.loads(await store._store.read(f"{_TASKS}{t.id}.json"))
        assert "workdir" not in raw_after and "workdir_access" not in raw_after

    asyncio.run(_run())


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

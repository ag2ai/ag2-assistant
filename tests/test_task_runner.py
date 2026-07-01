"""Tests for the background task runner — gating, cancel/cascade, concurrency."""

import asyncio

from assistant.tasks import DeliverableStatus, TaskManager, TaskStatus, TaskStore


def _store(tmp_path):
    return TaskStore(path=tmp_path / "tasks.db")


async def test_completes_when_deliverable_produced(tmp_path):
    store = _store(tmp_path)
    t = await store.create("write note")
    d = await store.add_deliverable(t.id, "note.md")

    async def executor(task_id, mgr, asker):
        await mgr.progress(task_id, "writing", pct=50)
        await store.set_deliverable_status(task_id, d["id"], DeliverableStatus.PRODUCED)

    mgr = TaskManager(store, executor)
    await mgr.submit(t.id)
    await mgr.wait(t.id)
    got = await store.get(t.id)
    assert got.status == TaskStatus.COMPLETED
    assert got.progress and got.progress[0]["message"] == "writing"


async def test_fails_when_deliverable_unmet(tmp_path):
    store = _store(tmp_path)
    t = await store.create("write note")
    await store.add_deliverable(t.id, "note.md")  # never produced

    async def executor(task_id, mgr, asker):
        return  # does nothing → deliverable stays pending

    mgr = TaskManager(store, executor)
    await mgr.submit(t.id)
    await mgr.wait(t.id)
    got = await store.get(t.id)
    assert got.status == TaskStatus.FAILED
    assert "deliverables not met" in got.error


async def test_executor_exception_fails_task(tmp_path):
    store = _store(tmp_path)
    t = await store.create("x")

    async def executor(task_id, mgr, asker):
        raise RuntimeError("boom")

    mgr = TaskManager(store, executor)
    await mgr.submit(t.id)
    await mgr.wait(t.id)
    got = await store.get(t.id)
    assert got.status == TaskStatus.FAILED and "boom" in got.error


async def test_manager_forwards_raw_ag2_events(tmp_path):
    from ag2.events import TaskStarted

    store = _store(tmp_path)
    seen = []

    async def executor(task_id, mgr, asker):
        return

    async def on_event(task_id, event):
        seen.append((task_id, event))

    mgr = TaskManager(store, executor, on_event=on_event)
    event = TaskStarted(task_id="sub-1", agent_name="researcher", objective="find sources")
    await mgr.emit_event("task-1", event)

    assert seen == [("task-1", event)]


async def test_parent_completes_despite_failed_subtask(tmp_path):
    """Resilience: a subtask that fails does NOT abort the parent — the parent
    still does its own work and completes if its own deliverable is met."""
    store = _store(tmp_path)
    root = await store.create("trip prep")
    await store.add_deliverable(root.id, "travel guide")
    good = await store.add_subtask(root.id, "weather research", reopen_parent=False)
    await store.add_deliverable(good.id, "weather notes")
    bad = await store.add_subtask(root.id, "retrieve bookings", reopen_parent=False)
    await store.add_deliverable(bad.id, "booking details")  # never produced → fails

    async def executor(task_id, mgr, asker):
        t = await store.get(task_id)
        if task_id == bad.id:
            return  # finds nothing → its deliverable stays unmet → subtask fails
        for d in t.pending_deliverables():
            await store.set_deliverable_status(task_id, d["id"], DeliverableStatus.PRODUCED)

    mgr = TaskManager(store, executor)
    await mgr.submit(root.id)
    await mgr.wait(root.id)

    assert (await store.get(bad.id)).status == TaskStatus.FAILED
    assert (await store.get(good.id)).status == TaskStatus.COMPLETED
    # parent still completes: its own deliverable was produced
    assert (await store.get(root.id)).status == TaskStatus.COMPLETED


async def test_orchestrator_with_no_own_deliverable_fails_if_subtask_fails(tmp_path):
    """A pure orchestrator (no deliverables of its own) has nothing else to be
    judged by, so it fails if a subtask fails."""
    store = _store(tmp_path)
    root = await store.create("orchestrate")  # no own deliverable
    child = await store.add_subtask(root.id, "the only work", reopen_parent=False)
    await store.add_deliverable(child.id, "output")  # never produced → fails

    async def executor(task_id, mgr, asker):
        return  # nobody produces anything

    mgr = TaskManager(store, executor)
    await mgr.submit(root.id)
    await mgr.wait(root.id)

    assert (await store.get(child.id)).status == TaskStatus.FAILED
    assert (await store.get(root.id)).status == TaskStatus.FAILED


async def test_cancel_stops_running_task_immediately(tmp_path):
    store = _store(tmp_path)
    t = await store.create("long job")
    started = asyncio.Event()

    async def executor(task_id, mgr, asker):
        started.set()
        await asyncio.Event().wait()  # never finishes unless cancelled

    mgr = TaskManager(store, executor)
    await mgr.submit(t.id)
    await started.wait()
    await mgr.cancel(t.id, reason="user stop")
    await mgr.wait(t.id)
    got = await store.get(t.id)
    assert got.status == TaskStatus.CANCELLED
    assert got.error == "user stop"


async def test_cancel_cascades_to_subtasks(tmp_path):
    store = _store(tmp_path)
    root = await store.create("root")
    a = await store.create("a", parent_id=root.id)
    b = await store.create("b", parent_id=a.id)  # grandchild
    await store.set_status(a.id, TaskStatus.RUNNING)
    await store.set_status(b.id, TaskStatus.RUNNING)

    async def executor(task_id, mgr, asker):
        await asyncio.Event().wait()

    mgr = TaskManager(store, executor)
    await mgr.submit(root.id)
    await asyncio.sleep(0.02)
    await mgr.cancel(root.id)
    await mgr.wait(root.id)
    for tid in (root.id, a.id, b.id):
        assert (await store.get(tid)).status == TaskStatus.CANCELLED


async def test_concurrency_cap(tmp_path):
    store = _store(tmp_path)
    live = 0
    peak = 0
    release = asyncio.Event()
    at_cap = asyncio.Event()

    async def executor(task_id, mgr, asker):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        if live >= 2:
            at_cap.set()
        await release.wait()
        live -= 1

    mgr = TaskManager(store, executor, max_concurrent=2)
    ids = [(await store.create(f"t{i}")).id for i in range(4)]
    for tid in ids:
        await mgr.submit(tid)
    # exactly the cap runs concurrently: 2 reach the executor, the rest wait
    await asyncio.wait_for(at_cap.wait(), timeout=5)
    await asyncio.sleep(0.05)  # give any (incorrectly) extra ones a chance to slip in
    assert peak == 2
    release.set()
    for tid in ids:
        await mgr.wait(tid)
    assert peak == 2  # never exceeded the cap


async def test_progress_callback_fires(tmp_path):
    store = _store(tmp_path)
    t = await store.create("x")
    seen = []

    async def on_progress(task_id, message, pct):
        seen.append((task_id, message, pct))

    async def executor(task_id, mgr, asker):
        await mgr.progress(task_id, "halfway", pct=50)

    mgr = TaskManager(store, executor, on_progress=on_progress)
    await mgr.submit(t.id)
    await mgr.wait(t.id)
    assert seen == [(t.id, "halfway", 50)]

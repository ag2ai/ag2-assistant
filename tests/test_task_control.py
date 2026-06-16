"""Task-control tools — modify one task from a conversation (add/cancel/edit)."""

import asyncio

from agclaw.tasks import TaskManager, TaskStatus, TaskStore
from agclaw.tasks.control import (
    build_task_tools,
    do_add_deliverable,
    do_add_subtask,
    do_cancel,
    do_reschedule,
    do_set_objective,
    render_task,
)


def _store(tmp_path):
    return TaskStore(path=tmp_path / "tasks.db")


async def _noop_executor(task_id, mgr, asker):
    pass


async def test_render_task_snapshot(tmp_path):
    store = _store(tmp_path)
    t = await store.create("Trip", objective="plan a trip")
    await store.add_deliverable(t.id, "itinerary")
    await store.add_subtask(t.id, "book flights", reopen_parent=False)
    text = await render_task(store, t.id)
    assert "Trip" in text and "plan a trip" in text
    assert "itinerary" in text and "book flights" in text


async def test_render_task_includes_schedule(tmp_path):
    store = _store(tmp_path)
    t = await store.create("digest", status=TaskStatus.SCHEDULED,
                           scheduled_for="2026-06-18T08:00:00", recurrence="daily")
    text = await render_task(store, t.id)
    assert "2026-06-18T08:00:00" in text and "repeats daily" in text
    # a one-off shows it's not recurring
    t2 = await store.create("once", status=TaskStatus.SCHEDULED,
                            scheduled_for="2026-06-18T08:00:00")
    assert "one-off" in await render_task(store, t2.id)


async def test_add_subtask_creates_child_with_deliverable(tmp_path):
    store = _store(tmp_path)
    mgr = TaskManager(store, _noop_executor)
    t = await store.create("Research IPOs", objective="briefing")
    msg = await do_add_subtask(store, mgr, t.id, "Research xAI", "look at xAI", "web")
    assert "Research xAI" in msg
    kids = await store.children(t.id)
    assert [c.title for c in kids] == ["Research xAI"]
    assert kids[0].capabilities == ["web"]
    assert kids[0].deliverables  # got its own deliverable


async def test_add_subtask_reopens_completed_task(tmp_path):
    store = _store(tmp_path)
    mgr = TaskManager(store, _noop_executor)
    t = await store.create("done task", status=TaskStatus.COMPLETED)
    await do_add_subtask(store, mgr, t.id, "more work")
    assert (await store.get(t.id)).status != TaskStatus.COMPLETED  # re-opened to run


async def test_set_objective_and_add_deliverable(tmp_path):
    store = _store(tmp_path)
    mgr = TaskManager(store, _noop_executor)
    t = await store.create("x")
    await do_set_objective(store, t.id, "the new objective")
    assert (await store.get(t.id)).objective == "the new objective"
    await do_add_deliverable(store, mgr, t.id, "a chart", "must be a PNG")
    assert any(d["description"] == "a chart" for d in (await store.get(t.id)).deliverables)


async def test_cancel_whole_task_and_subtask(tmp_path):
    store = _store(tmp_path)
    mgr = TaskManager(store, _noop_executor)
    t = await store.create("parent")
    child = await store.add_subtask(t.id, "leg one", reopen_parent=False)

    msg = await do_cancel(store, mgr, t.id, subtask="leg")
    assert "leg one" in msg
    assert (await store.get(child.id)).status == TaskStatus.CANCELLED

    assert "No subtask" in await do_cancel(store, mgr, t.id, subtask="nope")

    await do_cancel(store, mgr, t.id)
    assert (await store.get(t.id)).status == TaskStatus.CANCELLED


async def test_editing_a_scheduled_task_does_not_run_it(tmp_path):
    """Adding a subtask/deliverable to a SCHEDULED task updates its plan but must
    NOT execute it or change its status — it still runs on its schedule (#user)."""
    store = _store(tmp_path)
    submitted = []

    async def executor(task_id, mgr, asker):
        submitted.append(task_id)

    mgr = TaskManager(store, executor)
    t = await store.create("daily digest", status=TaskStatus.SCHEDULED,
                           scheduled_for="2030-01-01T05:00:00", recurrence="weekdays")

    await do_add_subtask(store, mgr, t.id, "Extra research")
    await do_add_deliverable(store, mgr, t.id, "an appendix")
    await mgr.wait(t.id)  # nothing should have been submitted

    got = await store.get(t.id)
    assert got.status == TaskStatus.SCHEDULED          # still scheduled, not running/completed
    assert got.scheduled_for == "2030-01-01T05:00:00"  # schedule intact
    assert submitted == []                             # the executor never ran
    assert [c.title for c in await store.children(t.id)] == ["Extra research"]  # edit applied


async def test_reschedule_changes_time_and_repeat(tmp_path):
    store = _store(tmp_path)
    t = await store.create("digest", status=TaskStatus.SCHEDULED,
                           scheduled_for="2026-01-01T09:00:00", recurrence="daily")
    # change the repeat
    msg = await do_reschedule(store, t.id, recurrence="weekly")
    got = await store.get(t.id)
    assert got.recurrence == "weekly" and got.status == TaskStatus.SCHEDULED
    assert "weekly" in msg
    # change the time, keeping the repeat
    await do_reschedule(store, t.id, when="2026-02-02T08:00:00")
    got = await store.get(t.id)
    assert got.scheduled_for == "2026-02-02T08:00:00" and got.recurrence == "weekly"
    # turn off repeating → one-off
    await do_reschedule(store, t.id, recurrence="off")
    assert (await store.get(t.id)).recurrence is None
    # bad recurrence is rejected, unchanged
    assert "don't understand" in await do_reschedule(store, t.id, recurrence="fortnightly-ish")


async def test_reschedule_needs_a_time(tmp_path):
    store = _store(tmp_path)
    t = await store.create("no time yet")  # no scheduled_for
    assert "give me a time" in (await do_reschedule(store, t.id, recurrence="daily")).lower()


async def test_build_task_tools_exposes_the_set(tmp_path):
    store = _store(tmp_path)
    mgr = TaskManager(store, _noop_executor)
    t = await store.create("x")
    names = {tool.name for tool in build_task_tools(store, mgr, t.id)}
    assert names == {"task_status", "add_subtask", "set_objective", "add_deliverable",
                     "reschedule", "cancel"}

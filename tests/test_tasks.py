"""Tests for the task model and persistent store (Phase 1 foundation)."""

import pytest

from agclaw.tasks import DeliverableStatus, Task, TaskStatus, TaskStore


def _store(tmp_path):
    return TaskStore(path=tmp_path / "tasks.db")


def test_task_roundtrip():
    t = Task(id="t1", title="Do thing", description="d", plan=["a", "b"])
    again = Task.from_dict(t.to_dict())
    assert again == t
    assert again.plan == ["a", "b"]


def test_task_from_dict_tolerates_unknown_keys():
    t = Task.from_dict({"id": "t1", "title": "x", "bogus": 123})
    assert t.id == "t1" and t.title == "x"


def test_is_terminal():
    assert Task(id="t", title="x", status=TaskStatus.COMPLETED).is_terminal
    assert not Task(id="t", title="x", status=TaskStatus.RUNNING).is_terminal


async def test_create_get_persists(tmp_path):
    store = _store(tmp_path)
    t = await store.create("Research IPOs", description="Anthropic & OpenAI")
    assert t.id.startswith("task-")
    assert t.created_at and t.stream_id == f"task:{t.id}"
    # a fresh store over the same db sees it (durable)
    got = await TaskStore(path=tmp_path / "tasks.db").get(t.id)
    assert got is not None and got.title == "Research IPOs"


async def test_children_and_roots(tmp_path):
    store = _store(tmp_path)
    root = await store.create("Big job")
    a = await store.create("Sub A", parent_id=root.id)
    await store.create("Sub B", parent_id=root.id)
    roots = await store.roots()
    assert [r.id for r in roots] == [root.id]
    kids = await store.children(root.id)
    assert {k.title for k in kids} == {"Sub A", "Sub B"}
    assert a.parent_id == root.id


async def test_descendants_are_recursive(tmp_path):
    store = _store(tmp_path)
    root = await store.create("root")
    a = await store.create("a", parent_id=root.id)
    b = await store.create("b", parent_id=a.id)        # grandchild
    c = await store.create("c", parent_id=b.id)        # great-grandchild
    ids = {t.id for t in await store.descendants(root.id)}
    assert ids == {a.id, b.id, c.id}


async def test_tree_shape(tmp_path):
    store = _store(tmp_path)
    root = await store.create("root")
    a = await store.create("a", parent_id=root.id)
    await store.create("a1", parent_id=a.id)
    tree = await store.tree(root.id)
    assert tree["task"]["id"] == root.id
    assert tree["children"][0]["task"]["id"] == a.id
    assert tree["children"][0]["children"][0]["task"]["title"] == "a1"


async def test_set_status_stamps_times(tmp_path):
    store = _store(tmp_path)
    t = await store.create("x")
    running = await store.set_status(t.id, TaskStatus.RUNNING)
    assert running.status == TaskStatus.RUNNING and running.started_at
    done = await store.set_status(t.id, TaskStatus.COMPLETED, result="ok")
    assert done.ended_at and done.result == "ok"


async def test_add_progress(tmp_path):
    store = _store(tmp_path)
    t = await store.create("x")
    await store.add_progress(t.id, "step 1 done", pct=50)
    got = await store.get(t.id)
    assert got.progress[0]["message"] == "step 1 done"
    assert got.progress[0]["pct"] == 50


async def test_delete(tmp_path):
    store = _store(tmp_path)
    t = await store.create("x")
    await store.delete(t.id)
    assert await store.get(t.id) is None


# --- objectives / deliverables / completion ---


def test_deliverable_done_rules():
    t = Task(id="t", title="x", auto_accept=True)
    t.deliverables = [
        {"id": "d1", "status": DeliverableStatus.PRODUCED},
        {"id": "d2", "status": DeliverableStatus.ACCEPTED},
    ]
    assert t.deliverables_satisfied()  # produced counts under auto_accept
    t.auto_accept = False
    assert not t.deliverables_satisfied()  # now d1 (produced) needs acceptance
    assert {d["id"] for d in t.pending_deliverables()} == {"d1"}


def test_no_deliverables_is_vacuously_satisfied():
    assert Task(id="t", title="x").deliverables_satisfied()


async def test_add_and_satisfy_deliverable(tmp_path):
    store = _store(tmp_path)
    t = await store.create("Write report", objective="A 1-page report on X")
    d = await store.add_deliverable(t.id, "report.md", criteria="covers X, ~1 page")
    assert d["status"] == DeliverableStatus.PENDING
    assert not await store.is_complete(t.id)
    await store.set_deliverable_status(
        t.id, d["id"], DeliverableStatus.PRODUCED,
        asset={"name": "report.md", "path": "/tmp/report.md", "kind": "text"},
    )
    assert await store.is_complete(t.id)  # auto_accept → produced is enough
    got = await store.get(t.id)
    assert got.deliverables[0]["asset"]["name"] == "report.md"


async def test_completion_requires_subtasks_done(tmp_path):
    store = _store(tmp_path)
    root = await store.create("Big job")  # no deliverables
    sub = await store.create("Sub", parent_id=root.id)
    assert not await store.is_complete(root.id)  # subtask not complete
    await store.set_status(sub.id, TaskStatus.COMPLETED)
    assert await store.is_complete(root.id)


# --- amendment ("oh and add SpaceX to that IPO research") ---


async def test_update_patches_fields_and_protects_id(tmp_path):
    store = _store(tmp_path)
    t = await store.create("x", objective="old")
    await store.update(t.id, objective="new", note="refined objective", id="HACK")
    got = await store.get(t.id)
    assert got.objective == "new"
    assert got.id == t.id  # id protected
    assert got.progress[-1]["message"] == "refined objective"


async def test_add_subtask_to_running_task_uncompletes_it(tmp_path):
    store = _store(tmp_path)
    ipo = await store.create("IPO research", objective="Anthropic & OpenAI")
    d = await store.add_deliverable(ipo.id, "summary")
    await store.set_deliverable_status(ipo.id, d["id"], "produced")
    assert await store.is_complete(ipo.id)  # done so far

    # "oh and add SpaceX to that IPO research"
    spacex = await store.add_subtask(ipo.id, "Research SpaceX IPO")
    assert spacex.parent_id == ipo.id
    assert not await store.is_complete(ipo.id)  # new work → no longer complete
    await store.set_status(spacex.id, TaskStatus.COMPLETED)
    assert await store.is_complete(ipo.id)


async def test_add_subtask_reopens_finished_task(tmp_path):
    store = _store(tmp_path)
    ipo = await store.create("IPO research")
    await store.set_status(ipo.id, TaskStatus.COMPLETED)
    assert (await store.get(ipo.id)).status == TaskStatus.COMPLETED

    await store.add_subtask(ipo.id, "Research SpaceX IPO")
    reopened = await store.get(ipo.id)
    assert reopened.status == TaskStatus.RUNNING  # reopened to absorb new work
    assert reopened.ended_at is None
    assert any("SpaceX" in p["message"] for p in reopened.progress)


async def test_remove_deliverable(tmp_path):
    store = _store(tmp_path)
    t = await store.create("x")
    d = await store.add_deliverable(t.id, "thing")
    await store.remove_deliverable(t.id, d["id"])
    assert (await store.get(t.id)).deliverables == []


async def test_completion_needs_acceptance_when_not_auto(tmp_path):
    store = _store(tmp_path)
    t = await store.create("Deck", auto_accept=False)
    d = await store.add_deliverable(t.id, "slides.pdf", criteria="10 slides")
    await store.set_deliverable_status(t.id, d["id"], DeliverableStatus.PRODUCED)
    assert not await store.is_complete(t.id)  # produced but needs sign-off
    await store.set_deliverable_status(t.id, d["id"], DeliverableStatus.ACCEPTED)
    assert await store.is_complete(t.id)

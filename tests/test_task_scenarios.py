"""End-to-end task-execution scenarios — simple → complex, with amendments and
cancellation. Uses a scriptable (no-LLM) executor so behaviour is deterministic.

The harness dispatches each task's executor to a per-title handler, so a test can
script exactly what each task does: produce its deliverables, spawn a subtask
(amendment), hang, raise, etc.
"""

import asyncio

from agclaw.tasks import DeliverableStatus, TaskManager, TaskStatus, TaskStore


def _store(tmp_path):
    return TaskStore(path=tmp_path / "tasks.db")


async def _produce_all(store, task_id):
    t = await store.get(task_id)
    for d in t.deliverables:
        await store.set_deliverable_status(task_id, d["id"], DeliverableStatus.PRODUCED)


class Scripted:
    """Executor dispatching to per-task-title handlers; records call order + concurrency."""

    def __init__(self, handlers=None):
        self.handlers = handlers or {}
        self.calls = []
        self.live = 0
        self.peak = 0

    async def __call__(self, task_id, mgr, asker):
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            task = await mgr.store.get(task_id)
            self.calls.append(task.title)
            handler = self.handlers.get(task.title)
            if handler is not None:
                await handler(task_id, mgr, asker)
            else:
                await _produce_all(mgr.store, task_id)  # default: satisfy deliverables
        finally:
            self.live -= 1


async def _run_root(store, root_id, executor, **kw):
    mgr = TaskManager(store, executor, **kw)
    await mgr.submit(root_id)
    await mgr.wait(root_id)
    return mgr


# --- simple ---


async def test_simple_leaf_completes(tmp_path):
    store = _store(tmp_path)
    t = await store.create("note")
    await store.add_deliverable(t.id, "note.md")
    await _run_root(store, t.id, Scripted())
    assert (await store.get(t.id)).status == TaskStatus.COMPLETED


async def test_leaf_with_no_deliverables_runs_once_then_completes(tmp_path):
    store = _store(tmp_path)
    t = await store.create("ping")
    sc = Scripted()
    await _run_root(store, t.id, sc)
    assert (await store.get(t.id)).status == TaskStatus.COMPLETED
    assert sc.calls == ["ping"]  # executor ran exactly once


# --- orchestration ---


async def test_parallel_subtasks_all_complete(tmp_path):
    store = _store(tmp_path)
    root = await store.create("root")  # pure orchestrator (no own deliverables)
    for name in ("A", "B", "C"):
        sub = await store.create(name, parent_id=root.id)
        await store.add_deliverable(sub.id, f"{name}.out")

    barrier = asyncio.Event()
    started = {"n": 0}

    async def child(task_id, mgr, asker):
        started["n"] += 1
        if started["n"] >= 3:
            barrier.set()
        await asyncio.wait_for(barrier.wait(), timeout=5)  # all three must be live
        await _produce_all(mgr.store, task_id)

    sc = Scripted({"A": child, "B": child, "C": child})
    await _run_root(store, root.id, sc, max_concurrent=3)
    assert (await store.get(root.id)).status == TaskStatus.COMPLETED
    for name in ("A", "B", "C"):
        kid = next(k for k in await store.children(root.id) if k.title == name)
        assert kid.status == TaskStatus.COMPLETED
    assert sc.peak == 3  # genuinely ran in parallel


async def test_orchestrator_synthesises_after_children(tmp_path):
    """Parent's own deliverable (synthesis) is produced only after subtasks done."""
    store = _store(tmp_path)
    root = await store.create("root")
    await store.add_deliverable(root.id, "final deck")
    a = await store.create("A", parent_id=root.id)
    await store.add_deliverable(a.id, "research A")

    order = []

    async def child(task_id, mgr, asker):
        order.append("child")
        await _produce_all(mgr.store, task_id)

    async def root_synth(task_id, mgr, asker):
        # by now the child must be complete
        kids = await mgr.store.children(task_id)
        assert all(k.status == TaskStatus.COMPLETED for k in kids)
        order.append("synth")
        await _produce_all(mgr.store, task_id)

    sc = Scripted({"A": child, "root": root_synth})
    await _run_root(store, root.id, sc)
    assert (await store.get(root.id)).status == TaskStatus.COMPLETED
    assert order == ["child", "synth"]


async def test_nested_three_levels(tmp_path):
    store = _store(tmp_path)
    root = await store.create("root")
    a = await store.create("a", parent_id=root.id)
    b = await store.create("b", parent_id=a.id)
    await store.add_deliverable(b.id, "leaf out")
    await _run_root(store, root.id, Scripted())
    for tid in (root.id, a.id, b.id):
        assert (await store.get(tid)).status == TaskStatus.COMPLETED


async def test_no_deadlock_under_low_cap(tmp_path):
    """Parents awaiting children must not consume worker slots (cap=1)."""
    store = _store(tmp_path)
    root = await store.create("root")
    for name in ("A", "B"):
        sub = await store.create(name, parent_id=root.id)
        await store.add_deliverable(sub.id, f"{name}.out")
    await asyncio.wait_for(_run_root(store, root.id, Scripted(), max_concurrent=1), timeout=5)
    assert (await store.get(root.id)).status == TaskStatus.COMPLETED


# --- amendment ("add SpaceX mid-run") ---


async def test_amendment_midrun_is_picked_up(tmp_path):
    store = _store(tmp_path)
    root = await store.create("IPO research")  # orchestrator
    a = await store.create("Research Anthropic", parent_id=root.id)
    await store.add_deliverable(a.id, "anthropic.md")

    async def anthropic(task_id, mgr, asker):
        # mid-run: "oh and add SpaceX to that IPO research"
        spacex = await mgr.store.add_subtask(root.id, "Research SpaceX", reopen_parent=False)
        await mgr.store.add_deliverable(spacex.id, "spacex.md")
        await _produce_all(mgr.store, task_id)

    sc = Scripted({"Research Anthropic": anthropic})
    await _run_root(store, root.id, sc)
    # the dynamically-added subtask was executed and the root waited for it
    assert "Research SpaceX" in sc.calls
    kids = {k.title: k.status for k in await store.children(root.id)}
    assert kids["Research SpaceX"] == TaskStatus.COMPLETED
    assert (await store.get(root.id)).status == TaskStatus.COMPLETED


# --- cancellation ---


async def test_cancel_root_cascades_immediately(tmp_path):
    store = _store(tmp_path)
    root = await store.create("root")
    a = await store.create("a", parent_id=root.id)
    b = await store.create("b", parent_id=a.id)
    await store.add_deliverable(b.id, "x")
    started = asyncio.Event()

    async def hang(task_id, mgr, asker):
        started.set()
        await asyncio.Event().wait()

    mgr = TaskManager(store, Scripted({"b": hang}))
    await mgr.submit(root.id)
    await asyncio.wait_for(started.wait(), timeout=5)  # deepest leaf is working
    await mgr.cancel(root.id, reason="stop")
    await mgr.wait(root.id)
    for tid in (root.id, a.id, b.id):
        assert (await store.get(tid)).status == TaskStatus.CANCELLED


async def test_cancel_subtask_fails_parent(tmp_path):
    store = _store(tmp_path)
    root = await store.create("root")
    a = await store.create("a", parent_id=root.id)
    await store.add_deliverable(a.id, "x")
    started = asyncio.Event()

    async def hang(task_id, mgr, asker):
        started.set()
        await asyncio.Event().wait()

    mgr = TaskManager(store, Scripted({"a": hang}))
    await mgr.submit(root.id)
    await asyncio.wait_for(started.wait(), timeout=5)
    await mgr.cancel(a.id)  # cancel just the subtask
    await mgr.wait(root.id)
    assert (await store.get(a.id)).status == TaskStatus.CANCELLED
    # parent can't complete with a cancelled (non-completed) subtask
    assert (await store.get(root.id)).status == TaskStatus.FAILED


# --- failure modes ---


async def test_failed_subtask_fails_orchestrator_parent(tmp_path):
    """A pure orchestrator (root with no deliverable of its own) fails when its
    only subtask fails — it has nothing else to be judged by. (A parent WITH its
    own deliverable stays resilient; see test_task_runner.)"""
    store = _store(tmp_path)
    root = await store.create("root")  # no own deliverable
    a = await store.create("a", parent_id=root.id)

    async def boom(task_id, mgr, asker):
        raise RuntimeError("kaboom")

    mgr = TaskManager(store, Scripted({"a": boom}))
    await mgr.submit(root.id)
    await mgr.wait(root.id)
    assert (await store.get(a.id)).status == TaskStatus.FAILED
    assert (await store.get(root.id)).status == TaskStatus.FAILED
    assert "incomplete" in (await store.get(root.id)).error


async def test_unmet_deliverable_fails_after_attempt_limit(tmp_path):
    store = _store(tmp_path)
    t = await store.create("stubborn")
    await store.add_deliverable(t.id, "never produced")

    async def noop(task_id, mgr, asker):
        return  # never produces the deliverable

    sc = Scripted({"stubborn": noop})
    await _run_root(store, t.id, sc)
    got = await store.get(t.id)
    assert got.status == TaskStatus.FAILED
    assert sc.calls.count("stubborn") == TaskManager.MAX_ATTEMPTS  # retried then gave up
    assert "deliverables not met" in got.error


# --- complex, IPO-like ---


async def test_complex_ipo_like_task(tmp_path):
    store = _store(tmp_path)
    root = await store.create("IPO presentation")
    await store.add_deliverable(root.id, "slide deck", "for execs")  # synthesis
    for name in ("Research Anthropic", "Research OpenAI", "Analyse risks"):
        sub = await store.create(name, parent_id=root.id)
        await store.add_deliverable(sub.id, f"{name} notes")

    produced_research = []

    async def child(task_id, mgr, asker):
        t = await mgr.store.get(task_id)
        produced_research.append(t.title)
        await mgr.progress(task_id, f"done {t.title}", pct=100)
        await _produce_all(mgr.store, task_id)

    async def synth(task_id, mgr, asker):
        assert len(produced_research) == 3  # all research finished first
        await _produce_all(mgr.store, task_id)

    handlers = {n: child for n in ("Research Anthropic", "Research OpenAI", "Analyse risks")}
    handlers["IPO presentation"] = synth
    await _run_root(store, root.id, Scripted(handlers), max_concurrent=3)

    assert (await store.get(root.id)).status == TaskStatus.COMPLETED
    assert set(produced_research) == {"Research Anthropic", "Research OpenAI", "Analyse risks"}
    # the deck deliverable is satisfied
    assert (await store.get(root.id)).deliverables_satisfied()

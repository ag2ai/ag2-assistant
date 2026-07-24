"""Deterministic scheduler over the new Task model (paused / next_run_at)."""

from datetime import datetime, timedelta

from assistant.tasks.scheduling import Scheduler
from assistant.tasks.store import TaskStore


async def test_tick_fires_due_unpaused_tasks_only(tmp_path):
    st = TaskStore(path=tmp_path / "tasks.db")
    past = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()
    due = await st.create_task("due", "p", schedule={"kind": "once", "at": past})
    paused = await st.create_task("paused", "p", schedule={"kind": "once", "at": past})
    await st.update_task(paused.id, paused=True)
    await st.create_task("manual", "p")  # next_run_at is None → never fires

    fired: list[str] = []

    async def fire(task_id: str) -> None:
        fired.append(task_id)
        # a real fire re-arms/disarms; mimic it so a second tick can't re-fire
        await st.update_task(task_id, next_run_at=None)

    sched = Scheduler(st, fire, interval=0.01)
    assert await sched.tick() == [due.id]
    assert fired == [due.id]
    assert await sched.tick() == []  # disarmed — no double fire

"""Task scheduling — recurrence parsing + the deterministic poll loop."""

from datetime import datetime, timedelta

from agclaw.tasks import TaskStatus, TaskStore
from agclaw.tasks.scheduling import (
    Scheduler,
    is_due,
    next_occurrence,
    parse_recurrence,
)


def test_parse_recurrence():
    assert parse_recurrence("daily") == timedelta(days=1)
    assert parse_recurrence("Hourly") == timedelta(hours=1)
    assert parse_recurrence("weekly") == timedelta(weeks=1)
    assert parse_recurrence("every 30 minutes") == timedelta(minutes=30)
    assert parse_recurrence("every 2 hours") == timedelta(hours=2)
    assert parse_recurrence("every 3 days") == timedelta(days=3)
    assert parse_recurrence("every week") == timedelta(weeks=1)
    assert parse_recurrence("") is None
    assert parse_recurrence(None) is None
    assert parse_recurrence("whenever") is None


def test_is_due():
    now = datetime.now().astimezone()
    assert is_due((now - timedelta(minutes=1)).isoformat(), now) is True
    assert is_due((now + timedelta(minutes=1)).isoformat(), now) is False
    assert is_due(None, now) is False
    assert is_due("not-a-date", now) is False


def test_next_occurrence_skips_missed_slots():
    now = datetime.now().astimezone()
    anchor = (now - timedelta(days=3, hours=-9)).isoformat()  # a few days ago
    nxt = next_occurrence("daily", anchor, now)
    assert nxt is not None and nxt > now
    # it lands on a whole-day multiple from the anchor (time-of-day preserved)
    assert (nxt - datetime.fromisoformat(anchor)) % timedelta(days=1) == timedelta(0)
    assert next_occurrence(None, anchor, now) is None  # not recurring


def _store(tmp_path):
    return TaskStore(path=tmp_path / "tasks.db")


async def test_scheduler_tick_fires_due_only(tmp_path):
    store = _store(tmp_path)
    now = datetime.now().astimezone()
    due = await store.create("due", status=TaskStatus.SCHEDULED,
                             scheduled_for=(now - timedelta(minutes=5)).isoformat())
    await store.create("future", status=TaskStatus.SCHEDULED,
                       scheduled_for=(now + timedelta(hours=1)).isoformat())
    await store.create("not scheduled")  # pending, ignored

    # archived scheduled tasks are put away — they don't fire
    await store.create("archived due", status=TaskStatus.SCHEDULED,
                       scheduled_for=(now - timedelta(minutes=5)).isoformat(), archived=True)

    fired = []

    async def fire(tid):
        fired.append(tid)

    sched = Scheduler(store, fire, interval=999)
    got = await sched.tick(now=now)
    assert got == [due.id]
    assert fired == [due.id]

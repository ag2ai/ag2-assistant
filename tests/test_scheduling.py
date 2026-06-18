"""Task scheduling — recurrence parsing + the deterministic poll loop."""

from datetime import datetime, timedelta

from assistant.tasks import TaskStatus, TaskStore
from assistant.tasks.scheduling import (
    Scheduler,
    first_occurrence,
    is_due,
    next_occurrence,
    parse_recurrence,
)


def test_parse_recurrence_intervals():
    assert parse_recurrence("daily") == {"kind": "interval", "delta": timedelta(days=1)}
    assert parse_recurrence("Hourly")["delta"] == timedelta(hours=1)
    assert parse_recurrence("every 30 minutes")["delta"] == timedelta(minutes=30)
    assert parse_recurrence("every 2 hours")["delta"] == timedelta(hours=2)
    assert parse_recurrence("every week")["delta"] == timedelta(weeks=1)
    assert parse_recurrence("") is None
    assert parse_recurrence(None) is None
    assert parse_recurrence("whenever") is None
    assert parse_recurrence("fortnightly") is None


def test_parse_recurrence_days():
    assert parse_recurrence("weekdays") == {"kind": "days", "days": frozenset({0, 1, 2, 3, 4})}
    assert parse_recurrence("weekends")["days"] == frozenset({5, 6})
    assert parse_recurrence("mon,wed,fri")["days"] == frozenset({0, 2, 4})
    assert parse_recurrence("every monday and friday")["days"] == frozenset({0, 4})
    assert parse_recurrence("tuesdays")["days"] == frozenset({1})


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


def test_weekday_recurrence_skips_weekends():
    # Friday 2026-06-19 05:00 → next weekday occurrence is Monday 2026-06-22 05:00
    fri_5am = "2026-06-19T05:00:00+10:00"
    now = datetime.fromisoformat("2026-06-19T06:00:00+10:00")  # just after Fri 5am
    nxt = next_occurrence("weekdays", fri_5am, now)
    assert nxt.weekday() == 0 and nxt.hour == 5  # Monday 05:00, weekend skipped
    assert nxt.date().isoformat() == "2026-06-22"


def test_first_occurrence_snaps_to_matching_weekday():
    # scheduling 'weekdays 05:00' on a Saturday should start Monday
    sat = datetime.fromisoformat("2026-06-20T09:00:00+10:00")  # Saturday
    first = first_occurrence("weekdays", "2026-06-20T05:00:00+10:00", sat)
    assert first.weekday() == 0 and first.hour == 5  # Monday 05:00
    # intervals are honoured as-is
    assert first_occurrence("daily", "2026-06-20T05:00:00+10:00", sat).isoformat() == "2026-06-20T05:00:00+10:00"


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

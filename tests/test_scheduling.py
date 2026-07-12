"""Task scheduling — cron recurrence parsing + the deterministic poll loop."""

from datetime import datetime, timedelta

from assistant.tasks import TaskStatus, TaskStore
from assistant.tasks.scheduling import (
    Scheduler,
    describe_cron,
    first_occurrence,
    is_due,
    next_occurrence,
    normalize_cron,
    validate_schedule,
)


def test_validate_schedule_accepts_valid_and_rejects_malformed():
    good = "2030-01-01T09:00:00+10:00"
    # valid combinations
    assert validate_schedule(good, "0 9 * * *", require_when=True) is None
    assert validate_schedule(good, "0 4-14 * * 1-5", require_when=True) is None
    assert validate_schedule(good, "@daily", require_when=True) is None
    assert validate_schedule(good, "", require_when=True) is None  # one-off
    assert validate_schedule("", "off") is None  # reschedule: stop repeating, keep time
    assert validate_schedule("", "") is None  # reschedule: keep both
    # malformed when / missing required when / malformed recurrence → correctable error
    assert "date/time" in (validate_schedule("banana", "", require_when=True) or "")
    assert "required" in (validate_schedule("", "", require_when=True) or "")
    assert "cron" in (validate_schedule(good, "every blue moon", require_when=True) or "")
    assert "cron" in (validate_schedule(good, "daily", require_when=True) or "")  # old grammar
    assert "cron" in (validate_schedule(good, "99 * * * *", require_when=True) or "")


def test_normalize_cron():
    assert normalize_cron("0 9 * * *") == "0 9 * * *"
    assert normalize_cron("@hourly") == "0 * * * *"
    assert normalize_cron("@Daily") == "0 0 * * *"
    assert normalize_cron("@weekly") == "0 0 * * 0"
    assert normalize_cron("") is None
    assert normalize_cron(None) is None
    assert normalize_cron("hourly") is None  # bare names are not cron
    assert normalize_cron("0 4-14 * *") is None  # wrong field count
    assert normalize_cron("99 * * * *") is None  # bad minute


def test_describe_cron():
    # minute-0 + hour-range gets its cadence restored (cron-descriptor omits it)
    assert (
        describe_cron("0 4-14 * * 1-5")
        == "Every hour, between 04:00 and 14:59, Monday through Friday"
    )
    assert describe_cron("30 4 * * *") == "At 04:30"
    assert describe_cron("@hourly") == "Every hour"
    assert describe_cron("nonsense") is None


def test_is_due():
    now = datetime.now().astimezone()
    assert is_due((now - timedelta(minutes=1)).isoformat(), now) is True
    assert is_due((now + timedelta(minutes=1)).isoformat(), now) is False
    assert is_due(None, now) is False
    assert is_due("not-a-date", now) is False


def test_next_occurrence_respects_window_and_days():
    # Thursday 2026-07-09 19:30 → hourly-weekday-window cron skips to Friday 04:00
    now = datetime.fromisoformat("2026-07-09T19:30:00+10:00")
    nxt = next_occurrence("0 4-14 * * 1-5", now)
    assert nxt.isoformat() == "2026-07-10T04:00:00+10:00"
    # inside the window it's simply the next hour
    inside = datetime.fromisoformat("2026-07-10T05:10:00+10:00")
    assert next_occurrence("0 4-14 * * 1-5", inside).isoformat() == "2026-07-10T06:00:00+10:00"
    # Friday 14:00 fired → next is Monday 04:00 (weekend skipped)
    fri_last = datetime.fromisoformat("2026-07-10T14:00:00+10:00")
    assert next_occurrence("0 4-14 * * 1-5", fri_last).isoformat() == "2026-07-13T04:00:00+10:00"
    assert next_occurrence(None, now) is None  # not recurring
    assert next_occurrence("banana", now) is None


def test_first_occurrence_snaps_to_cron():
    # scheduling a weekday cron with a Saturday `when` starts Monday
    first = first_occurrence("0 5 * * 1-5", "2026-07-11T05:00:00+10:00")  # a Saturday
    assert first.weekday() == 0 and first.hour == 5
    assert first.date().isoformat() == "2026-07-13"
    # a `when` exactly on a cron match is honoured as the first occurrence
    first = first_occurrence("0 4-14 * * 1-5", "2026-07-10T04:00:00+10:00")
    assert first.isoformat() == "2026-07-10T04:00:00+10:00"
    # `when` is a not-before floor for the cron
    first = first_occurrence("@hourly", "2030-01-01T09:30:00+10:00")
    assert first.isoformat() == "2030-01-01T10:00:00+10:00"
    # one-offs are honoured as-is
    assert (
        first_occurrence("", "2026-07-11T05:00:00+10:00").isoformat() == "2026-07-11T05:00:00+10:00"
    )


def _store(tmp_path):
    return TaskStore(path=tmp_path / "tasks.db")


async def test_scheduler_tick_fires_due_only(tmp_path):
    store = _store(tmp_path)
    now = datetime.now().astimezone()
    due = await store.create(
        "due", status=TaskStatus.SCHEDULED, scheduled_for=(now - timedelta(minutes=5)).isoformat()
    )
    await store.create(
        "future", status=TaskStatus.SCHEDULED, scheduled_for=(now + timedelta(hours=1)).isoformat()
    )
    await store.create("not scheduled")  # pending, ignored

    fired = []

    async def fire(tid):
        fired.append(tid)

    sched = Scheduler(store, fire, interval=999)
    got = await sched.tick(now=now)
    assert got == [due.id]
    assert fired == [due.id]

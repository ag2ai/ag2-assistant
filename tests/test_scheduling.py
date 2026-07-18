"""Task scheduling — cron recurrence parsing utilities.

Note: the old `validate_schedule`/`first_occurrence` helpers and the
create(status=..., scheduled_for=...)-shaped `TaskStore`/`TaskStatus` this file
used to also exercise were removed by the TaskService v2 rewrite (schedules are
now validated via `normalize_schedule`/`ValueError`, and the `Scheduler`
poll-loop is covered against the new Task model in test_task_scheduling.py).
The cron-parsing utilities below (`normalize_cron`, `describe_cron`, `is_due`,
`next_occurrence`) are unchanged, so their tests stay as-is.
"""

from datetime import datetime, timedelta

from assistant.tasks.scheduling import describe_cron, is_due, next_occurrence, normalize_cron


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



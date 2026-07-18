"""New Task/Run domain model: schedule union normalisation + (de)serialisation."""

import pytest

from assistant.tasks.model import (
    Run,
    RunStatus,
    ScheduleKind,
    Task,
    manual_schedule,
    normalize_schedule,
)


def test_manual_schedule_default():
    assert normalize_schedule(None) == {"kind": "manual", "at": None, "cron": None}
    assert normalize_schedule({"kind": "manual"}) == manual_schedule()
    # the wire format stays plain strings — constants are just named references
    assert ScheduleKind.ALL == ("manual", "once", "cron")


def test_once_schedule_requires_valid_datetime():
    s = normalize_schedule({"kind": "once", "at": "2026-08-01T09:00:00+03:00"})
    assert s == {"kind": "once", "at": "2026-08-01T09:00:00+03:00", "cron": None}
    with pytest.raises(ValueError):
        normalize_schedule({"kind": "once", "at": "tomorrow-ish"})
    with pytest.raises(ValueError):
        normalize_schedule({"kind": "once"})


def test_cron_schedule_normalises_nicknames_and_rejects_garbage():
    assert normalize_schedule({"kind": "cron", "cron": "@daily"})["cron"] == "0 0 * * *"
    assert normalize_schedule({"kind": "cron", "cron": "0 9 * * 1-5"})["cron"] == "0 9 * * 1-5"
    with pytest.raises(ValueError):
        normalize_schedule({"kind": "cron", "cron": "every tuesday"})
    with pytest.raises(ValueError):
        normalize_schedule({"kind": "party"})


def test_task_round_trips_and_ignores_unknown_keys():
    t = Task(id="task_1", name="Digest", prompt="do it")
    d = t.to_dict()
    d["legacy_field"] = "junk"  # old records must not crash the loader
    t2 = Task.from_dict(d)
    assert t2.name == "Digest" and t2.schedule == manual_schedule() and t2.paused is False


def test_run_stream_id_and_terminal_states():
    r = Run(id="run_1", task_id="task_1")
    assert r.stream_id == "task-run:run_1"
    assert r.status == RunStatus.RUNNING
    assert RunStatus.TERMINAL == {"completed", "failed", "cancelled"}
    assert Run.from_dict(r.to_dict()).task_id == "task_1"

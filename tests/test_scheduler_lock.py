"""Single-owner scheduler: the opt-in `scheduler` flag + a cross-process lock.

flock is per-open-file-description, so two locks on one path within a process
behave like two processes here.
"""

from assistant.config import Config
from assistant.gateway.tasks_service import TaskService
from assistant.scheduler_lock import SchedulerLock


async def _ex(task_id, mgr, asker):
    pass


def test_scheduler_lock_is_exclusive(tmp_path):
    p = tmp_path / "scheduler.lock"
    a, b = SchedulerLock(p), SchedulerLock(p)
    assert a.acquire() is True
    assert b.acquire() is False
    a.release()
    assert b.acquire() is True
    b.release()


def test_scheduler_lock_release_is_idempotent(tmp_path):
    a = SchedulerLock(tmp_path / "s.lock")
    assert a.acquire() is True
    a.release()
    a.release()
    assert a.acquire() is True
    a.release()


async def test_start_without_scheduler_keeps_tools_but_no_loop(tmp_path):
    svc = TaskService(config=Config(data_dir=tmp_path))
    svc._executor = _ex
    await svc.start(scheduler=False)
    try:
        assert svc._scheduler is None
        assert svc._scheduler_lock is None
        t = await svc.store.create("schedule me")  # tools still work
        assert t.id
    finally:
        await svc.close()


async def test_only_one_scheduler_leader_per_datadir(tmp_path):
    a = TaskService(config=Config(data_dir=tmp_path))
    b = TaskService(config=Config(data_dir=tmp_path))
    a._executor = b._executor = _ex
    await a.start(scheduler=True)
    await b.start(scheduler=True)
    try:
        assert a._scheduler is not None
        assert a._scheduler_lock is not None
        assert b._scheduler is None
        assert b._scheduler_lock is None
    finally:
        await a.close()
        await b.close()


async def test_scheduler_lock_frees_for_next_owner(tmp_path):
    a = TaskService(config=Config(data_dir=tmp_path))
    a._executor = _ex
    await a.start(scheduler=True)
    assert a._scheduler is not None
    await a.close()

    b = TaskService(config=Config(data_dir=tmp_path))
    b._executor = _ex
    await b.start(scheduler=True)
    try:
        assert b._scheduler is not None
    finally:
        await b.close()

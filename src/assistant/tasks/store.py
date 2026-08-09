"""Persistent task store — Task and Run JSON docs in a SQLite knowledge store.

Two doc kinds in one ``tasks.db``: ``/tasks/{task_id}.json`` (standing config)
and ``/runs/{run_id}.json`` (one execution's metadata). A run's transcript is
NOT here — it lives on the run's chat stream (``task-run:{run_id}``, chats.db).
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from ag2.knowledge import SqliteKnowledgeStore

from assistant.storage import SerialStore as _SerialStore
from assistant.storage import new_id, now_iso
from assistant.tasks.model import (
    RECALL_ALL,
    RECALL_NONE,
    Run,
    RunStatus,
    RunTrigger,
    ScheduleKind,
    Task,
    manual_schedule,
)
from assistant.tasks.scheduling import compute_next_run

_TASKS = "/tasks/"
_RUNS = "/runs/"
logger = logging.getLogger("ag2assistant.tasks")


class TaskStoreCorruptionError(RuntimeError):
    """Raised when an existing record cannot be decoded."""

    def __init__(self, doc_id: str, path: str, reason: BaseException) -> None:
        self.doc_id = doc_id
        self.path = path
        self.reason = reason
        super().__init__(f"Record {doc_id!r} at {path} is corrupt: {reason}")


def _now_dt() -> datetime:
    return datetime.now().astimezone()


class TaskStore:
    """CRUD over persisted Task + Run records."""

    def __init__(self, path: Path | None = None, store=None) -> None:
        if store is not None:
            self._store = store
        else:
            if path is None:
                raise ValueError("TaskStore needs an explicit `path` (or a `store`)")
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._store = _SerialStore(SqliteKnowledgeStore(str(path)))

    # ---- tasks ----

    async def create_task(
        self,
        name: str,
        prompt: str = "",
        model: str | None = None,
        schedule: dict | None = None,
        origin_channel: str | None = None,
        origin_chat: str | None = None,
        description: str = "",
        recall_depth: int = 0,
    ) -> Task:
        sched = schedule or manual_schedule()
        task = Task(
            id=new_id("task"),
            name=name,
            prompt=prompt,
            model=model,
            schedule=sched,
            origin_channel=origin_channel,
            origin_chat=origin_chat,
            description=description,
            recall_depth=recall_depth,
            next_run_at=compute_next_run(sched, _now_dt()),
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        await self.save_task(task)
        return task

    async def save_task(self, task: Task) -> None:
        await self._store.write(f"{_TASKS}{task.id}.json", json.dumps(task.to_dict()))

    async def get_task(self, task_id: str) -> Task | None:
        return await self._read(f"{_TASKS}{task_id}.json", Task, task_id)

    async def delete_task(self, task_id: str) -> None:
        await self._store.delete(f"{_TASKS}{task_id}.json")

    async def list_tasks(self) -> list[Task]:
        tasks = await self._read_all(_TASKS, Task)
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks

    async def strip_workdirs(self) -> list[tuple[str, str, str]]:
        """One-time migration (2026-07-20 task-folders): pop the legacy
        workdir/workdir_access keys off every persisted task record and return
        ``(task_id, workdir, access)`` for each task that had a folder attached.
        Idempotent — a second call finds nothing to strip."""
        moved: list[tuple[str, str, str]] = []
        for entry in await self._store.list(_TASKS):
            if not entry.endswith(".json"):
                continue
            path = _TASKS + entry
            try:
                data = json.loads(await self._store.read(path))
            except Exception:
                continue  # corrupt records are _read_all's problem, not the migration's
            if "workdir" not in data and "workdir_access" not in data:
                continue
            wd = data.pop("workdir", None)
            access = data.pop("workdir_access", None)
            await self._store.write(path, json.dumps(data))
            task_id = data.get("id", "")
            # id-less record → no valid task to attach a grant to; skip it rather
            # than mint a PROFILE-scope grant (privilege widening) with task_id="".
            if wd and task_id:
                moved.append((task_id, wd, access or "read"))
        return moved

    async def backfill_recall(self, cron_depth: int) -> int:
        """One-time migration (ADR 0027): stamp ``recall_depth`` onto records written
        before the field existed, giving recurring tasks ``cron_depth`` so the
        look-back they had until now survives the upgrade. Returns how many records
        moved.

        Idempotent through the key's absence — a record written since carries the key
        already, so a user who later chooses 0 is never overwritten.
        """
        moved = 0
        for entry in await self._store.list(_TASKS):
            if not entry.endswith(".json"):
                continue
            path = _TASKS + entry
            try:
                data = json.loads(await self._store.read(path))
            except Exception:
                continue  # corrupt records are _read_all's problem, not the migration's
            if "recall_depth" in data:
                continue
            cron = (data.get("schedule") or {}).get("kind") == ScheduleKind.CRON
            data["recall_depth"] = cron_depth if cron else RECALL_NONE
            await self._store.write(path, json.dumps(data))
            moved += 1
        return moved

    async def rekey_origin_channels(self, by_platform: dict[str, str]) -> int:
        """Rewrite every ``origin_channel`` still holding a platform name to that
        platform's Connection id, and return how many records moved. Idempotent."""
        moved = 0
        for entry in await self._store.list(_TASKS):
            if not entry.endswith(".json"):
                continue
            path = _TASKS + entry
            try:
                data = json.loads(await self._store.read(path))
            except Exception:
                continue  # corrupt records are _read_all's problem, not the migration's
            cid = by_platform.get(data.get("origin_channel") or "")
            if cid is None:
                continue
            data["origin_channel"] = cid
            await self._store.write(path, json.dumps(data))
            moved += 1
        return moved

    async def update_task(self, task_id: str, **fields) -> Task | None:
        """Patch task fields. ``id``/``created_at`` are protected; ``updated_at``
        bumps on every write. ``next_run_at`` re-derives from the schedule when
        schedule/paused change — unless the caller pins it explicitly (the
        scheduler's re-arm passes it directly)."""
        task = await self.get_task(task_id)
        if task is None:
            return None
        explicit_next = "next_run_at" in fields
        protected = {"id", "created_at"}
        for k, v in fields.items():
            if k in protected or k not in Task.__dataclass_fields__:
                continue
            setattr(task, k, v)
        if not explicit_next and ("schedule" in fields or "paused" in fields):
            task.next_run_at = None if task.paused else compute_next_run(task.schedule, _now_dt())
        task.updated_at = now_iso()
        await self.save_task(task)
        return task

    # ---- runs ----

    async def create_run(self, task_id: str, trigger: RunTrigger = "manual") -> Run:
        run = Run(id=new_id("run"), task_id=task_id, trigger=trigger, started_at=now_iso())
        await self.save_run(run)
        return run

    async def save_run(self, run: Run) -> None:
        await self._store.write(f"{_RUNS}{run.id}.json", json.dumps(run.to_dict()))

    async def get_run(self, run_id: str) -> Run | None:
        return await self._read(f"{_RUNS}{run_id}.json", Run, run_id)

    async def delete_run(self, run_id: str) -> None:
        await self._store.delete(f"{_RUNS}{run_id}.json")

    async def list_runs(self, task_id: str | None = None) -> list[Run]:
        runs = await self._read_all(_RUNS, Run)
        if task_id is not None:
            runs = [r for r in runs if r.task_id == task_id]
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return runs

    async def set_run_status(self, run_id: str, status: str, **fields) -> Run | None:
        """Transition a run (+ optional fields). Terminal states are sticky —
        a late transition on a settled run is dropped, so a user stop and the
        executor's own finish can't fight over the record."""
        run = await self.get_run(run_id)
        if run is None or run.status in RunStatus.TERMINAL:
            return run
        run.status = status
        for k, v in fields.items():
            if k in Run.__dataclass_fields__ and k not in {"id", "task_id"}:
                setattr(run, k, v)
        if status in RunStatus.TERMINAL and not run.ended_at:
            run.ended_at = now_iso()
        await self.save_run(run)
        return run

    async def recent_runs(
        self, task_id: str, n: int = RECALL_NONE, before: str | None = None
    ) -> list[Run]:
        """The task's most recent settled runs, oldest-first so they read
        chronologically in a prompt. ``n`` is a count, ``RECALL_ALL`` for every one,
        ``RECALL_NONE`` for none; ``before`` excludes the currently-executing run.

        Failed and cancelled runs are kept: they carry no summary but may have
        committed work before settling (ADR 0018), which the caller renders as a
        pointer rather than dropping.
        """
        if n == RECALL_NONE:
            return []
        out: list[Run] = []
        for r in await self.list_runs(task_id):  # newest first
            if r.id == before or r.status not in RunStatus.TERMINAL:
                continue
            out.append(r)
            if n != RECALL_ALL and len(out) >= n:
                break
        return list(reversed(out))

    # ---- shared readers ----

    async def _read(self, path: str, cls, doc_id: str):
        if not await self._store.exists(path):
            return None
        try:
            return cls.from_dict(json.loads(await self._store.read(path)))
        except Exception as exc:
            raise TaskStoreCorruptionError(doc_id, path, exc) from exc

    async def _read_all(self, prefix: str, cls) -> list:
        out = []
        for entry in await self._store.list(prefix):
            if not entry.endswith(".json"):
                continue
            try:
                out.append(cls.from_dict(json.loads(await self._store.read(prefix + entry))))
            except Exception:
                logger.exception("skipping corrupt record %s", entry)
        return out

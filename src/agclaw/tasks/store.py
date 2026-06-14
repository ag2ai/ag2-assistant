"""Persistent task store — JSON task records in a SQLite knowledge store.

Each task is one JSON doc at `/tasks/{id}.json`; the tree is reconstructed from
`parent_id`. Mirrors how sessions are persisted, so it's durable across restarts.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

from agclaw.tasks.model import Deliverable, DeliverableStatus, Task, TaskStatus

_PREFIX = "/tasks/"


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _new_id() -> str:
    return "task-" + uuid.uuid4().hex[:12]


class TaskStore:
    """CRUD + tree access over persisted Task records."""

    def __init__(self, path: Path | None = None, store=None) -> None:
        if store is not None:
            self._store = store
        else:
            from autogen.beta.knowledge import SqliteKnowledgeStore

            path = path or (Path.home() / ".agclaw" / "tasks.db")
            path.parent.mkdir(parents=True, exist_ok=True)
            self._store = SqliteKnowledgeStore(str(path))

    def _path(self, task_id: str) -> str:
        return f"{_PREFIX}{task_id}.json"

    async def create(
        self,
        title: str,
        description: str = "",
        parent_id: str | None = None,
        status: str = TaskStatus.PENDING,
        **fields,
    ) -> Task:
        """Create, persist, and return a new task."""
        task = Task(
            id=_new_id(),
            title=title,
            description=description,
            parent_id=parent_id,
            status=status,
            created_at=_now(),
            stream_id=None,
            **fields,
        )
        task.stream_id = f"task:{task.id}"
        await self.save(task)
        return task

    async def save(self, task: Task) -> None:
        await self._store.write(self._path(task.id), json.dumps(task.to_dict()))

    async def get(self, task_id: str) -> Task | None:
        path = self._path(task_id)
        if not await self._store.exists(path):
            return None
        try:
            return Task.from_dict(json.loads(await self._store.read(path)))
        except Exception:
            return None

    async def delete(self, task_id: str) -> None:
        await self._store.delete(self._path(task_id))

    async def list_all(self) -> list[Task]:
        tasks = []
        for entry in await self._store.list(_PREFIX):
            if not entry.endswith(".json"):
                continue
            try:
                tasks.append(Task.from_dict(json.loads(await self._store.read(_PREFIX + entry))))
            except Exception:
                continue
        return tasks

    async def children(self, parent_id: str) -> list[Task]:
        kids = [t for t in await self.list_all() if t.parent_id == parent_id]
        kids.sort(key=lambda t: t.created_at)
        return kids

    async def roots(self) -> list[Task]:
        roots = [t for t in await self.list_all() if t.parent_id is None]
        roots.sort(key=lambda t: t.created_at, reverse=True)  # newest first
        return roots

    async def descendants(self, task_id: str) -> list[Task]:
        """All descendants of a task (depth-first), for cascade operations."""
        all_tasks = await self.list_all()
        by_parent: dict[str | None, list[Task]] = {}
        for t in all_tasks:
            by_parent.setdefault(t.parent_id, []).append(t)
        out: list[Task] = []
        stack = list(by_parent.get(task_id, []))
        while stack:
            t = stack.pop()
            out.append(t)
            stack.extend(by_parent.get(t.id, []))
        return out

    async def tree(self, task_id: str) -> dict | None:
        """A nested {task, children:[...]} dict rooted at task_id (for the GUI)."""
        all_tasks = {t.id: t for t in await self.list_all()}
        if task_id not in all_tasks:
            return None
        by_parent: dict[str | None, list[Task]] = {}
        for t in all_tasks.values():
            by_parent.setdefault(t.parent_id, []).append(t)

        def build(tid: str) -> dict:
            kids = sorted(by_parent.get(tid, []), key=lambda t: t.created_at)
            return {
                "task": all_tasks[tid].to_dict(),
                "children": [build(k.id) for k in kids],
            }

        return build(task_id)

    async def set_status(self, task_id: str, status: str, **fields) -> Task | None:
        """Convenience: update a task's status (+ optional fields) and persist."""
        task = await self.get(task_id)
        if task is None:
            return None
        task.status = status
        for k, v in fields.items():
            setattr(task, k, v)
        if status == TaskStatus.RUNNING and not task.started_at:
            task.started_at = _now()
        if status in TaskStatus.TERMINAL and not task.ended_at:
            task.ended_at = _now()
        await self.save(task)
        return task

    # --- deliverables (what gates completion) ---

    async def add_deliverable(self, task_id: str, description: str, criteria: str = "") -> dict | None:
        task = await self.get(task_id)
        if task is None:
            return None
        dlv = Deliverable.new(description, criteria)
        task.deliverables.append(dlv)
        await self.save(task)
        return dlv

    async def set_deliverable_status(
        self, task_id: str, deliverable_id: str, status: str,
        asset: dict | None = None, notes: str = "",
    ) -> Task | None:
        task = await self.get(task_id)
        if task is None:
            return None
        for d in task.deliverables:
            if d.get("id") == deliverable_id:
                d["status"] = status
                if asset is not None:
                    d["asset"] = asset
                if notes:
                    d["notes"] = notes
                break
        await self.save(task)
        return task

    async def is_complete(self, task_id: str) -> bool:
        """A task is complete only when all its deliverables are satisfied AND
        every descendant subtask is itself completed. This is the objective check
        the runner uses to decide 'done' — not just the agent stopping."""
        task = await self.get(task_id)
        if task is None:
            return False
        if not task.deliverables_satisfied():
            return False
        for d in await self.descendants(task_id):
            if d.status != TaskStatus.COMPLETED:
                return False
        return True

    async def add_progress(self, task_id: str, message: str, pct: int | None = None) -> None:
        task = await self.get(task_id)
        if task is None:
            return
        entry = {"at": _now(), "message": message}
        if pct is not None:
            entry["pct"] = pct
        task.progress.append(entry)
        await self.save(task)

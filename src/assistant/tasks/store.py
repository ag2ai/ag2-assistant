"""Persistent task store — JSON task records in a SQLite knowledge store.

Each task is one JSON doc at `/tasks/{id}.json`; the tree is reconstructed from
`parent_id`. Mirrors how sessions are persisted, so it's durable across restarts.
"""

import json
from pathlib import Path

from assistant.storage import SerialStore as _SerialStore
from assistant.storage import new_id, now_iso
from assistant.tasks.model import Deliverable, Task, TaskStatus

_PREFIX = "/tasks/"


def _now() -> str:
    return now_iso()


def _new_id() -> str:
    return new_id("task")


class TaskStore:
    """CRUD + tree access over persisted Task records."""

    def __init__(self, path: Path | None = None, store=None) -> None:
        if store is not None:
            self._store = store
        else:
            from autogen.beta.knowledge import SqliteKnowledgeStore

            path = path or (Path.home() / ".ag2assistant" / "tasks.db")
            path.parent.mkdir(parents=True, exist_ok=True)
            self._store = _SerialStore(SqliteKnowledgeStore(str(path)))

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

    # --- amendment (change a task's scope, even mid-run or after finishing) ---

    async def update(self, task_id: str, note: str | None = None, **fields) -> Task | None:
        """Patch task fields (title/objective/description/schedule/auto_accept/…).

        `id`/`parent_id`/`created_at` are protected. Optionally records a `note`
        in the progress log so the change is visible in history.
        """
        task = await self.get(task_id)
        if task is None:
            return None
        protected = {"id", "parent_id", "created_at"}
        for k, v in fields.items():
            if k in protected or k not in Task.__dataclass_fields__:
                continue
            setattr(task, k, v)
        if note:
            task.progress.append({"at": _now(), "message": note})
        await self.save(task)
        return task

    async def reopen(
        self, task_id: str, status: str = TaskStatus.RUNNING, note: str | None = None
    ) -> Task | None:
        """Re-activate a finished/failed task (e.g. when new work is added)."""
        task = await self.get(task_id)
        if task is None:
            return None
        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            task.status = status
            task.ended_at = None
            if note:
                task.progress.append({"at": _now(), "message": note})
            await self.save(task)
        return task

    async def add_subtask(
        self,
        parent_id: str,
        title: str,
        description: str = "",
        reopen_parent: bool = True,
        **fields,
    ) -> Task | None:
        """Amend a task by adding a subtask. Reopens the parent if it had finished
        (so 'add SpaceX to that IPO research' resumes rather than stays done)."""
        parent = await self.get(parent_id)
        if parent is None:
            return None
        child = await self.create(title, description=description, parent_id=parent_id, **fields)
        note = f"Scope amended: added subtask '{title}'"
        if reopen_parent and parent.status in TaskStatus.TERMINAL:
            await self.reopen(parent_id, note=note)
        else:
            await self.add_progress(parent_id, note)
        return child

    async def remove_deliverable(self, task_id: str, deliverable_id: str) -> Task | None:
        task = await self.get(task_id)
        if task is None:
            return None
        task.deliverables = [d for d in task.deliverables if d.get("id") != deliverable_id]
        await self.save(task)
        return task

    async def set_deliverables(self, task_id: str, descriptions: list[str]) -> Task | None:
        """Replace the task's deliverables with a fresh list (one PENDING deliverable per
        description). The re-scoping primitive — e.g. relaxing several stale requirements
        down to one — so a recurring task's clones don't keep accumulating outputs."""
        task = await self.get(task_id)
        if task is None:
            return None
        task.deliverables = [Deliverable.new(d) for d in descriptions if d and d.strip()]
        await self.save(task)
        return task

    # --- deliverables (what gates completion) ---

    async def add_deliverable(
        self, task_id: str, description: str, criteria: str = ""
    ) -> dict | None:
        task = await self.get(task_id)
        if task is None:
            return None
        dlv = Deliverable.new(description, criteria)
        task.deliverables.append(dlv)
        await self.save(task)
        return dlv

    async def set_deliverable_status(
        self,
        task_id: str,
        deliverable_id: str,
        status: str,
        asset: dict | None = None,
        notes: str = "",
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
        """A task is complete when its own deliverables are satisfied AND no
        descendant is still in flight (every subtask has reached a terminal
        state). A subtask that FAILED is terminal and accounted for — the parent
        stays resilient: its own deliverable verification, fed the gap as
        context, is the arbiter of whether the overall task succeeded.

        Exception: a task with NO deliverables of its own (a pure orchestrator)
        has nothing else to be judged by, so it succeeds only if every subtask
        did. This is the objective 'done' check the runner uses — not just the
        agent stopping."""
        task = await self.get(task_id)
        if task is None:
            return False
        if not task.deliverables_satisfied():
            return False
        has_own = bool(task.deliverables)
        for d in await self.descendants(task_id):
            if not d.is_terminal:
                return False  # still running → not done
            if not has_own and d.status != TaskStatus.COMPLETED:
                return False  # orchestrator with nothing of its own → needs subtasks to pass
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

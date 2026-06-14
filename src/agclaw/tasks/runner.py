"""Task runner — runs tasks in the background, concurrently, cancellably.

`TaskManager.submit(task_id)` launches a task as a background asyncio task (capped
by a semaphore). The actual work is an injected `executor` coroutine, so the
runner's mechanics — concurrency, **immediate cascading cancel**, progress, and
deliverables-gated completion — are independent of the LLM and fully testable.

Completion is *verified*: after the executor returns, a task is marked COMPLETED
only if `TaskStore.is_complete` holds (all deliverables satisfied + all subtasks
done); otherwise it's FAILED with the unmet deliverables. Cancelling a task
cancels its in-flight asyncio task and every descendant's, immediately.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from agclaw.tasks.model import TaskStatus
from agclaw.tasks.store import TaskStore

# executor(task_id, manager, asker) -> does the work (produces deliverables,
# reports progress, may spawn subtasks). The manager handles status + gating.
Executor = Callable[[str, "TaskManager", object], Awaitable[None]]


class TaskManager:
    def __init__(
        self,
        store: TaskStore,
        executor: Executor,
        max_concurrent: int = 3,
        on_progress: Callable | None = None,
    ) -> None:
        self.store = store
        self.executor = executor
        self._sem = asyncio.Semaphore(max_concurrent)
        self._running: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()
        self._on_progress = on_progress

    async def submit(self, task_id: str, asker=None) -> asyncio.Task:
        """Start (or return the already-running) background run for a task."""
        if task_id in self._running:
            return self._running[task_id]
        self._cancelled.discard(task_id)
        t = asyncio.create_task(self._run(task_id, asker))
        self._running[task_id] = t
        return t

    async def _run(self, task_id: str, asker) -> None:
        try:
            async with self._sem:
                if task_id in self._cancelled:
                    await self.store.set_status(task_id, TaskStatus.CANCELLED)
                    return
                await self.store.set_status(task_id, TaskStatus.RUNNING)
                await self.executor(task_id, self, asker)

                if task_id in self._cancelled:
                    await self.store.set_status(task_id, TaskStatus.CANCELLED)
                    return
                if await self.store.is_complete(task_id):
                    await self.store.set_status(task_id, TaskStatus.COMPLETED)
                else:
                    task = await self.store.get(task_id)
                    pending = [d.get("description") for d in task.pending_deliverables()]
                    await self.store.set_status(
                        task_id, TaskStatus.FAILED,
                        error=f"deliverables not met: {pending}" if pending
                        else "incomplete (subtasks unfinished)",
                    )
        except asyncio.CancelledError:
            await self.store.set_status(task_id, TaskStatus.CANCELLED)
            raise
        except Exception as exc:  # the work blew up
            await self.store.set_status(task_id, TaskStatus.FAILED, error=str(exc))
        finally:
            self._running.pop(task_id, None)

    async def progress(self, task_id: str, message: str, pct: int | None = None) -> None:
        """Record a progress entry and notify any live subscriber (GUI/channel)."""
        await self.store.add_progress(task_id, message, pct)
        if self._on_progress is not None:
            try:
                res = self._on_progress(task_id, message, pct)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass

    async def cancel(self, task_id: str, reason: str = "cancelled") -> None:
        """Cancel a task and **all its descendants** immediately (stop work now)."""
        ids = [task_id] + [d.id for d in await self.store.descendants(task_id)]
        for tid in ids:
            self._cancelled.add(tid)
            running = self._running.get(tid)
            if running is not None and not running.done():
                running.cancel()  # stop in-flight work immediately
            task = await self.store.get(tid)
            if task is not None and not task.is_terminal:
                await self.store.set_status(tid, TaskStatus.CANCELLED, error=reason)

    async def wait(self, task_id: str) -> None:
        """Await a task's background run to settle (for tests / orchestration)."""
        running = self._running.get(task_id)
        if running is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await running

    def is_running(self, task_id: str) -> bool:
        return task_id in self._running

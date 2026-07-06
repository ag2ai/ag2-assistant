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

from assistant.tasks.model import TaskStatus
from assistant.tasks.store import TaskStore

# executor(task_id, manager, asker) -> does the work (produces deliverables,
# reports progress, may spawn subtasks). The manager handles status + gating.
Executor = Callable[[str, "TaskManager", object], Awaitable[None]]


class TaskManager:
    # How many times a leaf may run its executor while deliverables stay unmet
    # before the task is failed (prevents infinite rework spins).
    MAX_ATTEMPTS = 3

    def __init__(
        self,
        store: TaskStore,
        executor: Executor,
        max_concurrent: int = 3,
        on_progress: Callable | None = None,
        on_status: Callable | None = None,
        on_event: Callable | None = None,
        on_deliverable: Callable | None = None,
        inquiry_store=None,
    ) -> None:
        self.store = store
        self.executor = executor
        self._sem = asyncio.Semaphore(max_concurrent)
        self._running: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()
        # Per-task attempt bookkeeping (current 1-based attempt, max attempts) so
        # the executor can ask whether it's on its FINAL attempt and escalate to
        # the stronger model. Set just before each executor call; cleared on settle.
        self._attempts: dict[str, tuple[int, int]] = {}
        self._on_progress = on_progress
        # Notified on every lifecycle transition (RUNNING / terminal) so the
        # service can emit the matching AG2 task event onto the task's stream.
        self._on_status = on_status
        # Raw AG2 events emitted by nested worker/subagent runs. These ride
        # the task stream without affecting durable task status.
        self._on_event = on_event
        # Notified when a deliverable is produced (the executor calls
        # deliverable_produced) → DeliverableProduced event on the task's stream.
        self._on_deliverable = on_deliverable
        # When set, HITL prompts during a task are persisted as durable Inquiries
        # tied to the (sub)task, so they survive restarts and can be answered from
        # any channel. None → transient asking, exactly as before.
        self._inquiry_store = inquiry_store

    def _bind_asker(self, asker, task_id: str):
        """Bind the asker to this (sub)task so its prompts are tagged with the id.

        A `DurableAsker` is rebound (sharing its transport, so a sub-agent's
        question still bubbles to the same surface); a plain transport asker is
        wrapped when an inquiry store is configured; otherwise it's passed through
        unchanged (preserving the no-durability path)."""
        if asker is None:
            return None
        if hasattr(asker, "rebind"):
            return asker.rebind(task_id)
        if self._inquiry_store is not None:
            from assistant.hitl.inquiry import DurableAsker

            return DurableAsker(asker, self._inquiry_store, task_id=task_id)
        return asker

    async def _mark(self, task_id: str, status: str, error: str = "") -> None:
        """Set status and notify the lifecycle hook (the single transition point)."""
        if error:
            await self.store.set_status(task_id, status, error=error)
        else:
            await self.store.set_status(task_id, status)
        if self._on_status is not None:
            try:
                res = self._on_status(task_id, status, error)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed("task status callback", exc, task_id=task_id, status=status)

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
            await self._mark(task_id, TaskStatus.RUNNING)
            attempts = 0
            last_crash = ""  # reason the most recent executor attempt RAISED (if any)
            ran_clean = False  # did any attempt return without raising?
            while True:
                if task_id in self._cancelled:
                    await self._mark(task_id, TaskStatus.CANCELLED)
                    return

                # 1) Run any pending subtasks first, in parallel. We do NOT hold a
                #    worker slot here — only leaf work counts toward concurrency —
                #    so deep trees can't deadlock. Re-evaluated each pass, so
                #    subtasks added mid-run (amendments) are picked up.
                children = await self.store.children(task_id)
                pending_children = [c for c in children if not c.is_terminal]
                if pending_children:
                    # Run subtasks in parallel. A subtask that FAILS does NOT
                    # abort the parent: its failure is fed to the parent as
                    # context (see executor) and the parent's own deliverable
                    # verification decides the outcome — so a useful result that
                    # works around the gap still completes. (A pure orchestrator
                    # with no deliverables of its own still fails if a subtask
                    # did — see TaskStore.is_complete.)
                    await asyncio.gather(
                        *[self._run_subtree(c.id, asker) for c in pending_children]
                    )
                    continue  # re-evaluate: new children may have appeared (amendments)

                # 2) No pending subtasks → do this task's own work toward its
                #    deliverables (the leaf, or a parent's synthesis step).
                task = await self.store.get(task_id)
                needs_work = bool(task.pending_deliverables()) or (
                    not task.deliverables and not children and attempts == 0
                )
                if needs_work and attempts < self.MAX_ATTEMPTS:
                    attempts += 1
                    async with self._sem:
                        if task_id in self._cancelled:
                            await self._mark(task_id, TaskStatus.CANCELLED)
                            return
                        # Publish attempt state so the executor can escalate the
                        # model on the final attempt (current_attempt()).
                        self._attempts[task_id] = (attempts, self.MAX_ATTEMPTS)
                        try:
                            await self.executor(task_id, self, self._bind_asker(asker, task_id))
                            ran_clean = True
                            last_crash = ""
                        except asyncio.CancelledError:
                            raise  # cancellation is not a crash — settle as CANCELLED
                        except Exception as exc:
                            # A crashed attempt (e.g. LLMCallTimeout after retries)
                            # is ONE consumed attempt, not task death: record it and
                            # let the loop try again (or FAIL when exhausted). This
                            # is the incident's second failure mode — a wedged
                            # synthesis turn used to kill the whole task outright.
                            last_crash = str(exc) or exc.__class__.__name__
                            await self.progress(
                                task_id, f"attempt {attempts} crashed: {last_crash}"
                            )
                            await self._note_crash(task_id, attempts, last_crash)
                    continue  # re-check (may have produced deliverables / added subtasks)

                # 3) Settle: complete iff deliverables satisfied + subtasks done. A
                #    task whose only run(s) crashed (no clean run, nothing else to be
                #    judged on) is NOT complete even when it has no deliverables.
                complete = await self.store.is_complete(task_id)
                if complete and (ran_clean or task.deliverables or children):
                    await self._mark(task_id, TaskStatus.COMPLETED)
                else:
                    await self._mark(
                        task_id,
                        TaskStatus.FAILED,
                        error=self._failure_reason(task, attempts, last_crash, children),
                    )
                return
        except asyncio.CancelledError:
            await self._mark(task_id, TaskStatus.CANCELLED)
            raise
        except Exception as exc:  # the runner mechanics themselves blew up
            await self._mark(task_id, TaskStatus.FAILED, error=str(exc))
        finally:
            self._attempts.pop(task_id, None)
            self._running.pop(task_id, None)

    def current_attempt(self, task_id: str) -> tuple[int, int]:
        """The (attempt, max_attempts) of the executor call in flight for `task_id`.

        1-based; ``attempt == max_attempts`` means this is the FINAL attempt (the
        executor escalates to the stronger model there). ``(0, MAX_ATTEMPTS)`` when
        no attempt is in flight (defensive default for callers outside the loop)."""
        return self._attempts.get(task_id, (0, self.MAX_ATTEMPTS))

    def is_final_attempt(self, task_id: str) -> bool:
        """True while the executor is on its last permitted attempt for `task_id`."""
        attempt, max_attempts = self.current_attempt(task_id)
        return attempt >= max_attempts

    async def _note_crash(self, task_id: str, attempt: int, reason: str) -> None:
        """Annotate pending deliverables with the crash so the NEXT attempt's prompt
        carries it (same channel the verifier's rejection note uses), and a failed
        task shows why. Best-effort: a note write must never mask the crash."""
        note = f"attempt {attempt} crashed before producing this: {reason}"
        task = await self.store.get(task_id)
        if task is None:
            return
        for d in task.pending_deliverables():
            try:
                await self.store.set_deliverable_status(
                    task_id, d["id"], d.get("status", "pending"), notes=note
                )
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed("crash note write", exc, task_id=task_id)

    def _failure_reason(self, task, attempts: int, last_crash: str, children) -> str:
        """Distinguish a crash-flavoured failure from a verification rejection."""
        pending = [d.get("description") for d in task.pending_deliverables()]
        if last_crash:
            if pending:
                return (
                    f"deliverables not produced after {attempts} attempt(s); "
                    f"last attempt crashed: {last_crash} — pending: {pending}"
                )
            return f"crashed after {attempts} attempt(s): {last_crash}"
        if pending:
            return f"deliverables not met after {attempts} attempt(s): {pending}"
        return "incomplete (subtasks unfinished)"

    async def _run_subtree(self, task_id: str, asker) -> None:
        """Submit a subtask and await its background run to settle."""
        task = await self.submit(task_id, asker)
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def progress(self, task_id: str, message: str, pct: int | None = None) -> None:
        """Record a progress entry and notify any live subscriber (GUI/channel)."""
        await self.store.add_progress(task_id, message, pct)
        if self._on_progress is not None:
            try:
                res = self._on_progress(task_id, message, pct)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed("task progress callback", exc, task_id=task_id)

    async def emit_event(self, task_id: str, event) -> None:
        """Forward an AG2-native event onto this task's live stream."""
        if self._on_event is not None:
            try:
                res = self._on_event(task_id, event)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed(
                    "task event callback", exc, task_id=task_id, event=type(event).__name__
                )

    async def deliverable_produced(
        self, task_id: str, deliverable_id: str, description: str, preview: str = ""
    ) -> None:
        """Called by the executor when a deliverable is produced; notifies the hook."""
        if self._on_deliverable is not None:
            try:
                res = self._on_deliverable(task_id, deliverable_id, description, preview)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed(
                    "task deliverable callback",
                    exc,
                    task_id=task_id,
                    deliverable_id=deliverable_id,
                )

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
                await self._mark(tid, TaskStatus.CANCELLED, error=reason)
            # release any HITL prompt still blocking on this task (it would
            # otherwise hang until timeout); answering it is now moot.
            if self._inquiry_store is not None:
                await self._inquiry_store.cancel_for_task(tid)

    async def wait(self, task_id: str) -> None:
        """Await a task's background run to settle (for tests / orchestration)."""
        running = self._running.get(task_id)
        if running is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await running

    def is_running(self, task_id: str) -> bool:
        return task_id in self._running

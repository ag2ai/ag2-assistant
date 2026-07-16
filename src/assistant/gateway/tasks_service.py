"""Task service for the gateway — wires the task subsystem behind a small API.

Owns the durable `TaskStore` + `InquiryStore`, a `TaskManager`, and a planner
agent, and exposes the operations the REST layer needs: submit a request (intake
runs in the background, asking clarifying questions as durable inquiries), list /
inspect / cancel tasks, and list / answer inquiries from any channel.

HITL for tasks has no live socket of its own — questions are persisted as
inquiries and answered out of band (GUI / REST). The transport asker is a
`_ParkingAsker` that just blocks; `DurableAsker` races it against the inquiry
store, so resolution always arrives via `InquiryStore.answer()`.
"""

import asyncio
from collections.abc import Callable

from assistant.config import Config, load_config
from assistant.hitl import NullAsker
from assistant.tasks.scheduling import describe_cron

_CONTROL_PROMPT = (
    "You manage ONE task for the user. When they ask for a change — add or cancel "
    "a subtask, change the objective, add/remove/replace a deliverable, reschedule it "
    "(change when it runs or how it repeats), or cancel the task — use your tools to do it "
    "immediately (it's their task; don't ask permission), then confirm in one short "
    "sentence what you changed. When the user RELAXES or CHANGES a requirement (e.g. 'any "
    "style instead of watercolor', 'just one image'), REPLACE the deliverables — use "
    "set_deliverables to reset the list, or remove_deliverable to drop a stale one (call "
    "task_status for the ids). Do NOT add_deliverable on top of the old requirement, or the "
    "task accumulates requirements and produces duplicate outputs. To change WHEN or HOW OFTEN it runs (e.g. 'make it "
    "weekdays', 'move to 8am', 'stop repeating') use the reschedule tool — never add "
    "a subtask for a scheduling change. Compute the ISO time from the current "
    "date/time in your environment context. For questions about progress or status, "
    "read the task and answer concisely. You do NOT do the research or work yourself "
    "— the task runner does that; you only steer this task."
)


# A transport asker with no live channel — blocks until the inquiry is answered
# out of band. Same as the public NullAsker; aliased for the existing local name.
_ParkingAsker = NullAsker


class TaskService:
    """The gateway's task subsystem: stores, runner, planner, and intake driver."""

    def __init__(
        self,
        config: Config | None = None,
        store=None,
        inquiry_store=None,
        manager=None,
        planner_agent=None,
        executor=None,
        max_concurrent: int = 3,
        scheduler_interval: float = 30.0,
        config_factory: Callable[[], Config] | None = None,
    ) -> None:
        self._config = config or load_config()
        # How reload() re-resolves config (a profile runtime's factory re-reads the
        # profile's registry entry + settings; the default is load_config).
        self._config_factory = config_factory or load_config
        self._store = store
        self._inquiries = inquiry_store
        self._manager = manager
        self._planner = planner_agent
        self._executor = executor
        self._max_concurrent = max_concurrent
        self._scheduler_interval = scheduler_interval
        self._scheduler = None
        self._scheduler_lock = None
        self._bg: set[asyncio.Task] = set()
        # Bounded background pipeline that distils each completed recurring run into a
        # cached digest (see tasks/history.py). A queue + fixed worker pool caps
        # concurrency so a burst of completions can't fan out unbounded LLM calls;
        # overflow is dropped (safe — the run still shows via its stub). `_digest_inflight`
        # coalesces duplicate enqueues (completion vs. backfill) of the same run.
        self._digest_q: asyncio.Queue | None = None
        self._digest_workers: list[asyncio.Task] = []
        self._digest_inflight: set[str] = set()
        self._control_agents: dict = {}  # task_id -> (agent, stream) for task chat
        # Async (chat_id, event) -> None, wired by the gateway. Lets a task's
        # lifecycle ride the AG2 stream so the GUI renders it as events. None → off.
        self._emit = None

    @property
    def scheduler_running(self) -> bool:
        """True when THIS process owns the singleton scheduler (won the flock).
        Other processes run without one — the scheduler is single-leader, so a
        False here means "scheduling happens, just in another process", not down."""
        return self._scheduler is not None

    def set_emitter(self, emitter) -> None:
        """Wire an async ``(chat_id, event)`` emitter (the gateway's)."""
        self._emit = emitter

    async def _emit_status(self, task_id: str, status: str, error: str = "") -> None:
        """Translate a lifecycle transition into the matching AG2 task event and
        emit it onto the task's stream (``task:<id>``). Reuses AG2-native events;
        intermediate statuses (pending/planning) carry no event — the GUI reads
        those from the task panel."""
        # Record run history first — independent of the GUI emitter, so headless /
        # cron runs still build history. Only *enqueues* (never awaits the digest),
        # so it can't delay completion or the TaskCompleted event.
        await self._maybe_enqueue_digest(task_id, status)
        if self._emit is None:
            return
        from ag2.events import (
            TaskCancelled,
            TaskCompleted,
            TaskFailed,
            TaskStarted,
        )

        from assistant.tasks import TaskStatus

        t = await self._store.get(task_id)
        if t is None:
            return
        obj, name = (t.objective or t.title or ""), "ag2-assistant"
        ev = None
        if status == TaskStatus.RUNNING:
            ev = TaskStarted(task_id=task_id, agent_name=name, objective=obj)
        elif status == TaskStatus.COMPLETED:
            ev = TaskCompleted(
                task_id=task_id,
                agent_name=name,
                objective=obj,
                result=(t.result or ""),
                task_stream=f"task:{task_id}",
            )
        elif status == TaskStatus.FAILED:
            ev = TaskFailed(
                task_id=task_id,
                agent_name=name,
                objective=obj,
                error=Exception(error or t.error or "failed"),
            )
        elif status == TaskStatus.CANCELLED:
            ev = TaskCancelled(task_id=task_id, agent_name=name, objective=obj, reason=error or "")
        if ev is not None:
            try:
                await self._emit(f"task:{task_id}", ev)
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed("task lifecycle event emit", exc, task_id=task_id, status=status)

    # --- recurring-run history: bounded, best-effort digest pipeline ------------ #

    def _enqueue_digest(self, run_id: str) -> None:
        """Queue a completed occurrence-root run for digesting. Non-blocking; on a full
        queue it drops with a warning (safe — the run still shows via its stub)."""
        if self._digest_q is None or run_id in self._digest_inflight:
            return
        try:
            self._digest_q.put_nowait(run_id)
            self._digest_inflight.add(run_id)
        except asyncio.QueueFull:
            import logging

            logging.getLogger("ag2assistant.tasks").warning(
                "digest queue full; skipping digest for %s (stub still used)", run_id
            )

    async def _maybe_enqueue_digest(self, task_id: str, status: str) -> None:
        """Enqueue a digest when an occurrence root completes (parent_id None + run_of)."""
        from assistant.tasks import TaskStatus

        if status != TaskStatus.COMPLETED or self._digest_q is None:
            return
        try:
            t = await self._store.get(task_id)
        except Exception:
            return
        if t is not None and t.parent_id is None and getattr(t, "run_of", None):
            self._enqueue_digest(task_id)

    async def _run_outputs(self, run) -> list[str]:
        """Verified deliverable contents for a run + its descendants (for the digest)."""
        outs: list[str] = []
        try:
            tasks = [run] + await self._store.descendants(run.id)
        except Exception:
            tasks = [run]
        for t in tasks:
            for d in getattr(t, "deliverables", None) or []:
                content = (d.get("asset") or {}).get("content")
                if content:
                    outs.append(content)
        return outs

    async def _record_history(self, run_id: str) -> None:
        """Distil one completed run into its per-template digest cache. Best-effort."""
        from assistant.tasks import history

        run = await self._store.get(run_id)
        template_id = getattr(run, "run_of", None) if run is not None else None
        if run is None or not template_id:
            return
        km = history.episodic_store_for(self._config, template_id)
        outputs = await self._run_outputs(run)
        await history.record_run_digest(self._config, km, run, outputs)

    async def _digest_worker(self) -> None:
        """Pull run ids and digest them, one at a time per worker, each time-boxed."""
        import logging

        assert self._digest_q is not None
        while True:
            run_id = await self._digest_q.get()
            try:
                await asyncio.wait_for(
                    self._record_history(run_id),
                    timeout=self._config.tasks.digest_timeout_s,
                )
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                logging.getLogger("ag2assistant.tasks").warning(
                    "digest timed out for %s (stub still used)", run_id
                )
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed("digest worker", exc, task_id=run_id)
            finally:
                self._digest_inflight.discard(run_id)
                self._digest_q.task_done()

    async def _backfill_missing_digests(self) -> None:
        """Regenerate digests for completed occurrence roots whose episode is missing
        (dropped/cancelled by a prior shutdown), so a thin recap self-heals. Bounded:
        recent window per template, enqueued on the same overflow-safe queue."""
        from collections import defaultdict

        from assistant.tasks import TaskStatus, history

        # Wait before the first scan so startup never races other store users — same
        # rationale as Scheduler's delayed first tick (a TaskStore op on this loop
        # while another loop holds the shared lock deadlocks cross-loop).
        try:
            await asyncio.sleep(self._scheduler_interval)
        except asyncio.CancelledError:
            return
        try:
            all_tasks = await self._store.list_all()
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("digest backfill scan", exc)
            return
        by_template: dict[str, list] = defaultdict(list)
        for t in all_tasks:
            if (
                t.parent_id is None
                and getattr(t, "run_of", None)
                and t.status == TaskStatus.COMPLETED
            ):
                by_template[t.run_of].append(t)
        window = max(1, self._config.tasks.history_runs) * 3
        for template_id, runs in by_template.items():
            runs.sort(key=history.run_instant)  # oldest-first
            km = history.episodic_store_for(self._config, template_id)
            for r in runs[-window:]:
                if r.id in self._digest_inflight:
                    continue
                if await history.has_episode(km, r):
                    continue
                self._enqueue_digest(r.id)

    async def _emit_deliverable(
        self, task_id, deliverable_id, description, preview="", path=""
    ) -> None:
        """A produced deliverable → DeliverableProduced on the task's stream."""
        if self._emit is None:
            return
        from assistant.events import DeliverableProduced

        try:
            await self._emit(
                f"task:{task_id}",
                DeliverableProduced(
                    task_id,
                    deliverable_id=deliverable_id,
                    description=description,
                    preview=preview,
                    path=path,
                ),
            )
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed(
                "deliverable event emit",
                exc,
                task_id=task_id,
                deliverable_id=deliverable_id,
            )

    async def _emit_task_event(self, task_id: str, event) -> None:
        """Forward raw AG2 subagent events onto the durable task stream."""
        if self._emit is None:
            return
        try:
            await self._emit(f"task:{task_id}", event)
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("task raw event emit", exc, task_id=task_id, event=type(event).__name__)

    async def _emit_inquiry(self, inquiry, kind) -> None:
        """Durable HITL lifecycle → InquiryRaised/InquiryAnswered on its stream.
        (AG2's HumanInputRequest is transient; our inquiries are durable.) The
        target stream is the inquiry's `chat` — a task page (`task:<id>`) or a
        chat — so the question renders inline wherever it was raised."""
        if self._emit is None:
            return
        from assistant.events import InquiryAnswered, InquiryRaised
        from assistant.hitl.inquiry import InquiryStatus

        sid = inquiry.chat or (f"task:{inquiry.task_id}" if inquiry.task_id else None)
        if not sid:
            return
        try:
            if kind == "raised":
                await self._emit(
                    sid,
                    InquiryRaised(
                        inquiry.id,
                        task_id=inquiry.task_id,
                        question=inquiry.text,
                        detail=getattr(inquiry, "detail", "") or "",
                        options=list(inquiry.options or []),
                        kind=inquiry.kind,
                    ),
                )
            elif kind in InquiryStatus.TERMINAL:
                # Every terminal transition (answered / expired / cancelled) retires
                # the card — a timed-out or cancelled prompt must stop rendering live
                # buttons, so we surface the resolution, not only real answers.
                await self._emit(
                    sid,
                    InquiryAnswered(
                        inquiry.id,
                        answer=getattr(inquiry, "answer", "") or "",
                        status=kind,
                    ),
                )
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("inquiry event emit", exc, inquiry_id=inquiry.id, kind=kind, chat=sid)

    async def start(self, *, scheduler: bool = True) -> None:
        """Build the durable stores + runner (cheap; no LLM agent yet).

        Task tools are always wired; ``scheduler=False`` (channel commands) skips
        the polling loop so only one process ticks the shared ``tasks.db``. A
        cross-process lock enforces a single live scheduler even with ``True``.
        """
        from assistant.hitl import InquiryStore
        from assistant.tasks import TaskManager, TaskStore, make_task_executor

        d = self._config.data_dir
        d.mkdir(parents=True, exist_ok=True)
        if self._store is None:
            self._store = TaskStore(path=d / "tasks.db")
        if self._inquiries is None:
            self._inquiries = InquiryStore(
                path=d / "inquiries.db",
                on_change=self._emit_inquiry,
            )
        if self._executor is None:
            self._executor = make_task_executor(self._config)
        if self._manager is None:
            self._manager = TaskManager(
                self._store,
                self._executor,
                max_concurrent=self._max_concurrent,
                inquiry_store=self._inquiries,
                on_status=self._emit_status,  # lifecycle → AG2 task events
                on_event=self._emit_task_event,  # nested AG2 events → task stream
                on_deliverable=self._emit_deliverable,  # → DeliverableProduced
            )
        # Bounded digest workers run in every process that completes tasks (a task's
        # completion fires _emit_status in the process that ran it).
        if self._digest_q is None:
            self._digest_q = asyncio.Queue(maxsize=self._config.tasks.digest_queue_max)
            self._digest_workers = [
                asyncio.create_task(self._digest_worker())
                for _ in range(max(1, self._config.tasks.digest_concurrency))
            ]
        if scheduler and self._scheduler is None:
            from assistant.scheduler_lock import SchedulerLock
            from assistant.tasks.scheduling import Scheduler

            lock = SchedulerLock(d / "scheduler.lock")
            if lock.acquire():
                self._scheduler_lock = lock
                self._scheduler = Scheduler(
                    self._store, self._fire, interval=self._scheduler_interval
                )
                await self._scheduler.start()
                # Backfill digests missing from a prior shutdown/drop — only in the
                # single scheduler-owning process, to avoid cross-process double work.
                bf = asyncio.create_task(self._backfill_missing_digests())
                self._bg.add(bf)
                bf.add_done_callback(self._bg.discard)
            else:
                import logging

                logging.getLogger("ag2assistant.tasks").info(
                    "scheduler not started — another process already owns %s",
                    d / "scheduler.lock",
                )

    async def reload(self) -> None:
        """Rebuild the planner + executor from fresh config/keys after a settings
        change. The manager's executor reference is swapped so new runs use it while
        in-flight runs (tracked in the manager) finish on the old one; the planner is
        reset for a lazy rebuild. Stores and the scheduler are unaffected."""
        from assistant.tasks import make_task_executor

        self._config = self._config_factory()
        self._planner = None  # rebuilt lazily by _planner_agent() with fresh config
        self._executor = make_task_executor(self._config)
        if self._manager is not None:
            self._manager.executor = self._executor

    @property
    def store(self):
        return self._store

    @property
    def inquiries(self):
        return self._inquiries

    def _planner_agent(self):
        # Built lazily on first use so merely starting the gateway never
        # constructs an LLM agent (keeps unit tests / idle startup light).
        if self._planner is None:
            from assistant.agent import create_agent

            self._planner = create_agent(self._config, memory=False, skills=False)
        return self._planner

    async def _prepare_and_run(self, task_id: str, channel: str, clarify: bool = True) -> None:
        """Intake then hand the task to the runner.

        `clarify=True` (interactive) asks clarifying questions via durable HITL.
        Scheduled/unattended runs pass `clarify=False` — there's no one to answer
        at run time, so we plan best-effort rather than abandoning the run; the
        task still gets durable HITL for any execution-time permission prompt.
        """
        from assistant.hitl import DurableAsker
        from assistant.tasks import TaskStatus
        from assistant.tasks.planner import prepare_task
        from assistant.tools import available_capabilities

        try:
            intake_asker = (
                DurableAsker(_ParkingAsker(), self._inquiries, task_id=task_id, channel=channel)
                if clarify
                else None
            )
            await prepare_task(
                self._store,
                task_id,
                self._planner_agent(),
                asker=intake_asker,
                capabilities=available_capabilities(),
            )
            cur = await self._store.get(task_id)
            if cur is not None and not cur.is_terminal:
                # the runner re-binds the parking asker per (sub)task.
                await self._manager.submit(task_id, asker=_ParkingAsker())
        except Exception as exc:
            await self._store.set_status(
                task_id, TaskStatus.FAILED, error=f"intake/submit error: {exc}"
            )

    def _run_in_bg(self, task_id: str, channel: str, clarify: bool = True) -> None:
        bg = asyncio.create_task(self._prepare_and_run(task_id, channel, clarify))
        self._bg.add(bg)
        bg.add_done_callback(self._bg.discard)

    async def submit_request(self, text: str, channel: str = "web") -> str:
        """Create a task and drive intake + run in the background; return its id."""
        task = await self._store.create(text, origin_channel=channel, hitl_channel=channel)
        self._run_in_bg(task.id, channel)
        return task.id

    async def schedule_task(
        self,
        text: str,
        when: str,
        recurrence: str | None = None,
        channel: str = "web",
    ) -> str:
        """Schedule a task for `when` (ISO datetime), optionally recurring.

        Clarification + planning happen NOW (while the user is here), so the plan
        is baked into the task; the deterministic Scheduler then just *executes*
        that plan at each occurrence — no run-time questions."""
        from assistant.tasks import TaskStatus
        from assistant.tasks.scheduling import first_occurrence

        # recurring tasks snap to the first cron match (e.g. weekday-only crons
        # scheduled on a Saturday start Monday)
        first = first_occurrence(recurrence, when)
        if first is not None:
            when = first.isoformat()
        task = await self._store.create(
            text,
            origin_channel=channel,
            hitl_channel=channel,
            status=TaskStatus.SCHEDULED,
            scheduled_for=when,
            recurrence=recurrence or None,
        )
        if self._emit is not None:
            from assistant.events import TaskScheduled

            try:
                await self._emit(
                    f"task:{task.id}",
                    TaskScheduled(
                        task.id,
                        scheduled_for=when,
                        recurrence=recurrence or "",
                        recurrence_desc=describe_cron(recurrence) or "",
                    ),
                )
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed("scheduled task event emit", exc, task_id=task.id)
        bg = asyncio.create_task(self._plan_for_schedule(task.id, channel, when))
        self._bg.add(bg)
        bg.add_done_callback(self._bg.discard)
        return task.id

    async def _plan_for_schedule(self, task_id: str, channel: str, when: str) -> None:
        """Run intake (clarify + plan) up front, then re-arm the task as SCHEDULED."""
        from assistant.hitl import DurableAsker
        from assistant.tasks import TaskStatus
        from assistant.tasks.planner import prepare_task
        from assistant.tools import available_capabilities

        try:
            asker = DurableAsker(
                _ParkingAsker(),
                self._inquiries,
                task_id=task_id,
                channel=channel,
            )
            await prepare_task(
                self._store,
                task_id,
                self._planner_agent(),
                asker=asker,
                capabilities=available_capabilities(),
            )
            # prepare_task leaves it PENDING (or CANCELLED if abandoned); arm it.
            cur = await self._store.get(task_id)
            if cur is not None and cur.status == TaskStatus.PENDING:
                await self._store.update(
                    task_id,
                    status=TaskStatus.SCHEDULED,
                    scheduled_for=when,
                )
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("scheduled task upfront planning", exc, task_id=task_id)
            # Planning is best-effort; the run can still plan on fire as a fallback.

    async def _clone_subtree(self, src, new_parent_id: str) -> None:
        child = await self._store.add_subtask(
            new_parent_id,
            src.title,
            src.description,
            reopen_parent=False,
            capabilities=src.capabilities,
            objective=src.objective,
        )
        for d in src.deliverables or []:
            await self._store.add_deliverable(
                child.id, d.get("description", ""), d.get("criteria", "")
            )
        for gc in await self._store.children(src.id):
            await self._clone_subtree(gc, child.id)

    async def _clone_for_run(self, template, parent_id: str | None = None):
        """A fresh, unplanned-status copy of a planned task tree (deliverables reset,
        no assets) — one occurrence's run, with the template's baked-in plan. The new
        run is grouped under `parent_id` (defaults to the template itself)."""
        run = await self._store.create(
            template.title,
            description=template.description,
            objective=template.objective,
            capabilities=template.capabilities,
            origin_channel=template.origin_channel,
            hitl_channel=template.hitl_channel,
            run_of=parent_id or template.id,  # group as one occurrence
        )
        for d in template.deliverables or []:
            await self._store.add_deliverable(
                run.id, d.get("description", ""), d.get("criteria", "")
            )
        for child in await self._store.children(template.id):
            await self._clone_subtree(child, run.id)
        return run

    @staticmethod
    def _is_planned(task) -> bool:
        return bool(task.deliverables) or bool(task.plan)

    async def _fire(self, task_id: str) -> None:
        """Scheduler callback: execute a due task's prepared plan; re-arm recurring."""
        from datetime import datetime

        from assistant.tasks import TaskStatus
        from assistant.tasks.scheduling import next_occurrence

        t = await self._store.get(task_id)
        if t is None or t.status != TaskStatus.SCHEDULED:
            return
        now = datetime.now().astimezone()
        channel = t.origin_channel or "web"
        if t.recurrence:
            run = await self._clone_for_run(t)
            if self._is_planned(t):
                await self._manager.submit(run.id, asker=_ParkingAsker())  # execute the plan
            else:  # never planned (e.g. abandoned intake) → best-effort, no questions
                self._run_in_bg(run.id, channel, clarify=False)
            nxt = next_occurrence(t.recurrence, now)
            if nxt is not None:
                await self._store.update(task_id, scheduled_for=nxt.isoformat())
            else:  # unparseable recurrence → fire once
                await self._store.set_status(task_id, TaskStatus.COMPLETED)
        else:
            await self._store.set_status(task_id, TaskStatus.PENDING)  # leave SCHEDULED
            if self._is_planned(t):
                await self._manager.submit(task_id, asker=_ParkingAsker())
            else:
                self._run_in_bg(task_id, channel, clarify=False)

    # status groupings used by the listing filters
    _ACTIVE = {"pending", "scheduled", "awaiting_input", "planning", "running"}
    _STOPPED = {"failed", "cancelled"}

    async def list_tasks(self) -> list[dict]:
        """Active/recent top-level tasks for the drawer, each carrying any pending
        inquiries in its subtree. Needs-input first, then newest."""
        # One store scan; children/descendants come from the in-memory map (O(N)).
        all_tasks = await self._store.list_all()
        kids_map = self._children_map(all_tasks)
        roots = [t for t in all_tasks if t.parent_id is None]

        by_task: dict[str, list] = {}
        for inq in await self._inquiries.list_pending():
            by_task.setdefault(inq.task_id, []).append(inq)

        out = []
        for t in roots:
            s = await self._summary(t, kids_map)
            # subtree ids (self + all descendants), walked over the child map
            ids: set[str] = set()
            stack = [t.id]
            while stack:
                tid = stack.pop()
                if tid in ids:
                    continue
                ids.add(tid)
                stack.extend(k.id for k in kids_map.get(tid, []))
            s["inquiries"] = [self._inquiry_view(i) for tid in ids for i in by_task.get(tid, [])]
            out.append(s)

        out.sort(key=lambda s: s["created_at"], reverse=True)
        out.sort(key=lambda s: 0 if s["inquiries"] else 1)  # needs-input first (stable)
        return out

    async def list_all(self, status: str | None = None) -> list[dict]:
        """The full task history for the listing page, newest first. `status` filters:
        active / completed / stopped; None or 'all' = everything."""
        # One store scan; child counts come from the in-memory map (O(N)).
        all_tasks = await self._store.list_all()
        kids_map = self._children_map(all_tasks)
        roots = [t for t in all_tasks if t.parent_id is None]
        out = []
        for t in roots:
            if status == "active" and t.status not in self._ACTIVE:
                continue
            elif status == "completed" and t.status != "completed":
                continue
            elif status == "stopped" and t.status not in self._STOPPED:
                continue
            out.append(await self._summary(t, kids_map))
        out.sort(key=lambda s: s["created_at"], reverse=True)
        return out

    async def delete(self, task_id: str) -> tuple[bool, list[str]]:
        """Permanently delete a task and its whole subtree. If it's still running,
        cancel it and let the run settle first so the runner can't rewrite a record
        we're removing. Returns (ok, [deleted task ids]) — the caller purges each
        task's chat/event stream too. Irreversible; the GUI gates it behind a confirm.
        """
        t = await self._store.get(task_id)
        if t is None:
            return False, []
        ids = [task_id] + [d.id for d in await self._store.descendants(task_id)]
        if self._manager is not None and self._manager.is_running(task_id):
            await self._manager.cancel(task_id, reason="deleted")
            await self._manager.wait(task_id)  # settle before we remove the records
        for tid in ids:
            await self._store.delete(tid)
        return True, ids

    async def get_task(self, task_id: str) -> dict | None:
        """Full task detail with its subtree, deliverables (incl. assets), progress.
        A recurring template also lists the runs spawned from it (newest first), so
        its page shows what each occurrence actually did instead of a dead end."""
        t = await self._store.get(task_id)
        if t is None:
            return None
        node = await self._node(t, include_assets=True)
        runs = [r for r in await self._store.list_all() if getattr(r, "run_of", None) == task_id]
        runs.sort(key=lambda r: r.created_at or "", reverse=True)
        node["runs"] = [
            {
                "id": r.id,
                "status": r.status,
                "created_at": r.created_at,
                "ended_at": getattr(r, "ended_at", None),
            }
            for r in runs
        ]
        return node

    async def cancel(self, task_id: str, reason: str = "cancelled by user") -> bool:
        """Cancel a task and its whole subtree (also releases pending inquiries)."""
        if await self._store.get(task_id) is None:
            return False
        await self._manager.cancel(task_id, reason=reason)
        return True

    async def cancel_all(self, reason: str = "cancelled") -> int:
        """Cancel every non-terminal task via the cascading cancel path so state lands
        CANCELLED (not limbo). Used when archiving a profile (§4.9). Returns the count
        of top-level tasks cancelled."""
        if self._store is None or self._manager is None:
            return 0
        roots = [
            t for t in await self._store.list_all() if t.parent_id is None and not t.is_terminal
        ]
        for t in roots:
            await self._manager.cancel(t.id, reason=reason)
        return len(roots)

    # --- action wrappers (thin; the universal agent's system tools call these) ---

    async def add_subtask(self, task_id, title, description="", capabilities="web") -> str:
        from assistant.tasks.control import do_add_subtask

        return await do_add_subtask(
            self._store, self._manager, task_id, title, description, capabilities
        )

    async def add_deliverable(self, task_id, description, criteria="") -> str:
        from assistant.tasks.control import do_add_deliverable

        return await do_add_deliverable(self._store, self._manager, task_id, description, criteria)

    async def remove_deliverable(self, task_id, deliverable_id) -> str:
        from assistant.tasks.control import do_remove_deliverable

        return await do_remove_deliverable(self._store, self._manager, task_id, deliverable_id)

    async def set_deliverables(self, task_id, descriptions) -> str:
        from assistant.tasks.control import do_set_deliverables

        return await do_set_deliverables(self._store, self._manager, task_id, descriptions)

    async def set_objective(self, task_id, objective) -> str:
        from assistant.tasks.control import do_set_objective

        return await do_set_objective(self._store, task_id, objective)

    async def reschedule(self, task_id, when="", recurrence="") -> str:
        from assistant.tasks.control import do_reschedule

        return await do_reschedule(self._store, task_id, when, recurrence)

    async def cancel_target(self, task_id, subtask="") -> str:
        """Cancel the task or a named subtask (controller-style, returns a message)."""
        from assistant.tasks.control import do_cancel

        return await do_cancel(self._store, self._manager, task_id, subtask)

    async def run_now(self, task_id: str) -> str:
        """Run a scheduled task's occurrence immediately (keeping its schedule), or
        (re)run any other task now."""
        from assistant.tasks import TaskStatus

        t = await self._store.get(task_id)
        if t is None:
            return "Task not found."
        if t.status == TaskStatus.SCHEDULED:
            if self._is_planned(t):
                run = await self._clone_for_run(t)
                await self._manager.submit(run.id, asker=_ParkingAsker())
            else:
                run = await self._clone_for_run(t)
                self._run_in_bg(run.id, t.origin_channel or "web", clarify=False)
            return f"Running an occurrence of '{t.title}' now; its schedule is unchanged."
        self._run_in_bg(task_id, t.origin_channel or "web", clarify=False)
        return f"Running '{t.title}' now."

    async def rerun(self, task_id: str) -> dict:
        """Re-run a finished (failed/cancelled/completed) task from a clean start.

        Clones the task's planned tree into a FRESH run — new id, reset deliverables,
        no error, fresh timestamps, its own event stream — and executes it. The
        original record is left untouched as history. Returns ``{id}`` of the new run
        (or ``{error}``). Grouped as a sibling occurrence (under the recurring
        template if this was a run, else under the task itself)."""
        from assistant.tasks import TaskStatus

        t = await self._store.get(task_id)
        if t is None:
            return {"error": "Task not found."}
        if t.status not in TaskStatus.TERMINAL:
            return {"error": "Task is still active — cancel it before re-running."}
        parent_id = getattr(t, "run_of", None) or t.id
        run = await self._clone_for_run(t, parent_id=parent_id)
        if self._is_planned(t):
            await self._manager.submit(run.id, asker=_ParkingAsker())  # execute the plan
        else:  # never planned → best-effort, no questions
            self._run_in_bg(run.id, t.origin_channel or "web", clarify=False)
        return {"id": run.id}

    def _control(self, task_id: str):
        """A cached, task-scoped controller agent (+ its conversation stream)."""
        entry = self._control_agents.get(task_id)
        if entry is None:
            from ag2 import Agent
            from ag2.stream import MemoryStream

            from assistant.agent import model_config
            from assistant.tasks.control import build_task_tools

            agent = Agent(
                "task-controller",
                prompt=_CONTROL_PROMPT,
                config=model_config(self._config),
                tools=build_task_tools(self._store, self._manager, task_id),
            )
            entry = (agent, MemoryStream(id=f"taskctl:{task_id}"))
            self._control_agents[task_id] = entry
        return entry

    async def chat(self, task_id: str, text: str) -> str | None:
        """Converse about a task — the controller agent edits it via its tools."""
        from assistant.tasks.control import render_task

        if await self._store.get(task_id) is None:
            return None
        agent, stream = self._control(task_id)
        snapshot = await render_task(self._store, task_id)
        prompt = [_CONTROL_PROMPT, f"Current state of the task you manage:\n{snapshot}"]
        reply = await agent.ask(text, stream=stream, prompt=prompt)
        return reply.body

    async def pending_inquiries(self, task_id: str | None = None) -> list[dict]:
        items = await self._inquiries.list_pending(task_id)
        out = []
        for i in items:
            v = self._inquiry_view(i)
            root_id, title = await self._root_label(i.task_id)
            v["root_id"] = root_id  # open this to see the source task
            v["task_title"] = title  # so the user knows what they're answering
            out.append(v)
        return out

    async def _root_label(self, task_id: str | None) -> tuple[str | None, str]:
        """Resolve an inquiry's (sub)task to its root task's id + title."""
        if not task_id:
            return None, ""
        t = await self._store.get(task_id)
        if t is None:
            return task_id, ""
        seen: set[str] = set()
        while t.parent_id and t.parent_id not in seen:
            seen.add(t.id)
            parent = await self._store.get(t.parent_id)
            if parent is None:
                break
            t = parent
        return t.id, t.title

    async def answer_inquiry(self, inquiry_id: str, answer: str) -> bool:
        inq = await self._inquiries.answer(inquiry_id, answer)
        return inq is not None

    # ---- serialisation helpers ----

    @staticmethod
    def _deliverable_view(d: dict, include_asset: bool) -> dict:
        asset = d.get("asset") or {}
        return {
            "description": d.get("description", ""),
            "criteria": d.get("criteria", ""),
            "status": d.get("status", "pending"),
            "notes": d.get("notes", ""),
            "has_asset": bool(asset.get("content")),
            "asset": asset.get("content") if include_asset else None,
        }

    @staticmethod
    def _inquiry_view(i) -> dict:
        return {
            "id": i.id,
            "task_id": i.task_id,
            "chat": i.chat,  # stream it's on → strip matches it to the open page
            "kind": i.kind,
            "text": i.text,
            "detail": i.detail,
            "options": i.options,
            "created_at": i.created_at,
        }

    @staticmethod
    def _children_map(all_tasks: list) -> dict[str, list]:
        """parent_id -> [child Task, …], built once from a preloaded task list so
        task listing is O(N) instead of re-scanning the whole store per root."""
        m: dict[str, list] = {}
        for child in all_tasks:
            if child.parent_id:
                m.setdefault(child.parent_id, []).append(child)
        return m

    async def _summary(self, t, kids_map: dict[str, list] | None = None) -> dict:
        # Use the preloaded child map when supplied (O(N) listing); otherwise fall
        # back to a direct store query for single-task callers.
        if kids_map is not None:
            kids_count = len(kids_map.get(t.id, []))
        else:
            kids_count = len(await self._store.children(t.id))
        delivs = t.deliverables or []
        done = sum(1 for d in delivs if d.get("status") in ("produced", "accepted"))
        progress = t.progress or []
        return {
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "objective": t.objective or "",
            "created_at": t.created_at,
            "children": kids_count,
            "deliverables": len(delivs),
            "deliverables_done": done,
            "last_progress": progress[-1]["message"] if progress else None,
            "scheduled_for": t.scheduled_for,
            "recurrence": t.recurrence,
            "recurrence_desc": describe_cron(t.recurrence),
            "run_of": getattr(t, "run_of", None),
            "seen": getattr(t, "seen_at", None) is not None,
        }

    async def mark_seen(self, task_id: str) -> bool:
        """Record that the user has seen a *finished* task/run (clears its unread
        highlight and the chip's unread-results dot).

        Only stamps ``seen_at`` once the task is terminal: opening a task while it's
        still running is a progress peek, not seeing the result. Stamping on a peek
        would pre-empt the unread indicator that should fire when the task later
        completes (the reason a task could finish with no dot). Because peeks never
        write, ``seen_at`` is set iff the user opened the task after it finished, so
        the unread test stays a simple ``terminal && seen_at is None``.
        Idempotent — writes once, on the first open after finishing."""
        t = await self._store.get(task_id)
        if t is None:
            return False
        if t.is_terminal and getattr(t, "seen_at", None) is None:
            from datetime import datetime

            await self._store.update(task_id, seen_at=datetime.now().astimezone().isoformat())
        return True

    async def _node(self, t, include_assets: bool = False) -> dict:
        kids = await self._store.children(t.id)
        return {
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "objective": t.objective or "",
            "description": t.description or "",
            "created_at": t.created_at,
            "started_at": getattr(t, "started_at", None),
            "ended_at": getattr(t, "ended_at", None),
            "capabilities": t.capabilities or [],
            "scheduled_for": t.scheduled_for,
            "recurrence": t.recurrence,
            "recurrence_desc": describe_cron(t.recurrence),
            "run_of": getattr(t, "run_of", None),
            "intake": t.intake or {},
            "progress": t.progress or [],
            "error": t.error or "",
            "deliverables": [
                self._deliverable_view(d, include_assets) for d in (t.deliverables or [])
            ],
            "children": [await self._node(c, include_assets) for c in kids],
        }

    async def close(self) -> None:
        if self._scheduler is not None:
            await self._scheduler.stop()
        if self._scheduler_lock is not None:
            self._scheduler_lock.release()
            self._scheduler_lock = None
        for bg in list(self._bg):
            bg.cancel()
        self._bg.clear()
        for w in self._digest_workers:
            w.cancel()
        self._digest_workers = []
        self._digest_q = None
        self._digest_inflight.clear()

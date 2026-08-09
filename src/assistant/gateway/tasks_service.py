"""Task service for the gateway — Cowork-style tasks.

A Task is standing configuration (name + prompt + optional model + schedule);
each Run executes the prompt as ONE ordinary chat turn of the universal agent
on the run's own stream (``task-run:{run_id}``) — so a run IS a chat: the user
can steer it live and keep talking in it afterwards. The service owns the
durable stores and the deterministic Scheduler. The gateway is injected
(``set_gateway``) for turns / stops / stream deletion; an optional notifier
(``set_notifier``) pushes a run's outcome back to the channel the task came
from. HITL rides durable inquiries: a raised inquiry flips the run to
``needs_input``; the answer flips it back.
"""

import asyncio
import contextlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from assistant.config import Config, load_config
from assistant.connections import ConnectionStore
from assistant.hitl import NullAsker
from assistant.tasks.model import (
    RECALL_ALL,
    RECALL_NONE,
    Run,
    RunStatus,
    RunTrigger,
    ScheduleKind,
    Task,
    manual_schedule,
    normalize_schedule,
)
from assistant.tasks.scheduling import compute_next_run, parse_dt, schedule_text
from assistant.tasks.summary import default_summarizer, suggest_task_meta, summarize_run

# Chars of prior-run index a surface carries before the oldest entries are dropped.
# ~125 summaries: six months of a weekday task.
_RECALL_BUDGET = 16_000

# What a pre-ADR-0027 recurring task inherits: the depth it ran on until now.
_MIGRATED_RECALL = 3


def _recall_row(run: Run) -> str:
    """One index line. A run with no summary — failed, cancelled, or a summariser
    that returned "" — names its state and the call that opens it, since it may have
    committed work before settling (ADR 0018)."""
    when = (run.ended_at or run.started_at or "")[:10]
    if run.summary:
        return f"- {run.id} ({when}) · {run.summary}"
    return f'- {run.id} ({when}) · {run.status}, use read_run("{run.id}") to check.'


def _recall_lines(prior: list[Run]) -> list[str]:
    """The prior-run index, oldest-first. Over budget the oldest entries drop and the
    header says how many, so a truncated index never reads as a complete one."""
    if not prior:
        return []
    rows = [_recall_row(r) for r in prior]
    kept: list[str] = []
    used = 0
    for row in reversed(rows):  # newest first: the budget bites the oldest
        if used + len(row) > _RECALL_BUDGET:
            break
        kept.append(row)
        used += len(row)
    kept.reverse()
    omitted = len(rows) - len(kept)
    head = "Earlier runs of this task (most recent last"
    head += f"; {omitted} older omitted — get_task lists every run):" if omitted else "):"
    return [
        head,
        *kept,
        'Do not repeat work these runs did; read_run("<run id>") opens any of them in full.',
    ]


def _validate_recall(depth: int) -> None:
    """``recall_depth`` is a run count, ``RECALL_ALL`` for every one. No upper bound —
    the surface budget caps what a large value actually renders."""
    if not isinstance(depth, int) or isinstance(depth, bool) or depth < RECALL_ALL:
        raise ValueError(
            f"recall_depth must be a whole number of runs, {RECALL_ALL} for all "
            f"or {RECALL_NONE} for none, not {depth!r}"
        )


def _run_surface(task: Task, prior: list[Run], folder_lines: list[str]) -> str:
    """Surface paragraph for a run's turn: unattended-execution framing + the
    outcomes of recent runs so a recurring task doesn't repeat itself + the task's
    own working folders (resolved from live task-scope grants)."""
    lines = [
        f'You are executing the background task "{task.name}" (task id {task.id}) as an '
        "unattended scheduled run.",
        "Do the work described in the user message completely, then report the outcome — "
        "your final message is the run's result the user will read later.",
        "No one is watching live: prefer acting over asking; raise a question only if "
        "truly blocked.",
    ]
    lines += _recall_lines(prior)
    lines += folder_lines
    return "\n".join(lines)


def _task_folder_lines(folders, profile: str, task_id: str) -> list[str]:
    """Surface lines for the task's own folders (task-scope grants) — resolved at
    turn start so the unattended agent knows where to work. Profile folders are
    ambient (every chat has them) and are not repeated here."""
    lines = []
    for f in folders.list_folders():
        g = next(
            (
                g
                for g in f.get("grants", [])
                if g.get("profile") == profile
                and g.get("task_id", "") == task_id
                and not g.get("chat_id", "")
            ),
            None,
        )
        if g is None or g.get("mode") == "none":
            continue
        access = "read-write" if g.get("mode") == "read_write" else "read-only"
        note = "" if Path(f.get("path", "")).is_dir() else " — path is missing"
        lines.append(f"Working folder: {f['path']} ({access}){note}.")
    return lines


def _run_view(r: Run) -> dict:
    return {
        "id": r.id,
        "task_id": r.task_id,
        "status": r.status,
        "trigger": r.trigger,
        "started_at": r.started_at,
        "ended_at": r.ended_at,
        "summary": r.summary,
        "error": r.error or "",
        "seen": r.seen_at is not None,
    }


def _task_row(t: Task, last_run: Run | None, unread: int, needs_input: bool) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "prompt": t.prompt,
        "model": t.model,
        "description": t.description,
        "schedule": t.schedule,
        "schedule_desc": schedule_text(t.schedule),
        "paused": t.paused,
        "starred": t.starred,
        "recall_depth": t.recall_depth,
        "next_run_at": t.next_run_at,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "last_run": _run_view(last_run) if last_run else None,
        "unread": unread,
        "needs_input": needs_input,
    }


class TaskService:
    """The gateway's task subsystem: stores, scheduler, and the run executor."""

    def __init__(
        self,
        config: Config | None = None,
        store=None,
        inquiry_store=None,
        max_concurrent: int = 3,
        scheduler_interval: float = 30.0,
        config_factory: Callable[[], Config] | None = None,
        summary_factory: Callable[[Config], object] | None = None,
    ) -> None:
        self._config = config or load_config()
        self._config_factory = config_factory or load_config
        # How the cheap-model distiller (run summaries, task auto-naming) is built.
        self._summary_factory = summary_factory or default_summarizer
        self._store = store
        self._inquiries = inquiry_store
        self._scheduler = None
        self._scheduler_lock = None
        self._scheduler_interval = scheduler_interval
        self._sem = asyncio.Semaphore(max_concurrent)
        self._jobs: dict[str, asyncio.Task] = {}  # run_id → executing job
        self._stopping: set[str] = set()  # run ids being user-stopped
        self._gateway = None
        self._notify = None  # async (platform, chat_id, text) -> None
        self._emit = None  # async (chat_id, event) -> None
        self._questions = None  # the question mirror: ask(...) / retract(...)
        if self._inquiries is not None:
            # InquiryStore only takes its change hook at construction (`on_change=`,
            # stored privately as `_on_change`) — there is no public setter. A store
            # injected here (as tests do) still needs wiring to our hook, so set the
            # private attribute directly.
            self._inquiries._on_change = self._on_inquiry

    # ---- wiring ----

    def set_gateway(self, gateway) -> None:
        self._gateway = gateway

    def set_notifier(self, notify) -> None:
        self._notify = notify

    def set_emitter(self, emitter) -> None:
        self._emit = emitter

    def set_question_mirror(self, questions) -> None:
        """Where a chat's questions go so an Attached Peer can answer them (ADR 0020)."""
        self._questions = questions

    @property
    def store(self):
        return self._store

    @property
    def inquiries(self):
        return self._inquiries

    @property
    def scheduler_running(self) -> bool:
        return self._scheduler is not None

    async def start(self, *, scheduler: bool = True) -> None:
        """Build the durable stores (cheap; no LLM). ``scheduler=False`` skips the
        poll loop; a cross-process lock enforces a single live scheduler anyway."""
        from assistant.hitl import InquiryStore
        from assistant.tasks import TaskStore

        d = self._config.data_dir
        d.mkdir(parents=True, exist_ok=True)
        if self._store is None:
            self._store = TaskStore(path=d / "tasks.db")
        if self._inquiries is None:
            self._inquiries = InquiryStore(path=d / "inquiries.db", on_change=self._on_inquiry)
        await self._migrate_workdirs()
        await self._migrate_origin_connections()
        await self._migrate_recall()
        if scheduler and self._scheduler is None:
            from assistant.scheduler_lock import SchedulerLock
            from assistant.tasks import Scheduler

            lock = SchedulerLock(d / "scheduler.lock")
            if lock.acquire():
                self._scheduler_lock = lock
                self._scheduler = Scheduler(
                    self._store, self._fire, interval=self._scheduler_interval
                )
                await self._scheduler.start()

    async def _migrate_recall(self) -> None:
        """Pre-ADR-0027 tasks had an unconditional 3-run look-back; give the recurring
        ones ``_MIGRATED_RECALL`` so the upgrade changes nothing for them. Best-effort:
        a failure here costs look-back, never data."""
        try:
            await self._store.backfill_recall(_MIGRATED_RECALL)
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("task recall backfill", exc)

    async def _migrate_origin_connections(self) -> None:
        """A task queued before Connections existed points at a platform name; move it
        onto that platform's Connection so its outcome still reaches the chat."""
        try:
            # The resolved secret env, not an empty one: this may be the first read of
            # connections.json, and a store with no environment would migrate a
            # token-seeded install into nothing.
            store = ConnectionStore(self._config.paths, self._config.secret_env)
            await self._store.rekey_origin_channels(store.first_by_platform())
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("task origin connection migration", exc)

    async def _migrate_workdirs(self) -> None:
        """Legacy single-workdir tasks → task-scope Folder Grants (spec 2026-07-20).
        Best-effort and idempotent: strip_workdirs mutates records exactly once, so
        a grant hiccup on one task loses at most THAT one legacy attachment —
        never data, and never the rest of the batch."""
        from assistant.folders import READ, READ_WRITE, FolderStore

        try:
            moved = await self._store.strip_workdirs()
            if not moved:
                return
            folders = FolderStore(self._config.root_dir / "folders.json")
            profile = self._config.data_dir.name
            for task_id, wd, access in moved:
                mode = READ_WRITE if access == "read_write" else READ
                with contextlib.suppress(Exception):
                    folders.grant_path(wd, mode, profile, task_id=task_id)
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("task workdir migration", exc)

    async def reload(self) -> None:
        """Re-resolve config after a settings change (model set per turn — nothing
        else to rebuild here; the gateway swaps its own agents)."""
        self._config = self._config_factory()

    async def close(self) -> None:
        if self._scheduler is not None:
            await self._scheduler.stop()
            self._scheduler = None
        if self._scheduler_lock is not None:
            self._scheduler_lock.release()
            self._scheduler_lock = None
        for job in list(self._jobs.values()):
            job.cancel()
        self._jobs.clear()

    async def _jobs_done(self) -> None:
        """Await all in-flight run jobs (tests + orderly shutdown)."""
        await asyncio.gather(*list(self._jobs.values()), return_exceptions=True)

    # ---- task CRUD ----

    def _validate_model(self, model: str | None) -> None:
        if not model:
            return
        from assistant.llm_configs import LlmConfigStore

        if LlmConfigStore(self._config.paths).get_config(model) is None:
            raise ValueError(
                f"unknown model configuration id {model!r} — pick one from the "
                "configured LLM configurations (or omit for the profile default)"
            )

    async def create_task(
        self,
        name: str,
        prompt: str,
        model: str | None = None,
        schedule: dict | None = None,
        origin_channel: str | None = None,
        origin_chat: str | None = None,
        description: str = "",
        recall_depth: int = RECALL_NONE,
    ) -> dict:
        """Create a task. Raises ValueError on a bad schedule/model (callers map it
        to HTTP 422 or a correctable tool reply). An empty ``name`` triggers
        cheap-model auto-naming (and, unless given, auto-description) from the
        prompt. A task's working folders are managed separately as task-scope
        Folder Grants (Folders UI), not stored on the task."""
        self._validate_model(model)
        _validate_recall(recall_depth)
        name = (name or "").strip()
        description = (description or "").strip()
        if not name:
            gen_name, gen_desc = await suggest_task_meta(
                self._config, prompt, agent_factory=self._summary_factory
            )
            name = gen_name
            description = description or gen_desc
        task = await self._store.create_task(
            name=name,
            prompt=prompt,
            model=model or None,
            schedule=normalize_schedule(schedule),
            origin_channel=origin_channel,
            origin_chat=origin_chat,
            description=description,
            recall_depth=recall_depth,
        )
        return _task_row(task, None, 0, False)

    async def update_task(self, task_id: str, **patch) -> dict | None:
        if "schedule" in patch:
            patch["schedule"] = normalize_schedule(patch["schedule"])
        if "model" in patch:
            self._validate_model(patch["model"])
        if "recall_depth" in patch:
            _validate_recall(patch["recall_depth"])
        if "name" in patch:
            patch["name"] = (patch["name"] or "").strip()
        if "description" in patch:
            patch["description"] = (patch["description"] or "").strip()
        task = await self._store.update_task(task_id, **patch)
        return None if task is None else await self.get_task(task_id)

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task, all its runs, and each run's chat stream. Stops any
        in-flight run first. Irreversible; the GUI gates it behind a confirm."""
        task = await self._store.get_task(task_id)
        if task is None:
            return False
        for r in await self._store.list_runs(task_id):
            await self.stop_run(r.id)  # no-op on settled runs
            await self._store.delete_run(r.id)
            if self._gateway is not None:
                with contextlib.suppress(Exception):
                    await self._gateway.delete_chat(r.stream_id)
        await self._store.delete_task(task_id)
        if self._gateway is not None:
            # Best-effort: a stub gateway in unit tests may have no `.permissions`
            # at all, and a real deleted task's grants are gone either way once the
            # task itself no longer exists.
            with contextlib.suppress(Exception):
                self._gateway.permissions.drop_task(task_id)
            with contextlib.suppress(Exception):
                self._gateway.folders.drop_task(task_id)
        return True

    async def list_tasks(self) -> list[dict]:
        """Task rows for the drawer: needs-input first, then newest."""
        tasks = await self._store.list_tasks()
        latest: dict[str, Run] = {}
        unread: dict[str, int] = {}
        needs: set[str] = set()
        for r in await self._store.list_runs():  # newest first
            latest.setdefault(r.task_id, r)
            if r.status in RunStatus.TERMINAL and r.seen_at is None:
                unread[r.task_id] = unread.get(r.task_id, 0) + 1
            if r.status == RunStatus.NEEDS_INPUT:
                needs.add(r.task_id)
        out = [_task_row(t, latest.get(t.id), unread.get(t.id, 0), t.id in needs) for t in tasks]
        out.sort(key=lambda v: 0 if v["needs_input"] else 1)  # stable; list is newest-first
        return out

    async def get_task(self, task_id: str) -> dict | None:
        t = await self._store.get_task(task_id)
        if t is None:
            return None
        runs = await self._store.list_runs(task_id)
        unread = sum(1 for r in runs if r.status in RunStatus.TERMINAL and r.seen_at is None)
        needs = any(r.status == RunStatus.NEEDS_INPUT for r in runs)
        row = _task_row(t, runs[0] if runs else None, unread, needs)
        row["runs"] = [_run_view(r) for r in runs]
        return row

    async def get_run(self, run_id: str) -> dict | None:
        r = await self._store.get_run(run_id)
        if r is None:
            return None
        t = await self._store.get_task(r.task_id)
        view = _run_view(r)
        view["task_name"] = t.name if t else ""
        return view

    async def mark_run_seen(self, run_id: str) -> bool:
        """Stamp seen_at once the run is terminal (a live peek is not 'seen' —
        the unread dot must still fire when it finishes). Idempotent."""
        r = await self._store.get_run(run_id)
        if r is None:
            return False
        if r.status in RunStatus.TERMINAL and r.seen_at is None:
            r.seen_at = datetime.now().astimezone().isoformat()
            await self._store.save_run(r)
        return True

    # ---- execution ----

    async def _fire(self, task_id: str) -> None:
        """Scheduler callback: start a run and re-arm (cron) / disarm (once)."""
        t = await self._store.get_task(task_id)
        if t is None or t.paused or not t.next_run_at:
            return
        once = t.schedule.get("kind") == ScheduleKind.ONCE
        # Re-arm base = max(now, slot). Plain `now` re-arms onto the SAME slot when
        # `_fire` runs ahead of it (e.g. a direct/test-driven fire before the cron
        # time has actually arrived) — advancing "strictly after now" just returns
        # the slot that hasn't happened yet. Plain `slot` breaks after downtime: a
        # stale next_run_at (process off for 3 days on a daily cron) would advance
        # only one period per call, so the scheduler's next few 30s ticks would all
        # still find it <= now and fire again — catch-up spam the old system
        # deliberately avoided by skipping missed slots. Taking the max gives one
        # fire per stale slot (base becomes `now`, landing next_run_at in the
        # future) while still handling the early-fire case (base becomes `slot`).
        now = datetime.now().astimezone()
        slot = parse_dt(t.next_run_at)
        base = slot if slot is not None and slot > now else now
        # Re-arm BEFORE running, so a slow run can never double-fire this slot.
        rearm: dict = {"next_run_at": compute_next_run(t.schedule, base, after_fire=True)}
        if once:
            rearm["schedule"] = manual_schedule()  # exhausted → back to on-demand
        await self._store.update_task(task_id, **rearm)
        await self.start_run(task_id, trigger="once" if once else "schedule")

    async def start_run(self, task_id: str, trigger: RunTrigger = "manual") -> Run | None:
        t = await self._store.get_task(task_id)
        if t is None:
            return None
        run = await self._store.create_run(task_id, trigger=trigger)
        job = asyncio.create_task(self._execute(run.id))
        self._jobs[run.id] = job
        job.add_done_callback(lambda _j, rid=run.id: self._jobs.pop(rid, None))
        return run

    async def _execute(self, run_id: str) -> None:
        try:
            async with self._sem:
                await self._turn(run_id)
        except asyncio.CancelledError:
            # killed while queued (stop/delete/shutdown) — settle the record
            await self._finish(run_id, RunStatus.CANCELLED)
            raise

    async def _turn(self, run_id: str) -> None:
        from assistant.hitl import DurableAsker

        run = await self._store.get_run(run_id)
        if run is None or run.status in RunStatus.TERMINAL:
            return
        task = await self._store.get_task(run.task_id)
        if task is None:
            await self._finish(run_id, RunStatus.FAILED, error="task deleted")
            return
        asker = DurableAsker(
            NullAsker(),
            self._inquiries,
            task_id=run_id,  # inquiry.task_id carries the RUN id (run-…)
            channel=task.origin_channel or "web",
            chat=run.stream_id,
        )
        prior = await self._store.recent_runs(task.id, n=task.recall_depth, before=run_id)
        folder_lines: list[str] = []
        if self._gateway is not None:
            with contextlib.suppress(Exception):
                folder_lines = _task_folder_lines(
                    self._gateway.folders, self._config.data_dir.name, task.id
                )
        try:
            reply = await self._gateway.send_message(
                task.prompt,
                chat_id=run.stream_id,
                asker=asker,
                surface=_run_surface(task, prior, folder_lines),
                llm_config_id=task.model,
                task_id=task.id,
            )
        except asyncio.CancelledError:
            await self._finish(run_id, RunStatus.CANCELLED)
            raise
        except Exception as exc:
            await self._finish(run_id, RunStatus.FAILED, error=str(exc))
            return
        if run_id in self._stopping:  # user Stop → the turn returned "" via TurnCancelled
            await self._finish(run_id, RunStatus.CANCELLED)
            return
        # The summary is distilled from this profile's config — never `task.model`, never
        # the Chat override on the run's thread (ADR 0025).
        summary = await summarize_run(
            self._config, task.prompt, reply, agent_factory=self._summary_factory
        )
        await self._finish(run_id, RunStatus.COMPLETED, summary=summary)
        await self._deliver(task, summary or (reply or "").strip()[:400])

    async def _finish(self, run_id: str, status: str, **fields) -> None:
        self._stopping.discard(run_id)
        await self._store.set_run_status(run_id, status, **fields)
        # A run cancelled while parked on DurableAsker.ask (stop/delete/shutdown)
        # never reaches its own expire() — CancelledError propagates straight past
        # it — so release any inquiry it still owns here, or it strands the
        # "Needs your input" strip forever. Best-effort: a terminal run must settle
        # even if the inquiry store hiccups.
        if self._inquiries is not None:
            try:
                await self._inquiries.cancel_for_task(run_id)
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed("pending inquiry release on run finish", exc, run_id=run_id)

    async def stop_run(self, run_id: str) -> bool:
        """Stop a live run: cancel its turn (keeps what it already produced on the
        stream). A run still queued on the semaphore is cancelled directly."""
        run = await self._store.get_run(run_id)
        if run is None or run.status in RunStatus.TERMINAL:
            return False
        self._stopping.add(run_id)
        stopped_turn = False
        if self._gateway is not None:
            stopped_turn = await self._gateway.cancel_turn(run.stream_id, reason="Stopped")
        if not stopped_turn:
            job = self._jobs.get(run_id)
            if job is not None:
                job.cancel()
            else:  # nothing in flight at all (e.g. orphaned needs_input) — settle
                await self._finish(run_id, RunStatus.CANCELLED)
        return True

    async def cancel_all(self, reason: str = "cancelled") -> int:
        """Stop every non-terminal run (profile archive path)."""
        n = 0
        for r in await self._store.list_runs():
            if r.status not in RunStatus.TERMINAL:
                await self.stop_run(r.id)
                n += 1
        return n

    async def _deliver(self, task: Task, text: str) -> None:
        """Push a completed run's outcome back through the Connection the task came
        from — ``origin_channel`` is that Connection's id."""
        if self._notify is None or not task.origin_channel or not task.origin_chat or not text:
            return
        try:
            await self._notify(task.origin_channel, task.origin_chat, f"✅ {task.name}: {text}")
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("run outcome channel delivery", exc, task_id=task.id)

    # ---- durable inquiries (HITL) ----

    async def _on_inquiry(self, inquiry, kind) -> None:
        """InquiryStore change hook: surface the event on its stream AND flip the
        owning run between running ↔ needs_input."""
        await self._emit_inquiry(inquiry, kind)
        await self._mirror_inquiry(inquiry, kind)
        rid = inquiry.task_id or ""
        if not rid.startswith("run-") or self._store is None:
            return
        run = await self._store.get_run(rid)
        if run is None or run.status in RunStatus.TERMINAL:
            return
        from assistant.hitl.inquiry import InquiryStatus

        if kind == "raised" and run.status == RunStatus.RUNNING:
            await self._store.set_run_status(rid, RunStatus.NEEDS_INPUT)
        elif kind in InquiryStatus.TERMINAL and run.status == RunStatus.NEEDS_INPUT:
            await self._store.set_run_status(rid, RunStatus.RUNNING)

    async def _mirror_inquiry(self, inquiry, kind) -> None:
        """Durable HITL lifecycle → the Attached Peer: a raised question is shown there
        with its options, and any resolution takes it back."""
        if self._questions is None or not inquiry.chat:
            return
        from assistant.observability import log_suppressed

        try:
            if kind == "raised":
                await self._questions.ask(
                    inquiry.chat, inquiry.id, inquiry.text, tuple(inquiry.options or ())
                )
            else:
                await self._questions.retract(inquiry.chat, inquiry.id)
        except Exception as exc:
            log_suppressed("question mirror", exc, inquiry_id=inquiry.id, kind=kind)

    async def _emit_inquiry(self, inquiry, kind) -> None:
        """Durable HITL lifecycle → InquiryRaised/InquiryAnswered on its stream."""
        if self._emit is None:
            return
        from assistant.events import InquiryAnswered, InquiryRaised
        from assistant.hitl.inquiry import InquiryStatus

        sid = inquiry.chat
        if not sid:
            return
        try:
            if kind == "raised":
                await self._emit(
                    sid,
                    InquiryRaised(
                        inquiry.id,
                        task_id=inquiry.task_id or "",
                        question=inquiry.text,
                        detail=getattr(inquiry, "detail", "") or "",
                        options=list(inquiry.options or []),
                        kind=inquiry.kind,
                    ),
                )
            elif kind in InquiryStatus.TERMINAL:
                await self._emit(
                    sid,
                    InquiryAnswered(
                        inquiry.id, answer=getattr(inquiry, "answer", "") or "", status=kind
                    ),
                )
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("inquiry event emit", exc, inquiry_id=inquiry.id, kind=kind)

    async def pending_inquiries(self, task_id: str | None = None) -> list[dict]:
        items = await self._inquiries.list_pending(task_id)
        out = []
        for i in items:
            v = {
                "id": i.id,
                "task_id": i.task_id,
                "chat": i.chat,
                "kind": i.kind,
                "text": i.text,
                "detail": i.detail,
                "options": i.options,
                "created_at": i.created_at,
            }
            # resolve the run → its task, so the strip can label + link the source
            rid = i.task_id or ""
            if rid.startswith("run-"):
                run = await self._store.get_run(rid)
                task = await self._store.get_task(run.task_id) if run else None
                v["root_id"] = task.id if task else None
                v["task_title"] = task.name if task else ""
                v["run_id"] = rid
            else:
                v["root_id"], v["task_title"], v["run_id"] = None, "", None
            out.append(v)
        return out

    async def answer_inquiry(self, inquiry_id: str, answer: str) -> bool:
        return (await self._inquiries.answer(inquiry_id, answer)) is not None

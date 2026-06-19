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

from assistant.config import Config, load_config

_CONTROL_PROMPT = (
    "You manage ONE task for the user. When they ask for a change — add or cancel "
    "a subtask, change the objective, add a deliverable, reschedule it (change when "
    "it runs or how it repeats), or cancel the task — use your tools to do it "
    "immediately (it's their task; don't ask permission), then confirm in one short "
    "sentence what you changed. To change WHEN or HOW OFTEN it runs (e.g. 'make it "
    "weekdays', 'move to 8am', 'stop repeating') use the reschedule tool — never add "
    "a subtask for a scheduling change. Compute the ISO time from the current "
    "date/time in your environment context. For questions about progress or status, "
    "read the task and answer concisely. You do NOT do the research or work yourself "
    "— the task runner does that; you only steer this task."
)


class _ParkingAsker:
    """A transport asker with no live channel: blocks until the matching inquiry
    is answered out of band (the inquiry store wakes the DurableAsker)."""

    async def ask(self, question, timeout=None):
        await asyncio.Event().wait()


class TaskService:
    """The gateway's task subsystem: stores, runner, planner, and intake driver."""

    def __init__(
        self, config: Config | None = None, store=None, inquiry_store=None,
        manager=None, planner_agent=None, executor=None, max_concurrent: int = 3,
        scheduler_interval: float = 30.0,
    ) -> None:
        self._config = config or load_config()
        self._store = store
        self._inquiries = inquiry_store
        self._manager = manager
        self._planner = planner_agent
        self._executor = executor
        self._max_concurrent = max_concurrent
        self._scheduler_interval = scheduler_interval
        self._scheduler = None
        self._bg: set[asyncio.Task] = set()
        self._control_agents: dict = {}  # task_id -> (agent, stream) for task chat
        # Async (session_id, event) -> None, wired by the gateway. Lets a task's
        # lifecycle ride the AG2 stream so the GUI renders it as events. None → off.
        self._emit = None

    def set_emitter(self, emitter) -> None:
        """Wire an async ``(session_id, event)`` emitter (the gateway's)."""
        self._emit = emitter

    async def _emit_status(self, task_id: str, status: str, error: str = "") -> None:
        """Translate a lifecycle transition into the matching AG2 task event and
        emit it onto the task's stream (``task:<id>``). Reuses AG2-native events;
        intermediate statuses (pending/planning) carry no event — the GUI reads
        those from the task panel."""
        if self._emit is None:
            return
        from autogen.beta.events import (
            TaskCancelled,
            TaskCompleted,
            TaskFailed,
            TaskStarted,
        )

        from assistant.tasks import TaskStatus

        t = await self._store.get(task_id)
        if t is None:
            return
        obj, name = (t.objective or t.title or ""), "ag2assistant"
        ev = None
        if status == TaskStatus.RUNNING:
            ev = TaskStarted(task_id=task_id, agent_name=name, objective=obj)
        elif status == TaskStatus.COMPLETED:
            ev = TaskCompleted(task_id=task_id, agent_name=name, objective=obj,
                               result=(t.result or ""), task_stream=f"task:{task_id}")
        elif status == TaskStatus.FAILED:
            ev = TaskFailed(task_id=task_id, agent_name=name, objective=obj,
                            error=Exception(error or t.error or "failed"))
        elif status == TaskStatus.CANCELLED:
            ev = TaskCancelled(task_id=task_id, agent_name=name, objective=obj,
                               reason=error or "")
        if ev is not None:
            try:
                await self._emit(f"task:{task_id}", ev)
            except Exception:
                pass

    async def _emit_deliverable(self, task_id, deliverable_id, description, preview="") -> None:
        """A produced deliverable → DeliverableProduced on the task's stream."""
        if self._emit is None:
            return
        from assistant.events import DeliverableProduced

        try:
            await self._emit(f"task:{task_id}", DeliverableProduced(
                task_id, deliverable_id=deliverable_id, description=description, preview=preview,
            ))
        except Exception:
            pass

    async def _emit_inquiry(self, inquiry, kind) -> None:
        """Durable HITL lifecycle → InquiryRaised/InquiryAnswered on the task's stream.
        (AG2's HumanInputRequest is transient; our inquiries are durable & task-scoped.)"""
        if self._emit is None or not inquiry.task_id:
            return
        from assistant.events import InquiryAnswered, InquiryRaised
        from assistant.hitl.inquiry import InquiryStatus

        sid = f"task:{inquiry.task_id}"
        try:
            if kind == "raised":
                await self._emit(sid, InquiryRaised(
                    inquiry.id, task_id=inquiry.task_id, question=inquiry.text,
                    options=list(inquiry.options or []), kind=inquiry.kind,
                ))
            elif kind == InquiryStatus.ANSWERED:
                await self._emit(sid, InquiryAnswered(
                    inquiry.id, answer=getattr(inquiry, "answer", "") or "",
                ))
        except Exception:
            pass

    async def start(self) -> None:
        """Build the durable stores + runner (cheap; no LLM agent yet)."""
        from assistant.hitl import InquiryStore
        from assistant.tasks import TaskManager, TaskStore, make_task_executor

        d = self._config.data_dir
        d.mkdir(parents=True, exist_ok=True)
        if self._store is None:
            self._store = TaskStore(path=d / "tasks.db")
        if self._inquiries is None:
            self._inquiries = InquiryStore(
                path=d / "inquiries.db", on_change=self._emit_inquiry,
            )
        if self._executor is None:
            self._executor = make_task_executor(self._config)
        if self._manager is None:
            self._manager = TaskManager(
                self._store, self._executor,
                max_concurrent=self._max_concurrent, inquiry_store=self._inquiries,
                on_status=self._emit_status,        # lifecycle → AG2 task events
                on_deliverable=self._emit_deliverable,  # → DeliverableProduced
            )
        if self._scheduler is None:
            from assistant.tasks.scheduling import Scheduler

            self._scheduler = Scheduler(
                self._store, self._fire, interval=self._scheduler_interval
            )
            await self._scheduler.start()

    async def reload(self) -> None:
        """Rebuild the planner + executor from fresh config/keys after a settings
        change. The manager's executor reference is swapped so new runs use it while
        in-flight runs (tracked in the manager) finish on the old one; the planner is
        reset for a lazy rebuild. Stores and the scheduler are unaffected."""
        from assistant.config import load_config
        from assistant.tasks import make_task_executor

        self._config = load_config()
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
                if clarify else None
            )
            await prepare_task(
                self._store, task_id, self._planner_agent(),
                asker=intake_asker, capabilities=available_capabilities(),
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
        self, text: str, when: str, recurrence: str | None = None, channel: str = "web",
    ) -> str:
        """Schedule a task for `when` (ISO datetime), optionally recurring.

        Clarification + planning happen NOW (while the user is here), so the plan
        is baked into the task; the deterministic Scheduler then just *executes*
        that plan at each occurrence — no run-time questions."""
        from datetime import datetime

        from assistant.tasks import TaskStatus
        from assistant.tasks.scheduling import first_occurrence

        # for day-of-week recurrences (e.g. weekdays), start on the next matching day
        first = first_occurrence(recurrence, when, datetime.now().astimezone())
        if first is not None:
            when = first.isoformat()
        task = await self._store.create(
            text, origin_channel=channel, hitl_channel=channel,
            status=TaskStatus.SCHEDULED, scheduled_for=when, recurrence=recurrence or None,
        )
        if self._emit is not None:
            from assistant.events import TaskScheduled

            try:
                await self._emit(f"task:{task.id}", TaskScheduled(
                    task.id, scheduled_for=when, recurrence=recurrence or "",
                ))
            except Exception:
                pass
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
                _ParkingAsker(), self._inquiries, task_id=task_id, channel=channel,
            )
            await prepare_task(
                self._store, task_id, self._planner_agent(),
                asker=asker, capabilities=available_capabilities(),
            )
            # prepare_task leaves it PENDING (or CANCELLED if abandoned); arm it.
            cur = await self._store.get(task_id)
            if cur is not None and cur.status == TaskStatus.PENDING:
                await self._store.update(
                    task_id, status=TaskStatus.SCHEDULED, scheduled_for=when,
                )
        except Exception:
            pass  # planning is best-effort; the run can still plan on fire as a fallback

    async def _clone_subtree(self, src, new_parent_id: str) -> None:
        child = await self._store.add_subtask(
            new_parent_id, src.title, src.description, reopen_parent=False,
            capabilities=src.capabilities, objective=src.objective,
        )
        for d in src.deliverables or []:
            await self._store.add_deliverable(child.id, d.get("description", ""), d.get("criteria", ""))
        for gc in await self._store.children(src.id):
            await self._clone_subtree(gc, child.id)

    async def _clone_for_run(self, template):
        """A fresh, unplanned-status copy of a planned task tree (deliverables reset,
        no assets) — one occurrence's run, with the template's baked-in plan."""
        run = await self._store.create(
            template.title, description=template.description,
            objective=template.objective, capabilities=template.capabilities,
            origin_channel=template.origin_channel, hitl_channel=template.hitl_channel,
            run_of=template.id,  # mark this as one occurrence of the recurring task
        )
        for d in template.deliverables or []:
            await self._store.add_deliverable(run.id, d.get("description", ""), d.get("criteria", ""))
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
            nxt = next_occurrence(t.recurrence, t.scheduled_for, now)
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
        """Active/recent top-level tasks for the drawer (archived excluded), each
        carrying any pending inquiries in its subtree. Needs-input first, then
        newest."""
        roots = [t for t in await self._store.roots() if not getattr(t, "archived", False)]
        by_task: dict[str, list] = {}
        for inq in await self._inquiries.list_pending():
            by_task.setdefault(inq.task_id, []).append(inq)

        out = []
        for t in roots:
            s = await self._summary(t)
            ids = {t.id} | {d.id for d in await self._store.descendants(t.id)}
            s["inquiries"] = [
                self._inquiry_view(i) for tid in ids for i in by_task.get(tid, [])
            ]
            out.append(s)

        out.sort(key=lambda s: s["created_at"], reverse=True)
        out.sort(key=lambda s: 0 if s["inquiries"] else 1)  # needs-input first (stable)
        return out

    async def list_all(self, status: str | None = None) -> list[dict]:
        """The full task history for the listing page, newest first. `status` filters:
        active / completed / stopped / archived; None or 'all' = everything not archived."""
        roots = await self._store.roots()
        out = []
        for t in roots:
            archived = bool(getattr(t, "archived", False))
            if status == "archived":
                if not archived:
                    continue
            elif archived:
                continue  # archived hidden from every other view
            elif status == "active" and t.status not in self._ACTIVE:
                continue
            elif status == "completed" and t.status != "completed":
                continue
            elif status == "stopped" and t.status not in self._STOPPED:
                continue
            s = await self._summary(t)
            s["archived"] = archived
            out.append(s)
        out.sort(key=lambda s: s["created_at"], reverse=True)
        return out

    async def set_archived(self, task_id: str, archived: bool = True) -> tuple[bool, str]:
        """Archive a finished task (or unarchive any). Returns (ok, reason). Active
        tasks can't be archived — cancel them instead. reason: '' | 'notfound' | 'active'."""
        t = await self._store.get(task_id)
        if t is None:
            return False, "notfound"
        if archived and not t.is_terminal:
            return False, "active"  # only finished tasks can be archived
        await self._store.update(task_id, archived=archived)
        return True, ""

    async def get_task(self, task_id: str) -> dict | None:
        """Full task detail with its subtree, deliverables (incl. assets), progress."""
        t = await self._store.get(task_id)
        if t is None:
            return None
        return await self._node(t, include_assets=True)

    async def cancel(self, task_id: str, reason: str = "cancelled by user") -> bool:
        """Cancel a task and its whole subtree (also releases pending inquiries)."""
        if await self._store.get(task_id) is None:
            return False
        await self._manager.cancel(task_id, reason=reason)
        return True

    # --- action wrappers (thin; the universal agent's system tools call these) ---

    async def add_subtask(self, task_id, title, description="", capabilities="web") -> str:
        from assistant.tasks.control import do_add_subtask

        return await do_add_subtask(self._store, self._manager, task_id, title, description, capabilities)

    async def add_deliverable(self, task_id, description, criteria="") -> str:
        from assistant.tasks.control import do_add_deliverable

        return await do_add_deliverable(self._store, self._manager, task_id, description, criteria)

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

    def _control(self, task_id: str):
        """A cached, task-scoped controller agent (+ its conversation stream)."""
        entry = self._control_agents.get(task_id)
        if entry is None:
            from autogen.beta import Agent
            from autogen.beta.stream import MemoryStream

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
            v["root_id"] = root_id          # open this to see the source task
            v["task_title"] = title         # so the user knows what they're answering
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
            "id": i.id, "task_id": i.task_id, "kind": i.kind, "text": i.text,
            "detail": i.detail, "options": i.options, "created_at": i.created_at,
        }

    async def _summary(self, t) -> dict:
        kids = await self._store.children(t.id)
        delivs = t.deliverables or []
        done = sum(1 for d in delivs if d.get("status") in ("produced", "accepted"))
        progress = t.progress or []
        return {
            "id": t.id, "title": t.title, "status": t.status,
            "objective": t.objective or "", "created_at": t.created_at,
            "children": len(kids),
            "deliverables": len(delivs), "deliverables_done": done,
            "last_progress": progress[-1]["message"] if progress else None,
            "scheduled_for": t.scheduled_for, "recurrence": t.recurrence,
            "run_of": getattr(t, "run_of", None),
            "seen": getattr(t, "seen_at", None) is not None,
        }

    async def mark_seen(self, task_id: str) -> bool:
        """Record that the user has opened this task/run (clears its unread highlight).
        Idempotent — only writes the first time. Persists via the task store."""
        t = await self._store.get(task_id)
        if t is None:
            return False
        if getattr(t, "seen_at", None) is None:
            from datetime import datetime
            await self._store.update(task_id, seen_at=datetime.now().astimezone().isoformat())
        return True

    async def _node(self, t, include_assets: bool = False) -> dict:
        kids = await self._store.children(t.id)
        return {
            "id": t.id, "title": t.title, "status": t.status,
            "objective": t.objective or "", "description": t.description or "",
            "created_at": t.created_at, "capabilities": t.capabilities or [],
            "archived": bool(getattr(t, "archived", False)),
            "scheduled_for": t.scheduled_for, "recurrence": t.recurrence,
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
        for bg in list(self._bg):
            bg.cancel()
        self._bg.clear()

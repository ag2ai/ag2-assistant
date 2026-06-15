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

from agclaw.config import Config, load_config


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
    ) -> None:
        self._config = config or load_config()
        self._store = store
        self._inquiries = inquiry_store
        self._manager = manager
        self._planner = planner_agent
        self._executor = executor
        self._max_concurrent = max_concurrent
        self._bg: set[asyncio.Task] = set()

    async def start(self) -> None:
        """Build the durable stores + runner (cheap; no LLM agent yet)."""
        from agclaw.hitl import InquiryStore
        from agclaw.tasks import TaskManager, TaskStore, make_task_executor

        d = self._config.data_dir
        d.mkdir(parents=True, exist_ok=True)
        if self._store is None:
            self._store = TaskStore(path=d / "tasks.db")
        if self._inquiries is None:
            self._inquiries = InquiryStore(path=d / "inquiries.db")
        if self._executor is None:
            self._executor = make_task_executor(self._config)
        if self._manager is None:
            self._manager = TaskManager(
                self._store, self._executor,
                max_concurrent=self._max_concurrent, inquiry_store=self._inquiries,
            )

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
            from agclaw.agent import create_agent

            self._planner = create_agent(self._config, memory=False, skills=False)
        return self._planner

    async def submit_request(self, text: str, channel: str = "web") -> str:
        """Create a task and drive intake + run in the background; return its id."""
        from agclaw.hitl import DurableAsker
        from agclaw.tasks.planner import prepare_task
        from agclaw.tools import available_capabilities

        task = await self._store.create(text, origin_channel=channel, hitl_channel=channel)

        async def _drive() -> None:
            try:
                asker = DurableAsker(
                    _ParkingAsker(), self._inquiries, task_id=task.id, channel=channel,
                )
                await prepare_task(
                    self._store, task.id, self._planner_agent(),
                    asker=asker, capabilities=available_capabilities(),
                )
                cur = await self._store.get(task.id)
                if cur is not None and not cur.is_terminal:
                    # the runner re-binds the parking asker into a DurableAsker
                    # per (sub)task, so each subtask's prompts are tagged.
                    await self._manager.submit(task.id, asker=_ParkingAsker())
            except Exception as exc:
                from agclaw.tasks import TaskStatus

                await self._store.set_status(
                    task.id, TaskStatus.FAILED, error=f"intake/submit error: {exc}"
                )

        bg = asyncio.create_task(_drive())
        self._bg.add(bg)
        bg.add_done_callback(self._bg.discard)
        return task.id

    async def list_tasks(self) -> list[dict]:
        """Top-level tasks, newest first, as lightweight summaries."""
        roots = await self._store.roots()
        out = [await self._summary(t) for t in roots]
        out.sort(key=lambda s: s["created_at"], reverse=True)
        return out

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

    async def pending_inquiries(self, task_id: str | None = None) -> list[dict]:
        items = await self._inquiries.list_pending(task_id)
        return [self._inquiry_view(i) for i in items]

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
        }

    async def _node(self, t, include_assets: bool = False) -> dict:
        kids = await self._store.children(t.id)
        return {
            "id": t.id, "title": t.title, "status": t.status,
            "objective": t.objective or "", "description": t.description or "",
            "created_at": t.created_at, "capabilities": t.capabilities or [],
            "intake": t.intake or {},
            "progress": t.progress or [],
            "error": t.error or "",
            "deliverables": [
                self._deliverable_view(d, include_assets) for d in (t.deliverables or [])
            ],
            "children": [await self._node(c, include_assets) for c in kids],
        }

    async def close(self) -> None:
        for bg in list(self._bg):
            bg.cancel()
        self._bg.clear()

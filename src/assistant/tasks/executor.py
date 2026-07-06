"""The real (LLM-backed) task executor — with deliverable verification.

For each pending deliverable the executor runs the agent to produce it, then a
**verifier** (a cheaper model) checks the output against the deliverable's
acceptance criteria. Only verified output is marked PRODUCED; output that merely
describes what could be done, hands back a plan/menu instead of the finished
work, or admits it couldn't is REJECTED (with a reason) so the runner reworks or
fails — never a false "done". (Asking the user for clarification is encouraged,
not penalised: it happens mid-turn via HITL, so the final output is still the
real deliverable.)

The agent is bound to the task's asker, so tools, permissions, and HITL behave
exactly as in a normal turn — and because every subtask in the tree gets that
SAME asker, a sub-agent's clarification/confirmation bubbles all the way up to
the channel that triggered the task (no extra access, nothing swallowed).
"""

import asyncio

from pydantic import BaseModel

from assistant.tasks.model import DeliverableStatus

_MAX_ASSET_CHARS = 50_000
_MAX_VERIFY_CHARS = 12_000
_MAX_CHILD_CONTEXT = 12_000

# Inner subagent events worth nesting under its card: its responses, the tools it
# calls, any deliverables, and a nested subagent's own lifecycle (→ recursion).
_FORWARD_INNER = frozenset(
    {
        "ModelResponse",
        "ToolCallsEvent",
        "DeliverableProduced",
        "TaskStarted",
        "TaskCompleted",
        "TaskFailed",
        "TaskCancelled",
    }
)


class _Verdict(BaseModel):
    satisfied: bool
    reason: str = ""


def _subagent_archetype(caps: list[str]) -> tuple[str, str]:
    """Pick a visible worker archetype for this task attempt."""
    if "web" in caps:
        return (
            "researcher",
            "You are a focused research subagent. Use the available research tools, "
            "ground factual claims in sources, and return finished notes or answers "
            "that directly satisfy the assigned deliverables.",
        )
    if any(c in caps for c in ("gmail", "calendar", "drive", "google")):
        return (
            "operator",
            "You are a focused workspace operator subagent. Use only the assigned "
            "workspace tools, ask for confirmation when needed, and return the "
            "finished result of the assigned work.",
        )
    if "code" in caps:
        return (
            "coder",
            "You are a focused coding subagent. Use the available code tools only "
            "for the assigned work, keep changes scoped, and return the finished "
            "implementation or analysis.",
        )
    return (
        "worker",
        "You are a focused worker subagent. Complete the assigned deliverables "
        "directly, ask for missing user information when needed, and return the "
        "finished result.",
    )


async def _run_visible_subagent(
    config, task, caps, prompt: str, skills: bool, asker, manager, model: str | None = None
):
    """Run a named AG2 subagent and forward its lifecycle events.

    `model` overrides the model for this run — the escalated main model on the
    final attempt. When None, leaf subtasks (those with a parent) run on the
    cheaper/faster model and the root synthesis on the main model, as before.
    """
    from ag2.context import ConversationContext
    from ag2.stream import MemoryStream
    from ag2.tools.subagents.run_task import run_task

    from assistant.agent import cheap_model, create_agent, turn_prompt
    from assistant.permissions import PermissionManager, PermissionStore

    name, archetype_prompt = _subagent_archetype(caps)
    sub_config = config.model_copy(deep=True)
    sub_config.agent.name = name
    # Tasks write into the SHARED workspace root (same as chat) — `sub_config`
    # already inherits `config.workspace_dir` from the deep copy, so generated
    # images land in `<workspace>/images/`, files at the root, and reported paths
    # are workspace-relative (and so findable in the Files browser). We no longer
    # scope a task to its own `<workspace>/<slug>/` subfolder.
    # Seed the archetype as the persona, then compose the full turn prompt around
    # it without memory guidance because these subagents run with memory=False.
    sub_config.agent.system_prompt = archetype_prompt
    sub_config.agent.system_prompt = "\n\n".join(
        turn_prompt(sub_config, memory=False, workspace="files" in caps)
    )
    # Leaf subtasks run cheap; the root synthesis runs on the main model. `model`
    # (set on the final attempt) overrides that, escalating a struggling leaf.
    sub_model = model or (cheap_model(config) if task.parent_id else None)
    agent = create_agent(
        sub_config,
        memory=False,
        skills=skills,
        asker=asker,
        capabilities=caps,
        model=sub_model,
        compact=True,
    )

    from assistant.events import SubagentTrace
    from assistant.gateway.wire import to_wire

    subagent_task_id = f"{task.id}:{name}"

    # Lifecycle (TaskStarted/Completed/Failed/Usage) rides the parent stream and
    # drives the subagent card. The subagent's own work runs on `work_stream`; we
    # forward each inner event wrapped as a SubagentTrace so the GUI nests it under
    # the card (live + persistent on the task event log). A nested subagent's
    # lifecycle arrives here too and nests one level deeper — recursion for free.
    async def forward(event) -> None:
        await manager.emit_event(task.id, event)

    async def forward_inner(event) -> None:
        # Allowlist only what the GUI renders + nested-subagent lifecycle. Skips the
        # prompt echo, token chunks, HITL (durable path), and bookkeeping — so a
        # subagent's run doesn't re-persist the task stream per token.
        if type(event).__name__ not in _FORWARD_INNER:
            return
        await manager.emit_event(task.id, SubagentTrace(subagent_task_id, inner=to_wire(event)))

    parent_stream = MemoryStream(id=f"{task.id}:subagents")
    work_stream = MemoryStream(id=subagent_task_id)
    sub_id = parent_stream.subscribe(forward)
    work_sub = work_stream.subscribe(forward_inner)
    try:
        context = ConversationContext(
            stream=parent_stream,
            dependencies={
                PermissionManager: PermissionManager(
                    PermissionStore(config.data_dir / "permissions.json"),
                    asker=asker,
                    sandbox=config.tools.sandbox,
                )
            },
        )
        objective = f"Produce deliverables for: {task.title}"
        try:
            return await run_task(
                agent,
                objective,
                parent_context=context,
                context=prompt,
                stream=work_stream,
                task_id=subagent_task_id,
            )
        except asyncio.CancelledError:
            from ag2.events import TaskCancelled

            await manager.emit_event(
                task.id,
                TaskCancelled(
                    task_id=subagent_task_id,
                    agent_name=name,
                    objective=objective,
                    reason="parent task cancelled",
                ),
            )
            raise
    finally:
        work_stream.unsubscribe(work_sub)
        parent_stream.unsubscribe(sub_id)


async def _verify_deliverable(config, deliverable: dict, output: str) -> "_Verdict":
    """Strictly check produced output against a deliverable's criteria (cheap model)."""
    from ag2 import Agent

    from assistant.agent import model_config

    model = config.llm.aggregate_model or config.llm.model
    verifier = Agent("deliverable-verifier", config=model_config(config, model))
    prompt = (
        "Judge whether produced output satisfies a requested deliverable.\n\n"
        f"DELIVERABLE: {deliverable['description']}\n"
        f"ACCEPTANCE CRITERIA: {deliverable.get('criteria') or '(none given)'}\n\n"
        f"PRODUCED OUTPUT:\n{output[:_MAX_VERIFY_CHARS]}\n\n"
        "Be strict, but judge only whether the finished deliverable content is "
        "present and meets the criteria. satisfied=false if the output merely "
        "describes what COULD be done, hands back a plan or a menu of options "
        "instead of the finished work, or says it couldn't complete it. "
        "satisfied=true if the actual deliverable content is present and meets the "
        "criteria — even if it also notes caveats or open questions alongside it."
    )
    try:
        reply = await verifier.ask(prompt, response_schema=_Verdict)
        return await reply.content()
    except Exception as exc:
        return _Verdict(satisfied=False, reason=f"verification error: {exc}")


async def _used_web_tools(source) -> bool:
    """True if the agent actually called a search / web-fetch tool this turn."""
    from ag2.events import BuiltinToolCallEvent, ToolCallEvent

    try:
        history = source.history if hasattr(source, "history") else source
        events = list(await history.get_events())
    except Exception as exc:
        from assistant.observability import log_suppressed

        log_suppressed("web tool usage introspection", exc)
        return True  # can't introspect → don't falsely reject
    for ev in events:
        if isinstance(ev, (ToolCallEvent, BuiltinToolCallEvent)):
            name = (getattr(ev, "name", "") or "").lower()
            if "search" in name or "fetch" in name:
                return True
    return False


def make_task_executor(config, skills: bool = True):
    """Build an executor coroutine for `TaskManager`, using the real agent."""

    async def executor(task_id, manager, asker) -> None:
        store = manager.store
        task = await store.get(task_id)
        if task is None:
            return
        pending = task.pending_deliverables()
        if not pending:
            return  # pure orchestrator with no own deliverables

        # Agent scoped to the task's declared capabilities → a research subtask
        # can't reach Drive or run code; a calendar task only gets calendar.
        # Leaf subtasks (research/work pieces) run on the cheaper, faster model;
        # the root synthesis stays on the main model where quality matters most.
        caps = task.capabilities or []
        wanted = "\n".join(
            f"- {d['description']}"
            + (f" (acceptance: {d['criteria']})" if d.get("criteria") else "")
            + (
                f"\n  NOTE — a previous attempt was rejected: {d['notes']}"
                if d.get("notes")
                else ""
            )
            for d in pending
        )
        objective = task.objective or task.title
        # The original request — the objective is a paraphrase, so any content the
        # user supplied IN the request ("summarise THIS text: …", "analyse THIS
        # data: …") only survives here. Always carry it through verbatim.
        request = (task.description or task.title or "").strip()

        # A subtask inherits its parent's framing — the overall objective and the
        # user's clarifications — so it doesn't work blind. Without this a leaf
        # like "research the weather" has no idea the trip is to Lisbon and
        # defaults to the user's home location.
        parent_context = ""
        if task.parent_id:
            parent = await store.get(task.parent_id)
            if parent is not None:
                bits = []
                if parent.objective:
                    bits.append(f"Overall objective: {parent.objective}")
                if parent.intake:
                    qa = "\n".join(f"- {q} → {a}" for q, a in parent.intake.items())
                    bits.append(f"What the user clarified:\n{qa}")
                if bits:
                    parent_context = (
                        "\n\nThis is one subtask of a larger task — use this shared "
                        "context (don't ignore it):\n" + "\n".join(bits)
                    )

        kids = await store.children(task_id)
        done_kids = [c for c in kids if c.status == "completed"]
        failed_kids = [c for c in kids if c.is_terminal and c.status != "completed"]
        context = ""
        # let a parent synthesise from its finished subtasks' outputs
        parts = []
        for c in done_kids:
            for d in c.deliverables:
                a = (d.get("asset") or {}).get("content")
                if a:
                    parts.append(f"### {c.title}\n{a[:_MAX_CHILD_CONTEXT]}")
        if parts:
            context = "\n\nResults from completed subtasks:\n" + "\n\n".join(parts)
        if failed_kids:
            # resilience: tell the parent which legs couldn't be done so it works
            # around the gap and reports it honestly instead of inventing data.
            gaps = "\n".join(
                f"- {c.title}: {c.error or 'could not be completed'}" for c in failed_kids
            )
            context += (
                "\n\nSome subtasks could NOT be completed. Work around these gaps "
                "and state them honestly in your output — do not fabricate the "
                "missing information:\n" + gaps
            )

        prompt = (
            f"You are completing a task. Produce the actual deliverable content "
            f"with your tools — the finished output itself, not just a description "
            f"of how you would do it.\n"
            f"Involve the user as much as you need for clarity. Whenever you need "
            f"information only they have, or confirmation before a risky, "
            f"irreversible, or access-gated action, ASK them and then continue with "
            f"their answer — asking for clarification or confirmation is always "
            f"welcome and better than guessing.\n\n"
            f"Original request: {request}\n\n"
            f"Objective: {objective}{parent_context}{context}\n\n"
            f"Deliverable(s) to produce now:\n{wanted}"
        )

        await manager.progress(task_id, f"working on {len(pending)} deliverable(s)")

        # Final-attempt escalation: give the last shot the stronger (main) model.
        # Only a leaf runs on the cheap model, so escalation applies only where
        # cheap ≠ main — the root synthesis already uses the main model, and if
        # they're configured equal there's nothing to escalate (skip silently, no
        # duplicate note). The runner marks the in-flight attempt as final.
        from assistant.agent import cheap_model

        escalate_model = None
        if task.parent_id and manager.is_final_attempt(task_id):
            main = config.llm.model
            if cheap_model(config) != main:
                escalate_model = main
                await manager.progress(task_id, f"final attempt — escalating to {main}")

        result = await _run_visible_subagent(
            config, task, caps, prompt, skills, asker, manager, model=escalate_model
        )
        if not result.completed:
            raise RuntimeError(str(result.error or "subagent failed"))
        output = (result.result or "").strip()

        # Grounding gate: if this task is meant to research the web but the agent
        # never actually called a web tool, its facts aren't grounded → reject.
        web_used = await _used_web_tools(result.stream)
        ungrounded = "web" in caps and not web_used

        produced = 0
        for d in pending:
            if ungrounded:
                await store.set_deliverable_status(
                    task_id,
                    d["id"],
                    DeliverableStatus.REJECTED,
                    notes="not grounded: answer was written without using the search/"
                    "web-fetch tools — research the facts before producing this.",
                )
                continue
            verdict = await _verify_deliverable(config, d, output)
            if verdict.satisfied:
                produced += 1
                # Persist the deliverable as a real file in the task's workspace
                # folder (best-effort); keep the inline content for the viewer.
                asset = {
                    "name": d["description"][:60],
                    "kind": "text",
                    "content": output[:_MAX_ASSET_CHARS],
                }
                try:
                    from assistant.workspace import write_deliverable_file

                    asset["path"] = write_deliverable_file(config.workspace_dir, task, d, output)
                    asset["kind"] = "file"
                except Exception as exc:
                    from assistant.observability import log_suppressed

                    log_suppressed(
                        "deliverable file write",
                        exc,
                        task_id=task_id,
                        deliverable_id=d["id"],
                    )
                    # File write is best-effort; the inline content still stands.
                await store.set_deliverable_status(
                    task_id, d["id"], DeliverableStatus.PRODUCED, asset=asset
                )
                await manager.deliverable_produced(
                    task_id,
                    d["id"],
                    d.get("description", ""),
                    output[:240].replace("\n", " "),
                )
            else:
                await store.set_deliverable_status(
                    task_id,
                    d["id"],
                    DeliverableStatus.REJECTED,
                    notes=verdict.reason,
                )
        await manager.progress(
            task_id, f"{produced}/{len(pending)} deliverable(s) verified & produced"
        )

    return executor

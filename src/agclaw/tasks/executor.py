"""The real (LLM-backed) task executor — with deliverable verification.

For each pending deliverable the executor runs the agent to produce it, then a
**verifier** (a cheaper model) checks the output against the deliverable's
acceptance criteria. Only verified output is marked PRODUCED; output that merely
describes what could be done, asks the user a question, or admits it couldn't is
REJECTED (with a reason) so the runner reworks or fails — never a false "done".

The agent is bound to the task's asker, so tools, permissions, and HITL behave
exactly as in a normal turn (no extra access).
"""

from pydantic import BaseModel

from agclaw.tasks.model import DeliverableStatus

_MAX_ASSET_CHARS = 50_000
_MAX_VERIFY_CHARS = 12_000
_MAX_CHILD_CONTEXT = 12_000


class _Verdict(BaseModel):
    satisfied: bool
    reason: str = ""


async def _verify_deliverable(config, deliverable: dict, output: str) -> "_Verdict":
    """Strictly check produced output against a deliverable's criteria (cheap model)."""
    from autogen.beta import Agent

    from agclaw.agent import model_config

    model = config.llm.aggregate_model or config.llm.model
    verifier = Agent("deliverable-verifier", config=model_config(config, model))
    prompt = (
        "Judge whether produced output satisfies a requested deliverable.\n\n"
        f"DELIVERABLE: {deliverable['description']}\n"
        f"ACCEPTANCE CRITERIA: {deliverable.get('criteria') or '(none given)'}\n\n"
        f"PRODUCED OUTPUT:\n{output[:_MAX_VERIFY_CHARS]}\n\n"
        "Be strict. satisfied=false if the output only describes what COULD be done, "
        "asks the user a question, offers options instead of doing it, or says it "
        "couldn't complete the work. satisfied=true only if the actual deliverable "
        "content is present and meets the criteria."
    )
    try:
        reply = await verifier.ask(prompt, response_schema=_Verdict)
        return await reply.content()
    except Exception as exc:
        return _Verdict(satisfied=False, reason=f"verification error: {exc}")


async def _used_web_tools(reply) -> bool:
    """True if the agent actually called a search / web-fetch tool this turn."""
    from autogen.beta.events import BuiltinToolCallEvent, ToolCallEvent

    try:
        events = list(await reply.history.get_events())
    except Exception:
        return True  # can't introspect → don't falsely reject
    for ev in events:
        if isinstance(ev, (ToolCallEvent, BuiltinToolCallEvent)):
            name = (getattr(ev, "name", "") or "").lower()
            if "search" in name or "fetch" in name:
                return True
    return False


def make_task_executor(config, skills: bool = True):
    """Build an executor coroutine for `TaskManager`, using the real agent."""
    from agclaw.agent import create_agent, turn_prompt

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
        # (We use the main model throughout: the cheaper tier is too weak at the
        # agentic tool use research needs — it failed grounding/verification.)
        caps = task.capabilities or []
        agent = create_agent(
            config, memory=False, skills=skills, asker=asker, capabilities=caps,
        )

        wanted = "\n".join(
            f"- {d['description']}" + (f" (acceptance: {d['criteria']})" if d.get("criteria") else "")
            + (f"\n  NOTE — a previous attempt was rejected: {d['notes']}" if d.get("notes") else "")
            for d in pending
        )
        objective = task.objective or task.title
        done_kids = [c for c in await store.children(task_id) if c.status == "completed"]
        context = ""
        if done_kids:
            # let a parent synthesise from its finished subtasks' outputs
            parts = []
            for c in done_kids:
                for d in c.deliverables:
                    a = (d.get("asset") or {}).get("content")
                    if a:
                        parts.append(f"### {c.title}\n{a[:_MAX_CHILD_CONTEXT]}")
            if parts:
                context = "\n\nResults from completed subtasks:\n" + "\n\n".join(parts)

        prompt = (
            f"You are completing a task. Actually DO the work with your tools and "
            f"produce the deliverable content itself (do not just describe a plan or "
            f"ask which option to take).\n\nObjective: {objective}{context}\n\n"
            f"Deliverable(s) to produce now:\n{wanted}"
        )

        await manager.progress(task_id, f"working on {len(pending)} deliverable(s)")
        reply = await agent.ask(prompt, prompt=turn_prompt(config))
        output = (reply.body or "").strip()

        # Grounding gate: if this task is meant to research the web but the agent
        # never actually called a web tool, its facts aren't grounded → reject.
        web_used = await _used_web_tools(reply)
        ungrounded = "web" in caps and not web_used

        produced = 0
        for d in pending:
            if ungrounded:
                await store.set_deliverable_status(
                    task_id, d["id"], DeliverableStatus.REJECTED,
                    notes="not grounded: answer was written without using the search/"
                          "web-fetch tools — research the facts before producing this.",
                )
                continue
            verdict = await _verify_deliverable(config, d, output)
            if verdict.satisfied:
                produced += 1
                await store.set_deliverable_status(
                    task_id, d["id"], DeliverableStatus.PRODUCED,
                    asset={"name": d["description"][:60], "kind": "text",
                           "content": output[:_MAX_ASSET_CHARS]},
                )
            else:
                await store.set_deliverable_status(
                    task_id, d["id"], DeliverableStatus.REJECTED, notes=verdict.reason,
                )
        await manager.progress(
            task_id, f"{produced}/{len(pending)} deliverable(s) verified & produced"
        )

    return executor

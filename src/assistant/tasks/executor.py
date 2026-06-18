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

from pydantic import BaseModel

from assistant.tasks.model import DeliverableStatus

_MAX_ASSET_CHARS = 50_000
_MAX_VERIFY_CHARS = 12_000
_MAX_CHILD_CONTEXT = 12_000


class _Verdict(BaseModel):
    satisfied: bool
    reason: str = ""


async def _verify_deliverable(config, deliverable: dict, output: str) -> "_Verdict":
    """Strictly check produced output against a deliverable's criteria (cheap model)."""
    from autogen.beta import Agent

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
    from assistant.agent import cheap_model, create_agent, turn_prompt

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
        sub_model = cheap_model(config) if task.parent_id else None
        agent = create_agent(
            config, memory=False, skills=skills, asker=asker, capabilities=caps,
            model=sub_model,
        )

        wanted = "\n".join(
            f"- {d['description']}" + (f" (acceptance: {d['criteria']})" if d.get("criteria") else "")
            + (f"\n  NOTE — a previous attempt was rejected: {d['notes']}" if d.get("notes") else "")
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
                await manager.deliverable_produced(
                    task_id, d["id"], d.get("description", ""),
                    output[:240].replace("\n", " "),
                )
            else:
                await store.set_deliverable_status(
                    task_id, d["id"], DeliverableStatus.REJECTED, notes=verdict.reason,
                )
        await manager.progress(
            task_id, f"{produced}/{len(pending)} deliverable(s) verified & produced"
        )

    return executor

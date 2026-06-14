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

_MAX_ASSET_CHARS = 20_000
_MAX_VERIFY_CHARS = 8_000


class _Verdict(BaseModel):
    satisfied: bool
    reason: str = ""


def make_task_executor(config, skills: bool = True):
    """Build an executor coroutine for `TaskManager`, using the real agent."""
    from agclaw.agent import create_agent, model_config, turn_prompt

    async def _verify(deliverable: dict, output: str) -> _Verdict:
        """Strictly check produced output against the deliverable's criteria."""
        from autogen.beta import Agent

        model = config.llm.aggregate_model or config.llm.model
        verifier = Agent("deliverable-verifier", config=model_config(config, model))
        prompt = (
            "Judge whether produced output satisfies a requested deliverable.\n\n"
            f"DELIVERABLE: {deliverable['description']}\n"
            f"ACCEPTANCE CRITERIA: {deliverable.get('criteria') or '(none given)'}\n\n"
            f"PRODUCED OUTPUT:\n{output[:_MAX_VERIFY_CHARS]}\n\n"
            "Be strict. satisfied=false if the output only describes what COULD be "
            "done, asks the user a question, offers options instead of doing it, or "
            "says it couldn't complete the work. satisfied=true only if the actual "
            "deliverable content is present and meets the criteria."
        )
        try:
            reply = await verifier.ask(prompt, response_schema=_Verdict)
            return await reply.content()
        except Exception as exc:
            return _Verdict(satisfied=False, reason=f"verification error: {exc}")

    async def executor(task_id, manager, asker) -> None:
        store = manager.store
        task = await store.get(task_id)
        if task is None:
            return
        pending = task.pending_deliverables()
        if not pending:
            return  # pure orchestrator with no own deliverables

        agent = create_agent(config, memory=False, skills=True, asker=asker)

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
                        parts.append(f"### {c.title}\n{a[:4000]}")
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

        produced = 0
        for d in pending:
            verdict = await _verify(d, output)
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

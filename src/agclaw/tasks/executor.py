"""The real (LLM-backed) task executor.

Turns a task's objective + pending deliverables into actual work: it runs the
agent (with the same tools + permission/HITL wiring as a chat turn, bound to the
task's asker), captures the output as the deliverables' assets, and marks them
produced. The runner then gates completion on those deliverables.

Subtask orchestration is handled by the runner (children run first); this
executor produces the deliverables of whichever task it's invoked for — a leaf's
outputs, or a parent's synthesis once its children are done.
"""

from agclaw.tasks.model import DeliverableStatus

_MAX_ASSET_CHARS = 20_000


def make_task_executor(config):
    """Build an executor coroutine for `TaskManager`, using the real agent."""
    from agclaw.agent import create_agent, turn_prompt

    async def executor(task_id, manager, asker) -> None:
        store = manager.store
        task = await store.get(task_id)
        if task is None:
            return
        pending = task.pending_deliverables()
        if not pending:
            return  # nothing to produce (pure orchestrator with no own deliverables)

        # An agent bound to this task's asker → permissions/HITL route to the
        # channel that triggered the task (same gating as a normal turn).
        agent = create_agent(config, memory=False, skills=True, asker=asker)

        wanted = "\n".join(
            f"- {d['description']}" + (f" (acceptance: {d['criteria']})" if d.get("criteria") else "")
            for d in pending
        )
        objective = task.objective or task.title
        context = ""
        children = await store.children(task_id)
        done_kids = [c for c in children if c.status == "completed"]
        if done_kids:
            context = "\n\nCompleted subtasks you can build on: " + ", ".join(
                c.title for c in done_kids
            )
        prompt = (
            f"You are completing a task.\nObjective: {objective}{context}\n\n"
            f"Produce the following deliverable(s), doing the work with your tools:\n{wanted}\n\n"
            "When done, return the deliverable content (or a clear summary of what "
            "you produced and where)."
        )

        await manager.progress(task_id, f"working on {len(pending)} deliverable(s)")
        reply = await agent.ask(prompt, prompt=turn_prompt(config))
        output = (reply.body or "").strip()

        # Record the output as each pending deliverable's asset and mark produced.
        for d in pending:
            await store.set_deliverable_status(
                task_id, d["id"], DeliverableStatus.PRODUCED,
                asset={
                    "name": d["description"][:60],
                    "kind": "text",
                    "content": output[:_MAX_ASSET_CHARS],
                },
            )
        await manager.progress(task_id, "deliverables produced")

    return executor

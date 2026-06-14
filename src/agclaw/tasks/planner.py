"""Task intake & planning.

Turns a raw request into a structured task: classify trivial vs non-trivial, ask
clarifying questions over HITL (non-trivial only), then set the objective and
create the deliverables + subtasks. The LLM produces a `TaskPlan` via structured
output; the application of that plan to the store and the HITL intake are plain,
testable logic.
"""

from pydantic import BaseModel, Field

from agclaw.hitl.base import Question
from agclaw.tasks.model import TaskStatus
from agclaw.tasks.store import TaskStore


class ClarifyQuestion(BaseModel):
    text: str
    options: list[str] = Field(default_factory=list)  # empty → free-text answer


class PlanDeliverable(BaseModel):
    description: str
    criteria: str = ""


class PlanSubtask(BaseModel):
    title: str
    description: str = ""
    deliverable: str = ""   # what this subtask must produce
    criteria: str = ""      # acceptance criteria for that output


class TaskPlan(BaseModel):
    """The agent's structured plan for a task."""

    trivial: bool = False
    objective: str = ""
    questions: list[ClarifyQuestion] = Field(default_factory=list)
    deliverables: list[PlanDeliverable] = Field(default_factory=list)
    subtasks: list[PlanSubtask] = Field(default_factory=list)


_PLAN_PROMPT = """You are planning a task for a personal assistant.

Given the user's request, decide whether it is TRIVIAL (a quick lookup or action
needing no clarification, e.g. "any unread emails?") or NON-TRIVIAL (a larger job
that benefits from a few clarifying questions first, e.g. "research X and prepare
a presentation").

Produce a plan:
- trivial: true/false
- objective: one concise sentence describing what "done" looks like
- questions: ONLY if non-trivial — 2 to 5 clarifying questions that materially
  change the work (scope, audience, format, depth, deadline). Give options when
  the answer is naturally a choice; otherwise leave options empty for free text.
- deliverables: the concrete outputs the OVERALL task must produce, each with
  acceptance criteria. Prefer outputs the assistant can actually produce with its
  tools (research, summaries, drafts, markdown, code) — not things needing an
  external app it has no tool for.
- subtasks: break a non-trivial job into a few independent pieces of work; give
  each subtask its own `deliverable` (what that piece must produce) and criteria.

Keep it tight. Trivial tasks need no questions, no subtasks, and one deliverable.

User request:
{request}"""


async def make_plan(agent, request: str) -> TaskPlan:
    """Ask the agent for a structured plan (LLM call)."""
    reply = await agent.ask(_PLAN_PROMPT.format(request=request), response_schema=TaskPlan)
    return await reply.content()


async def run_intake(store: TaskStore, task_id: str, plan: TaskPlan, asker) -> dict:
    """Ask the plan's clarifying questions over HITL; store the answers."""
    answers: dict = {}
    for q in plan.questions:
        try:
            ans = await asker.ask(
                Question(text=q.text, options=q.options or None, kind="question")
            )
        except Exception:
            ans = ""
        if ans:
            answers[q.text] = ans
    if answers:
        await store.update(task_id, intake=answers)
    return answers


async def apply_plan(store: TaskStore, task_id: str, plan: TaskPlan) -> None:
    """Write objective, deliverables, and subtasks from a plan into the store."""
    await store.update(task_id, objective=plan.objective, plan=[s.title for s in plan.subtasks])
    for d in plan.deliverables:
        await store.add_deliverable(task_id, d.description, d.criteria)
    for s in plan.subtasks:
        child = await store.add_subtask(task_id, s.title, s.description, reopen_parent=False)
        # every subtask must produce something, or it does no real work
        await store.add_deliverable(
            child.id, s.deliverable or f"Output of: {s.title}", s.criteria
        )


def _request_with_answers(request: str, answers: dict) -> str:
    if not answers:
        return request
    qa = "\n".join(f"- {q} → {a}" for q, a in answers.items())
    return f"{request}\n\nClarifications the user provided:\n{qa}"


async def prepare_task(store: TaskStore, task_id: str, agent, asker=None) -> None:
    """Full intake: plan → (clarify via HITL if non-trivial) → objective+deliverables+subtasks.

    Leaves the task PENDING and ready for the runner to execute.
    """
    task = await store.get(task_id)
    if task is None:
        return
    request = task.description or task.title

    plan = await make_plan(agent, request)
    if not plan.trivial and plan.questions and asker is not None:
        await store.set_status(task_id, TaskStatus.AWAITING_INPUT)
        answers = await run_intake(store, task_id, plan, asker)
        if answers:
            # re-plan with the clarifications so deliverables/subtasks reflect them
            plan = await make_plan(agent, _request_with_answers(request, answers))

    await store.set_status(task_id, TaskStatus.PLANNING)
    await apply_plan(store, task_id, plan)
    await store.set_status(task_id, TaskStatus.PENDING)

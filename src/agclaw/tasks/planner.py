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
    capabilities: list[str] = Field(default_factory=list)  # tools this piece needs


class TaskPlan(BaseModel):
    """The agent's structured plan for a task."""

    trivial: bool = False
    objective: str = ""
    questions: list[ClarifyQuestion] = Field(default_factory=list)
    deliverables: list[PlanDeliverable] = Field(default_factory=list)
    subtasks: list[PlanSubtask] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)  # tools the top-level work needs


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
- deliverables: the FINAL outputs of the overall task (e.g. the briefing, the
  report, the itinerary), each with acceptance criteria. These belong to the task
  itself and are produced LAST by combining the subtasks. Prefer outputs the
  assistant can actually produce with its tools (research, summaries, drafts,
  markdown, code) — not things needing an external app it has no tool for.
- subtasks: break a non-trivial job into a few INDEPENDENT research/work pieces
  that feed the final deliverables; give each its own intermediate `deliverable`
  (e.g. "research notes on X") and criteria. Do NOT create a "compile" or
  "synthesise" subtask — the final deliverable above is the synthesis and is
  produced from the subtasks automatically.
- capabilities: for the top-level work AND for each subtask, list ONLY the tool
  groups it genuinely needs, chosen from: {capabilities}. Use the fewest that fit
  — e.g. factual research → ["web"]; running/calculating → ["code"]; the user's
  calendar → ["calendar"]; their Drive files → ["drive"]; reading a local file →
  ["files"]. A pure writing/synthesis step that only combines other results needs
  NONE ([]). Never request a capability the work doesn't need (e.g. don't add
  "drive" to web research).

Keep it tight. Trivial tasks need no questions, no subtasks, and one deliverable.

User request:
{request}"""


async def make_plan(agent, request: str, capabilities: list[str] | None = None) -> TaskPlan:
    """Ask the agent for a structured plan (LLM call)."""
    from agclaw.tools import available_capabilities

    caps = capabilities if capabilities is not None else available_capabilities()
    prompt = _PLAN_PROMPT.format(request=request, capabilities=caps)
    reply = await agent.ask(prompt, response_schema=TaskPlan)
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


def _valid_caps(caps: list[str]) -> list[str]:
    from agclaw.tools import CAPABILITIES

    return [c for c in caps if c in CAPABILITIES]


async def apply_plan(store: TaskStore, task_id: str, plan: TaskPlan) -> None:
    """Write objective, deliverables, subtasks, and capability scopes from a plan."""
    await store.update(
        task_id,
        objective=plan.objective,
        plan=[s.title for s in plan.subtasks],
        capabilities=_valid_caps(plan.capabilities),
    )
    for d in plan.deliverables:
        await store.add_deliverable(task_id, d.description, d.criteria)
    for s in plan.subtasks:
        child = await store.add_subtask(
            task_id, s.title, s.description, reopen_parent=False,
            capabilities=_valid_caps(s.capabilities),
        )
        # every subtask must produce something, or it does no real work
        await store.add_deliverable(
            child.id, s.deliverable or f"Output of: {s.title}", s.criteria
        )


def _request_with_answers(request: str, answers: dict) -> str:
    if not answers:
        return request
    qa = "\n".join(f"- {q} → {a}" for q, a in answers.items())
    return f"{request}\n\nClarifications the user provided:\n{qa}"


async def prepare_task(
    store: TaskStore, task_id: str, agent, asker=None,
    capabilities: list[str] | None = None,
) -> None:
    """Full intake: plan → (clarify via HITL if non-trivial) → objective+deliverables+subtasks.

    `capabilities` optionally restricts the tool groups the planner may assign
    (e.g. to exclude 'gmail'). Leaves the task PENDING, ready for the runner.
    """
    task = await store.get(task_id)
    if task is None:
        return
    request = task.description or task.title

    plan = await make_plan(agent, request, capabilities)
    if not plan.trivial and plan.questions and asker is not None:
        await store.set_status(task_id, TaskStatus.AWAITING_INPUT)
        answers = await run_intake(store, task_id, plan, asker)
        if answers:
            # re-plan with the clarifications so deliverables/subtasks reflect them
            plan = await make_plan(agent, _request_with_answers(request, answers), capabilities)

    await store.set_status(task_id, TaskStatus.PLANNING)
    await apply_plan(store, task_id, plan)
    await store.set_status(task_id, TaskStatus.PENDING)

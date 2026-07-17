"""Task intake & planning.

Turns a raw request into a structured task: classify trivial vs non-trivial, ask
clarifying questions over HITL (non-trivial only), then set the objective and
create the deliverables + subtasks. The LLM produces a `TaskPlan` via structured
output; the application of that plan to the store and the HITL intake are plain,
testable logic.
"""

from pydantic import BaseModel, Field

from assistant.hitl.base import Question
from assistant.tasks.model import TaskStatus
from assistant.tasks.store import TaskStore


class ClarifyQuestion(BaseModel):
    text: str
    options: list[str] = Field(default_factory=list)  # empty → free-text answer


class PlanDeliverable(BaseModel):
    description: str
    criteria: str = ""


class PlanSubtask(BaseModel):
    title: str
    description: str = ""
    deliverable: str = ""  # what this subtask must produce
    criteria: str = ""  # acceptance criteria for that output
    capabilities: list[str] = Field(default_factory=list)  # tools this piece needs


class TaskPlan(BaseModel):
    """The agent's structured plan for a task."""

    trivial: bool = False
    title: str = ""  # concise display name for the task (refines the raw request)
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
- title: a concise 2–6 word name for the task, Title Case, no quotes or trailing
  punctuation (e.g. "Sydney Trip Itinerary", "Weekly AI News Digest")
- objective: one concise sentence describing what "done" looks like
- questions: ONLY if non-trivial — 2 to 5 clarifying questions that materially
  change the work (scope, audience, format, depth, deadline). Give options when
  the answer is naturally a choice; otherwise leave options empty for free text.
- deliverables: the FINAL outputs of the overall task (e.g. the briefing, the
  report, the itinerary), each with acceptance criteria. These belong to the task
  itself and are produced LAST by combining the subtasks. Prefer outputs the
  assistant can actually produce with its tools (research, summaries, drafts,
  markdown, code) — not things needing an external app it has no tool for.
  Keep the set EFFICIENT: combine wherever possible, so every deliverable is a
  genuinely DISTINCT artifact and the list is only as long as the request really
  needs. Never restate one output twice in different words, and never split out a
  deliverable that merely names the format, component, depth or DESTINATION of
  another. All of these are ONE deliverable each, not two:
    - "a news briefing" + "a bullet-point briefing on the same news"
    - "a summary of emails needing a reply" + "an InboxBrief of those messages"
    - "a repo activity summary" + "that summary saved to the workspace as Markdown"
  Saving, exporting or filing an output is NOT a second deliverable — it belongs in
  the criteria of the one deliverable it applies to. A report and a slide deck ARE
  two, because they are different artifacts. Fold format, component, depth, style
  and where it gets saved into a deliverable's description and criteria rather than
  adding another — each extra deliverable makes the assistant redo and re-save the
  same work.
- subtasks: break a non-trivial job into a few INDEPENDENT research/work pieces
  that feed the final deliverables; give each its own intermediate `deliverable`
  (e.g. "research notes on X") and criteria. Do NOT create a "compile" or
  "synthesise" subtask — the final deliverable above is the synthesis and is
  produced from the subtasks automatically.
- capabilities: for the top-level work AND for each subtask, list the capability
  groups it genuinely needs, chosen from the catalogue below. A group grants the
  work every tool it holds; a group left out means that work simply cannot do that
  kind of thing, so read each description and pick the groups that COVER the work.
  Each group you add also widens what this task can reach into — the user's mail,
  their files, their Drive — so include one only where the work would fail without
  it. A step that only reasons or writes, combining results already gathered into
  the deliverable text and saving nothing, needs none ([]).

Capability groups available for this request:
{capabilities}

Keep it tight. Trivial tasks need no questions, no subtasks, and one deliverable.

User request:
{request}"""


async def make_plan(agent, request: str, capabilities: list[str] | None = None) -> TaskPlan:
    """Ask the agent for a structured plan (LLM call)."""
    from assistant.tools import available_capabilities, capability_catalogue

    caps = capabilities if capabilities is not None else available_capabilities()
    prompt = _PLAN_PROMPT.format(request=request, capabilities=capability_catalogue(caps))
    reply = await agent.ask(prompt, response_schema=TaskPlan)
    return await reply.content()


async def run_intake(store: TaskStore, task_id: str, plan: TaskPlan, asker) -> dict:
    """Ask the plan's clarifying questions over HITL; store the answers."""
    answers: dict = {}
    for q in plan.questions:
        try:
            ans = await asker.ask(Question(text=q.text, options=q.options or None, kind="question"))
        except Exception:
            ans = ""
        if ans:
            answers[q.text] = ans
    if answers:
        await store.update(task_id, intake=answers)
    return answers


def _valid_caps(caps: list[str]) -> list[str]:
    from assistant.tools import CAPABILITIES

    return [c for c in caps if c in CAPABILITIES]


async def apply_plan(store: TaskStore, task_id: str, plan: TaskPlan) -> None:
    """Write objective, deliverables, subtasks, and capability scopes from a plan."""
    updates = {
        "objective": plan.objective,
        "plan": [s.title for s in plan.subtasks],
        "capabilities": _valid_caps(plan.capabilities),
    }
    if title := plan.title.strip():  # refine the display title (raw request → concise)
        updates["title"] = title[:80]
    await store.update(task_id, **updates)
    for d in plan.deliverables:
        await store.add_deliverable(task_id, d.description, d.criteria)
    for s in plan.subtasks:
        child = await store.add_subtask(
            task_id,
            s.title,
            s.description,
            reopen_parent=False,
            capabilities=_valid_caps(s.capabilities),
        )
        # every subtask must produce something, or it does no real work
        await store.add_deliverable(child.id, s.deliverable or f"Output of: {s.title}", s.criteria)


def _request_with_answers(request: str, answers: dict) -> str:
    if not answers:
        return request
    qa = "\n".join(f"- {q} → {a}" for q, a in answers.items())
    return f"{request}\n\nClarifications the user provided:\n{qa}"


# Cap on clarification rounds: a hopelessly vague request can't loop forever.
# After this many rounds we proceed with whatever was gathered (best effort).
_MAX_INTAKE_ROUNDS = 4


async def prepare_task(
    store: TaskStore,
    task_id: str,
    agent,
    asker=None,
    capabilities: list[str] | None = None,
) -> None:
    """Full intake: plan → (iteratively clarify via HITL) → objective+deliverables+subtasks.

    Clarification is a LOOP, not a single pass: while the agent's plan still has
    open questions, we keep asking the user and re-planning with the *accumulated*
    answers — so a vague request ("help me get ready for my trip") drives several
    rounds of back-and-forth before any work starts. The loop ends when the plan
    converges (no more questions / trivial), the user abandons it (stops
    answering → task CANCELLED), or we hit `_MAX_INTAKE_ROUNDS`.

    `capabilities` optionally restricts the tool groups the planner may assign
    (e.g. to exclude 'gmail'). Leaves the task PENDING, ready for the runner.
    """
    task = await store.get(task_id)
    if task is None:
        return
    request = task.description or task.title

    plan = await make_plan(agent, request, capabilities)

    answers: dict = {}
    rounds = 0
    while asker is not None and not plan.trivial and plan.questions and rounds < _MAX_INTAKE_ROUNDS:
        await store.set_status(task_id, TaskStatus.AWAITING_INPUT)
        new = await run_intake(store, task_id, plan, asker)
        rounds += 1
        if not new:
            # the user gave no answers this whole round → treat as abandonment
            await store.set_status(
                task_id,
                TaskStatus.CANCELLED,
                error="abandoned during clarification (no answers given)",
            )
            return
        answers.update(new)
        await store.update(task_id, intake=dict(answers))
        # The moment they answer we're re-planning, not waiting — reflect that so
        # the status doesn't look stuck on "awaiting input" during the LLM call.
        await store.set_status(task_id, TaskStatus.PLANNING)
        # re-plan with everything gathered so far; this may surface FEWER
        # questions (converging) or new ones if the answers opened up scope.
        plan = await make_plan(agent, _request_with_answers(request, answers), capabilities)

    await store.set_status(task_id, TaskStatus.PLANNING)
    await apply_plan(store, task_id, plan)
    await store.set_status(task_id, TaskStatus.PENDING)

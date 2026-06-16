"""Tests for task intake & planning (clarify → objective/deliverables/subtasks)."""

import pytest

from agclaw.tasks import TaskStatus, TaskStore
from agclaw.tasks.planner import (
    ClarifyQuestion,
    PlanDeliverable,
    PlanSubtask,
    TaskPlan,
    apply_plan,
    prepare_task,
    run_intake,
)


def _store(tmp_path):
    return TaskStore(path=tmp_path / "tasks.db")


class _FakeAsker:
    def __init__(self, answers):
        self._answers = list(answers)
        self.asked = []

    async def ask(self, question, timeout=None):
        self.asked.append(question)
        return self._answers.pop(0)


class _FakeReply:
    def __init__(self, plan):
        self._plan = plan

    async def content(self):
        return self._plan


class _FakeAgent:
    """Returns canned plans in sequence (one per make_plan call)."""

    def __init__(self, plans):
        self._plans = list(plans)
        self.asks = 0

    async def ask(self, msg, response_schema=None, **kwargs):
        self.asks += 1
        return _FakeReply(self._plans.pop(0))


async def test_apply_plan_writes_objective_deliverables_subtasks(tmp_path):
    store = _store(tmp_path)
    t = await store.create("IPO research")
    plan = TaskPlan(
        objective="Deck on Anthropic & OpenAI IPOs",
        deliverables=[PlanDeliverable(description="slide deck", criteria="10 slides")],
        subtasks=[PlanSubtask(title="Research Anthropic"), PlanSubtask(title="Research OpenAI")],
    )
    await apply_plan(store, t.id, plan)
    got = await store.get(t.id)
    assert got.objective == "Deck on Anthropic & OpenAI IPOs"
    assert got.deliverables[0]["description"] == "slide deck"
    assert got.plan == ["Research Anthropic", "Research OpenAI"]
    kids = await store.children(t.id)
    assert {k.title for k in kids} == {"Research Anthropic", "Research OpenAI"}


async def test_run_intake_collects_answers(tmp_path):
    store = _store(tmp_path)
    t = await store.create("deck")
    plan = TaskPlan(
        questions=[
            ClarifyQuestion(text="Audience?", options=["Execs", "Engineers"]),
            ClarifyQuestion(text="How many slides?"),
        ]
    )
    asker = _FakeAsker(["Execs", "12"])
    answers = await run_intake(store, t.id, plan, asker)
    assert answers == {"Audience?": "Execs", "How many slides?": "12"}
    assert (await store.get(t.id)).intake == answers
    # options were passed through on the first question
    assert asker.asked[0].options == ["Execs", "Engineers"]


async def test_prepare_trivial_task_skips_intake(tmp_path):
    store = _store(tmp_path)
    t = await store.create("any unread emails?")
    plan = TaskPlan(trivial=True, objective="Report unread emails",
                    deliverables=[PlanDeliverable(description="summary of unread")])
    agent = _FakeAgent([plan])
    asker = _FakeAsker([])
    await prepare_task(store, t.id, agent, asker)
    got = await store.get(t.id)
    assert agent.asks == 1  # only the plan call, no re-plan
    assert asker.asked == []  # no clarifying questions
    assert got.objective == "Report unread emails"
    assert got.status == TaskStatus.PENDING


async def test_answering_flips_status_off_awaiting_input(tmp_path):
    """The instant the user answers, status becomes PLANNING (not stuck on
    awaiting_input) while the answer is re-planned."""
    store = _store(tmp_path)
    t = await store.create("vague job")
    seen = []

    class _RecordingAgent:  # records the task's status at each plan/re-plan call
        def __init__(self, plans):
            self._plans = list(plans)
            self.asks = 0

        async def ask(self, msg, response_schema=None, **kwargs):
            seen.append((await store.get(t.id)).status)
            self.asks += 1
            return _FakeReply(self._plans.pop(0))

    plan1 = TaskPlan(trivial=False, objective="p", questions=[ClarifyQuestion(text="Which?")])
    plan2 = TaskPlan(trivial=False, objective="done", deliverables=[PlanDeliverable(description="x")])
    await prepare_task(store, t.id, _RecordingAgent([plan1, plan2]), _FakeAsker(["A"]))

    assert seen[1] == TaskStatus.PLANNING       # re-plan after the answer, not awaiting_input
    assert (await store.get(t.id)).status == TaskStatus.PENDING


async def test_prepare_nontrivial_task_clarifies_then_replans(tmp_path):
    store = _store(tmp_path)
    t = await store.create("research the IPOs and make a deck")
    plan1 = TaskPlan(
        trivial=False,
        objective="provisional",
        questions=[ClarifyQuestion(text="Audience?", options=["Execs", "Engineers"])],
    )
    plan2 = TaskPlan(
        trivial=False,
        objective="Exec deck on the IPOs",
        deliverables=[PlanDeliverable(description="slide deck", criteria="for execs")],
        subtasks=[PlanSubtask(title="Research Anthropic"), PlanSubtask(title="Research OpenAI")],
    )
    agent = _FakeAgent([plan1, plan2])
    asker = _FakeAsker(["Execs"])
    await prepare_task(store, t.id, agent, asker)
    got = await store.get(t.id)
    assert agent.asks == 2  # planned, asked, re-planned with the answer
    assert got.intake == {"Audience?": "Execs"}
    assert got.objective == "Exec deck on the IPOs"
    assert len(await store.children(t.id)) == 2
    assert got.status == TaskStatus.PENDING


async def test_prepare_iterates_clarification_until_plan_converges(tmp_path):
    """A vague request drives MULTIPLE rounds of HITL — keep asking and re-planning
    until the plan has no more open questions (involve the user as much as needed)."""
    store = _store(tmp_path)
    t = await store.create("Help me get ready for my trip.")
    plan1 = TaskPlan(trivial=False, objective="provisional",
                     questions=[ClarifyQuestion(text="Where are you going?")])
    plan2 = TaskPlan(trivial=False, objective="provisional",
                     questions=[ClarifyQuestion(text="When, and for how long?")])
    plan3 = TaskPlan(  # converged — no more questions
        trivial=False, objective="Trip-prep checklist for Lisbon",
        deliverables=[PlanDeliverable(description="packing + prep checklist")],
        subtasks=[PlanSubtask(title="Lisbon weather + essentials")],
    )
    agent = _FakeAgent([plan1, plan2, plan3])
    asker = _FakeAsker(["Lisbon", "Next week, 5 days"])
    await prepare_task(store, t.id, agent, asker)
    got = await store.get(t.id)
    assert agent.asks == 3  # plan, replan, replan — three rounds
    assert len(asker.asked) == 2  # asked across two clarification rounds
    assert got.intake == {"Where are you going?": "Lisbon",
                          "When, and for how long?": "Next week, 5 days"}
    assert got.status == TaskStatus.PENDING
    assert got.objective == "Trip-prep checklist for Lisbon"


async def test_prepare_abandons_when_user_stops_answering(tmp_path):
    """If the user gives no answers to a clarification round, the task is abandoned
    (CANCELLED) rather than proceeding on guesses."""
    store = _store(tmp_path)
    t = await store.create("Help me get ready for my trip.")
    plan = TaskPlan(trivial=False, objective="provisional",
                    questions=[ClarifyQuestion(text="Where are you going?")])
    agent = _FakeAgent([plan])
    asker = _FakeAsker([""])  # user declines to answer
    await prepare_task(store, t.id, agent, asker)
    got = await store.get(t.id)
    assert got.status == TaskStatus.CANCELLED
    assert agent.asks == 1  # no re-plan after abandonment


async def test_prepare_caps_clarification_rounds(tmp_path):
    """A request that never stops generating questions still terminates: after the
    round cap we proceed with what we have rather than looping forever."""
    from agclaw.tasks.planner import _MAX_INTAKE_ROUNDS

    store = _store(tmp_path)
    t = await store.create("vague forever")
    plans = [  # every plan returns a fresh question
        TaskPlan(trivial=False, objective="p",
                 questions=[ClarifyQuestion(text=f"q{i}?")])
        for i in range(_MAX_INTAKE_ROUNDS + 2)
    ]
    agent = _FakeAgent(plans)
    asker = _FakeAsker([f"a{i}" for i in range(_MAX_INTAKE_ROUNDS + 2)])
    await prepare_task(store, t.id, agent, asker)
    got = await store.get(t.id)
    assert len(asker.asked) == _MAX_INTAKE_ROUNDS  # asked exactly the cap, then stopped
    assert got.status == TaskStatus.PENDING


@pytest.mark.integration
async def test_make_plan_real_llm(tmp_path):
    from agclaw.agent import create_agent
    from agclaw.config import load_config
    from agclaw.tasks.planner import make_plan

    agent = create_agent(load_config(), memory=False, skills=False)
    plan = await make_plan(agent, "Find any unread emails from today")
    assert isinstance(plan.objective, str) and plan.objective
    assert isinstance(plan.trivial, bool)


@pytest.mark.integration
async def test_task_end_to_end_real(tmp_path):
    """Plan a simple task, then run it with the real executor → completed."""
    from agclaw.agent import create_agent
    from agclaw.config import load_config
    from agclaw.tasks import TaskManager, TaskStatus, make_task_executor
    from agclaw.tasks.planner import prepare_task

    cfg = load_config()
    store = TaskStore(path=tmp_path / "tasks.db")
    agent = create_agent(cfg, memory=False, skills=False)

    t = await store.create("Tell me a one-sentence fun fact about octopuses")
    await prepare_task(store, t.id, agent, asker=None)  # trivial → no intake

    mgr = TaskManager(store, make_task_executor(cfg))
    await mgr.submit(t.id)
    await mgr.wait(t.id)

    got = await store.get(t.id)
    assert got.status == TaskStatus.COMPLETED, got.error
    assert got.deliverables and got.deliverables[0]["status"] == "produced"
    assert got.deliverables[0]["asset"]["content"]

"""Owner-side approvals for ACP turns.

A gated tool call during an ACP-driven turn must be approved on the *owner's*
own surface — never the ACP client (see
ADR 0033). These tests
drive a real ``ag2.acp`` connection in-process (``ag2.acp.testing.connect``, the
same harness ``test_acp_serve.py`` uses) against a real ``Gateway``+``Agent``,
with ``ag2.testing.TestConfig`` scripting the model turns — no real LLM.
"""

import asyncio

import acp
from ag2 import Agent, Context, tool
from ag2.acp import ACPAgent
from ag2.acp.testing import connect
from ag2.events import ToolCallEvent, ToolResultEvent
from ag2.testing import TestConfig

from assistant.acp.approvals import install_owner_side_approvals
from assistant.gateway.core import Gateway
from assistant.hitl.base import Question
from assistant.permissions import ALLOW_ONCE, DENY, PermissionManager

_VERSION = "0.0.0-test"

# The exact prompt text check_command() builds — asserted absent from every
# session/update, catching a regression that leaks it to the ACP client.
_APPROVAL_MARKER = "Allow AG2 Assistant to run"


class FakeOwnerAsker:
    """A scripted or hanging stand-in for the owner's own Asker surface — never
    the ACP client. ``hang=True`` never resolves on its own; only cancelling the
    waiting task ends it — deny-on-cancel (ADR 0033)."""

    def __init__(self, *, answer: str | None = None, hang: bool = False) -> None:
        self.questions: list[Question] = []
        self._answer = answer
        self._hang = hang

    async def ask(self, question: Question, timeout: float | None = None) -> str:
        self.questions.append(question)
        if self._hang:
            await asyncio.Event().wait()
        assert self._answer is not None
        return self._answer


def _build_gated_tool(side_effects: list[str]):
    """A minimal gated tool: the side effect only runs once the turn-level
    ``PermissionManager`` (installed by ``install_owner_side_approvals``) allows it."""

    @tool
    async def sensitive_action(context: Context) -> str:
        """Do something that needs the owner's approval."""
        pm = context.dependencies.get(PermissionManager) or PermissionManager()
        if await pm.check_command("sensitive_action", "{}"):
            side_effects.append("ran")
            return "ran the sensitive action"
        return "denied by the owner"

    return sensitive_action


def _agent_factory(gated_tool, *events):
    """A ``create_agent``-shaped factory (see ``Gateway._make_agent``) handing out a
    bare ``Agent`` scripted with ``TestConfig`` — no LLM, no other tools."""

    def factory(cfg, **kwargs):
        return Agent(name="acp-approvals-test", config=TestConfig(*events), tools=[gated_tool])

    return factory


async def _started_gateway(config, gated_tool, *events) -> Gateway:
    gateway = Gateway(
        config=config,
        memory=False,
        platform="acp",
        agent_factory=_agent_factory(gated_tool, *events),
    )
    await gateway.start()
    return gateway


async def _wait_until(predicate, *, timeout: float = 5.0) -> None:
    async def _poll():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout=timeout)


def _assert_no_approval_leaked(recorder, session_id: str) -> None:
    """The ACP client must never see the approval — it's answered on the owner's
    own surface, never round-tripped through session/update."""
    raw = "\n".join(u.model_dump_json() for u in recorder.updates_for(session_id))
    assert _APPROVAL_MARKER not in raw


async def test_allow_runs_the_gated_tool_via_the_owner_asker(config):
    side_effects: list[str] = []
    fake_asker = FakeOwnerAsker(answer=ALLOW_ONCE)
    gateway = await _started_gateway(
        config,
        _build_gated_tool(side_effects),
        ToolCallEvent(id="c1", name="sensitive_action", arguments="{}"),
        "done",
    )
    agent = gateway.require_agent()
    install_owner_side_approvals(agent, gateway, fake_asker)
    acp_agent = ACPAgent(agent, name="AG2 Assistant", version=_VERSION)
    try:
        async with connect(acp_agent) as (client, recorder):
            session = await client.new_session(cwd=".")
            response = await client.prompt(
                session_id=session.session_id, prompt=[acp.text_block("go")]
            )
            assert response.stop_reason == "end_turn"
            _assert_no_approval_leaked(recorder, session.session_id)
    finally:
        await gateway.close()

    assert side_effects == ["ran"]
    # The Question reached the owner-side asker — kind="permission", not free text.
    assert [q.kind for q in fake_asker.questions] == ["permission"]


async def test_deny_fails_the_tool_call_and_session_stays_usable(config):
    side_effects: list[str] = []
    fake_asker = FakeOwnerAsker(answer=DENY)
    gateway = await _started_gateway(
        config,
        _build_gated_tool(side_effects),
        ToolCallEvent(id="c1", name="sensitive_action", arguments="{}"),
        "acknowledged",
        ToolCallEvent(id="c2", name="sensitive_action", arguments="{}"),
        "acknowledged again",
    )
    agent = gateway.require_agent()
    install_owner_side_approvals(agent, gateway, fake_asker)
    acp_agent = ACPAgent(agent, name="AG2 Assistant", version=_VERSION)
    try:
        async with connect(acp_agent) as (client, recorder):
            session = await client.new_session(cwd=".")
            first = await client.prompt(
                session_id=session.session_id, prompt=[acp.text_block("go")]
            )
            assert first.stop_reason == "end_turn"
            # Session usable: a second turn still asks — a stale denial carried
            # over from turn 1 would skip the ask (permissions.py's `_any_denied`).
            second = await client.prompt(
                session_id=session.session_id, prompt=[acp.text_block("again")]
            )
            assert second.stop_reason == "end_turn"
            _assert_no_approval_leaked(recorder, session.session_id)
    finally:
        await gateway.close()

    assert side_effects == []
    assert [q.kind for q in fake_asker.questions] == ["permission", "permission"]


async def test_cancel_mid_approval_denies_with_no_side_effect_and_no_hang(config):
    side_effects: list[str] = []
    fake_asker = FakeOwnerAsker(hang=True)
    gateway = await _started_gateway(
        config,
        _build_gated_tool(side_effects),
        ToolCallEvent(id="c1", name="sensitive_action", arguments="{}"),
    )
    agent = gateway.require_agent()
    install_owner_side_approvals(agent, gateway, fake_asker)
    acp_agent = ACPAgent(agent, name="AG2 Assistant", version=_VERSION)
    try:
        async with connect(acp_agent) as (client, recorder):
            session = await client.new_session(cwd=".")
            prompt_task = asyncio.ensure_future(
                client.prompt(session_id=session.session_id, prompt=[acp.text_block("go")])
            )
            # Cancel once the turn is actually parked on the (hanging) approval.
            await _wait_until(lambda: fake_asker.questions)
            await client.cancel(session_id=session.session_id)

            # Ends the whole turn — see approvals.py's module docstring for why.
            response = await asyncio.wait_for(prompt_task, timeout=5)
            assert response.stop_reason == "cancelled"

            session_obj = await acp_agent.sessions.get(session.session_id)
            stream = acp_agent.sessions.stream(session_obj)
            events = await stream.history.get_events()
            texts = [
                part.content
                for e in events
                if isinstance(e, ToolResultEvent)
                for part in e.result.parts
                if hasattr(part, "content")
            ]
            assert any("approval not obtained" in t.lower() for t in texts)

            _assert_no_approval_leaked(recorder, session.session_id)
    finally:
        await gateway.close()

    assert side_effects == []

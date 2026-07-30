"""Fake collaborators and the factories that hand them out.

Everything the gateway would otherwise reach the network for — the turn agent, a
platform channel, the one-shot cheap-model helpers — is injected, so a test picks
its own stand-in instead of patching a module attribute.
"""

import asyncio


class FakeReply:
    """Minimal stand-in for AgentReply."""

    def __init__(self, body: str):
        self.body = body


class FakeRun:
    """Stand-in for AG2's ``AgentRun`` — the turn handle the gateway drives.

    Mirrors the contract the gateway relies on: ``result()`` is driven by a task the
    caller can cancel (cancelling the await cancels the turn), ``enqueue`` appends to
    the *stream's* inbox (that's where AG2 keeps it, which is why a fed message is
    drained by the running turn), and the scope cancels a still-running turn on exit.
    The turn itself is whatever the fake agent's ``ask`` does.
    """

    def __init__(self, agent, msg, kwargs):
        self._agent = agent
        self._msg = msg
        self._kwargs = kwargs
        self._task = None
        self.stream = kwargs.get("stream")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        if self._task is not None and not self._task.done():
            self._task.cancel()
        return False

    def _ensure(self):
        if self._task is None:
            self._task = asyncio.ensure_future(self._agent.ask(*self._msg, **self._kwargs))
        return self._task

    def start(self) -> None:
        self._ensure()

    def enqueue(self, *content) -> None:
        if self.stream is not None:
            self.stream.enqueue(*content)

    async def result(self):
        task = self._ensure()
        try:
            return await task
        except asyncio.CancelledError:
            task.cancel()
            raise


class FakeRunMixin:
    """Gives an ``ask``-only fake agent the ``run()`` surface the gateway drives, so a
    fake still only has to define ``ask`` (AG2's ``ask`` is likewise ``run`` + result)."""

    def run(self, *msg, **kwargs) -> FakeRun:
        return FakeRun(self, msg, kwargs)


class FakeAgent(FakeRunMixin):
    """Deterministic fake agent: echo[N] proves per-chat continuity; empty tools."""

    def __init__(self):
        self._counts: dict = {}
        self.tools = []

    async def ask(self, *msg, stream=None, **kwargs) -> FakeReply:
        sid = getattr(stream, "id", "default")
        self._counts[sid] = self._counts.get(sid, 0) + 1
        return FakeReply(f"echo[{self._counts[sid]}]: {msg[0]}")


class FakeChannel:
    """Stand-in Channel: records start/stop/notify without touching a network."""

    def __init__(self, platform: str, **tokens):
        self.platform = platform
        self.tokens = tokens
        self.started = False
        self.stopped = False
        self.sent: list[tuple[str, str]] = []

    async def start(self, gateway) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def notify(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class FakeStructuredAgent:
    """Stand-in for a one-shot cheap-model agent (chat titles, run summaries): the
    ``ask_structured`` native path, returning a canned structured result."""

    config = object()  # a real ag2.Agent always carries one; picks the native path

    def __init__(self, out):
        self._out = out

    async def ask(self, prompt, response_schema=None):
        class _Reply:
            async def content(_self):
                return self._out

        return _Reply()


def fake_agent_factory(agent=None, built=None):
    """A ``create_agent``-shaped factory handing out fakes, so no runtime touches an
    LLM. ``agent`` may be an agent instance (reused for every profile) or a callable
    building one; omitted, each call gets a fresh ``FakeAgent``. Pass ``built`` to
    collect the config every build was asked for — an agent rebuild is how a runtime
    reload is observed."""

    def factory(config, **kwargs):
        if built is not None:
            built.append(config)
        if agent is None:
            return FakeAgent()
        return agent(config, **kwargs) if callable(agent) else agent

    return factory


def fake_channel_factory(made=None):
    """A ``get_channel``-shaped factory handing out ``FakeChannel``s. Pass a list to
    collect every channel it builds."""

    def factory(platform, **tokens):
        channel = FakeChannel(platform, **tokens)
        if made is not None:
            made.append(channel)
        return channel

    return factory


def _canned(**fields):
    """A bare object carrying ``fields`` — what ``ask_structured`` hands back."""
    return type("Out", (), fields)()


def fake_title_factory(title="Fake Title"):
    """A titler factory whose one-shot agent always answers ``title``."""
    return lambda config: FakeStructuredAgent(_canned(title=title))


def fake_summary_factory(summary="Fake summary.", name="Fake Task", description=""):
    """A distiller factory for run summaries AND task auto-naming (one fake answers
    both schemas — each reader picks the field it needs)."""
    return lambda config: FakeStructuredAgent(
        _canned(summary=summary, name=name, description=description)
    )

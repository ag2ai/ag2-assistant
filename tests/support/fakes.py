"""Fake collaborators and the factories that hand them out.

Everything the gateway would otherwise reach the network for — the turn agent, a
platform channel, the one-shot cheap-model helpers — is injected, so a test picks
its own stand-in instead of patching a module attribute.
"""

import asyncio
import types
from contextlib import asynccontextmanager
from pathlib import PurePosixPath

from ag2.testing import TestClient, TestConfig
from ag2.tools.sandbox.base import ExecResult
from fast_depends import Provider


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

    # The static prompt a real agent is built with: its persona and its plugins' text.
    system_prompt: tuple[str, ...] = ()

    def run(self, *msg, **kwargs) -> FakeRun:
        if "prompt" not in kwargs:
            kwargs["prompt"] = [
                *self.system_prompt,
                *(part for plugin in kwargs.get("plugins", ()) for part in plugin._system_prompt),
            ]
        return FakeRun(self, msg, kwargs)


class FakeActionsMixin:
    """Gives a fake agent the DI surface AG2 resolves an A2UI server action against
    — the dependencies, variables and provider ``build_server_action_context`` reads."""

    _agent_dependencies = types.MappingProxyType({})
    _agent_variables = types.MappingProxyType({})
    dependency_provider = Provider()


class FakeAgent(FakeRunMixin, FakeActionsMixin):
    """Deterministic fake agent: echo[N] proves per-chat continuity; empty tools."""

    def __init__(self):
        self._counts: dict = {}
        self.tools = []

    async def ask(self, *msg, stream=None, **kwargs) -> FakeReply:
        sid = getattr(stream, "id", "default")
        self._counts[sid] = self._counts.get(sid, 0) + 1
        return FakeReply(f"echo[{self._counts[sid]}]: {msg[0]}")


class ModelNamingAgent(FakeRunMixin):
    """A fake agent that answers with the model its config was built from, so a turn's
    reply names the model configuration the turn actually resolved to."""

    def __init__(self, config, unusable=()):
        self.config = config
        self.unusable = unusable
        self.tools = []

    async def ask(self, *msg, stream=None, config=None, **kwargs) -> FakeReply:
        name = config.model if config is not None else self.config.llm.model
        if name in self.unusable:
            raise RuntimeError(f"{name} cannot run")
        return FakeReply(name)


def model_naming_agent_factory(unusable=()):
    """A ``create_agent``-shaped factory handing out ``ModelNamingAgent``s. Any model
    named in ``unusable`` raises at build time — a configuration that exists but cannot
    run (no key, not signed in), which fails the turn rather than being rescued."""

    def factory(config, **kwargs):
        if config.llm.model in unusable:
            raise RuntimeError(f"{config.llm.model} cannot run")
        return ModelNamingAgent(config, unusable)

    return factory


def failing_agent_factory(fail_for):
    """Raise while building an Agent for any Profile named in ``fail_for``."""

    def factory(config, **kwargs):
        pid = config.data_dir.name
        if pid in fail_for:
            raise RuntimeError(f"agent build exploded for {pid}")
        return FakeAgent()

    return factory


class FakeChannel:
    """Stand-in Channel: records the Connection and token(s) it was built with, plus
    start/stop/notify, without touching a network."""

    def __init__(self, platform: str, connection: str = "", **tokens):
        self.platform = platform
        self.connection = connection
        self.tokens = tokens
        self.started = False
        self.stopped = False
        self.router = None
        self.sent: list[tuple[str, str]] = []

    async def start(self, router) -> None:
        self.started = True
        self.router = router

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

    def factory(platform, connection="", **tokens):
        channel = FakeChannel(platform, connection=connection, **tokens)
        if made is not None:
            made.append(channel)
        return channel

    return factory


def _canned(**fields):
    """A bare object carrying ``fields`` — what ``ask_structured`` hands back."""
    return type("Out", (), fields)()


def fake_title_factory(title="Fake Title", built=None):
    """A titler factory whose one-shot agent always answers ``title``. Pass ``built`` to
    collect the config each titler was built from — which model named the chat."""

    def factory(config):
        if built is not None:
            built.append(config)
        return FakeStructuredAgent(_canned(title=title))

    return factory


def fake_summary_factory(summary="Fake summary.", name="Fake Task", description="", built=None):
    """A distiller factory for run summaries AND task auto-naming (one fake answers
    both schemas — each reader picks the field it needs). Pass ``built`` to collect the
    config each distiller was built from — which model summarised the run."""

    def factory(config):
        if built is not None:
            built.append(config)
        return FakeStructuredAgent(_canned(summary=summary, name=name, description=description))

    return factory


class ScriptedModels:
    """Real TestModel clients recording requests and executing a supplied response script."""

    def __init__(self, script=None, before_call=None):
        self.script = script or (lambda config, model: ["done"])
        self.before_call = before_call
        self.requests = []

    def __call__(self, config, model=None):
        return _RecordingModel(self, config.model_copy(deep=True), model)


class _RecordingModel(TestConfig):
    def __init__(self, models, config, model):
        self.models, self.config, self.selected_model = models, config, model
        super().__init__()

    def create(self):
        return _RecordingClient(self.models, self.config, self.selected_model)


class _RecordingClient(TestClient):
    def __init__(self, models, config, model):
        self.models, self.config, self.selected_model = models, config, model
        super().__init__(*models.script(config, model), raise_tool_errors=False)

    async def __call__(self, messages, context, **kwargs):
        schemas = tuple(kwargs.get("tools", ()))
        kwargs["tools"] = schemas
        self.models.requests.append(
            (self.config, tuple(context.prompt), schemas, tuple(messages), self.selected_model)
        )
        if self.models.before_call is not None:
            await self.models.before_call(messages, context)
        return await super().__call__(messages, context, **kwargs)


class StatefulEnvironment:
    """An environment factory double whose executions expose retained sandbox state."""

    workdir = PurePosixPath("/workspace")
    host_workdir = None
    supported_languages = ("python",)

    def __init__(self, **kwargs):
        self.settings = kwargs
        self.value = 0
        self.closed = False

    @asynccontextmanager
    async def open(self, context=None):
        if self.closed:
            raise RuntimeError("Environment disposed")
        yield self

    async def exec(self, argv, **kwargs):
        if self.closed:
            raise RuntimeError("Environment disposed")
        self.value += 1
        return ExecResult(output=str(self.value), exit_code=0)

    async def aclose(self):
        if self.closed:
            raise RuntimeError("Environment disposed twice")
        self.closed = True

"""Shared test fixtures."""

import asyncio
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

import assistant.gateway.core as core_mod
from assistant.codex_auth import CodexAuthError
from assistant.config import Config, load_config
from assistant.gateway.app import create_app
from assistant.gateway.profile_manager import ProfileManager
from assistant.paths import Paths
from assistant.profiles import ProfileRegistry


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


def use_fake_agent(monkeypatch, agent_factory=None):
    """Patch the agent factory the gateway core looks up, so no runtime touches an LLM."""

    factory = agent_factory or (lambda *a, **k: FakeAgent())
    monkeypatch.setattr(core_mod, "create_agent", factory)


def make_profile_app(
    paths=None,
    *,
    name="Test",
    accent="#109e91",
    persist=False,
    memory=False,
    codex_client=None,
    google=None,
):
    """Build a create_app FastAPI app around a ProfileManager with ONE profile.

    Returns ``(app, pid)``. ``paths`` is the install layout to run on; omitted, it
    comes from the isolated environment the autouse HOME fixture sets up. The profile
    is created in the registry before start() so lifespan boots it; hit
    ``/api/p/{pid}/…``.
    """

    paths = paths if paths is not None else load_config().paths
    meta = ProfileRegistry(paths).create_profile(name, accent)
    paths.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    manager = ProfileManager(paths, memory=memory, persist=persist)
    app = create_app(
        manager,
        persist=persist,
        code_reader=no_loopback_code_reader,
        codex_client=codex_client,
        google=google,
    )
    return app, meta.id


def no_loopback_code_reader(state: str) -> str:
    """A ChatGPT sign-in code reader that gives up at once. The real one binds the
    loopback callback port and blocks for five minutes, which would outlive the test."""
    raise CodexAuthError("no loopback listener in tests")


def api(pid: str, path: str = "") -> str:
    """Profile-scoped route prefix helper for tests: api('work', '/chats')."""
    return f"/api/p/{pid}{path}"


def make_paths(root) -> Paths:
    """An isolated layout under ``root`` — the plain-function form of the ``paths``
    fixture, for helpers that already receive a tmp dir."""
    return Paths(
        root=root / "state",
        workspace=root / "workspace",
        codex_auth=root / "codex-cli-auth.json",
    )


@pytest.fixture
def paths(tmp_path) -> Paths:
    """An isolated on-disk layout. Neither the environment nor $HOME take part."""
    return make_paths(tmp_path)


@pytest.fixture
def config(paths) -> Config:
    """A Config over an isolated layout; every other field keeps its default."""
    return Config.for_paths(paths)


@pytest.fixture
def profile_app(monkeypatch):
    """A started single-profile app + its pid, agent faked. Yields ``(client, pid)``
    inside a TestClient context (lifespan boots the runtime)."""

    use_fake_agent(monkeypatch)
    app, pid = make_profile_app(persist=True)
    with TestClient(app) as client:
        yield client, pid


@pytest.fixture
def profile_app_factory(monkeypatch):
    """Like ``profile_app`` but callable, for tests that must configure the app
    (e.g. hand it an httpx client) before it starts. Yields a builder returning
    ``(client, pid)`` inside a managed TestClient context."""
    use_fake_agent(monkeypatch)
    stack = ExitStack()

    def build(**kwargs):
        app, pid = make_profile_app(persist=True, **kwargs)
        return stack.enter_context(TestClient(app)), pid

    with stack:
        yield build


@pytest.fixture(autouse=True)
def _isolate_ag2assistant_home(monkeypatch, tmp_path):
    """Isolate tests from the developer's real ~/.ag2assistant state.

    Redirecting HOME is enough: every on-disk location now derives from
    ``Paths.from_env(env, home)``, so the Google credentials/token, the Codex CLI
    login, the registry and the permission/task stores all land under the test's
    own tmp root instead of the developer's real state. A real Google token would
    otherwise make build_agent_tools append the 8 Google tools and break tool-set
    assertions.
    """
    monkeypatch.setenv("HOME", str(tmp_path))

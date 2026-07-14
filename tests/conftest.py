"""Shared test fixtures."""

import asyncio

import pytest


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
    """Deterministic fake agent: echo[N] proves per-session continuity; empty tools."""

    def __init__(self):
        self._counts: dict = {}
        self.tools = []

    async def ask(self, *msg, stream=None, **kwargs) -> FakeReply:
        sid = getattr(stream, "id", "default")
        self._counts[sid] = self._counts.get(sid, 0) + 1
        return FakeReply(f"echo[{self._counts[sid]}]: {msg[0]}")


def use_fake_agent(monkeypatch, agent_factory=None):
    """Patch the agent factory the gateway core looks up, so no runtime touches an LLM."""
    import assistant.gateway.core as core_mod

    factory = agent_factory or (lambda *a, **k: FakeAgent())
    monkeypatch.setattr(core_mod, "create_agent", factory)


def make_profile_app(*, name="Test", palette="teal", persist=False, memory=False):
    """Build a create_app FastAPI app around a ProfileManager with ONE profile.

    Returns ``(app, pid)``. Relies on the autouse HOME-isolation fixture so the
    registry + profile dir land under the test's tmp root. The profile is created
    in the registry before start() so lifespan boots it; hit ``/api/p/{pid}/…``.
    """
    from assistant import profiles
    from assistant.gateway.app import create_app
    from assistant.gateway.profile_manager import ProfileManager

    meta = profiles.create_profile(name, palette)
    profiles.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    manager = ProfileManager(memory=memory, persist=persist)
    app = create_app(manager, persist=persist)
    return app, meta.id


def api(pid: str, path: str = "") -> str:
    """Profile-scoped route prefix helper for tests: api('work', '/sessions')."""
    return f"/api/p/{pid}{path}"


@pytest.fixture
def profile_app(monkeypatch):
    """A started single-profile app + its pid, agent faked. Yields ``(client, pid)``
    inside a TestClient context (lifespan boots the runtime)."""
    from fastapi.testclient import TestClient

    use_fake_agent(monkeypatch)
    app, pid = make_profile_app(persist=True)
    with TestClient(app) as client:
        yield client, pid


@pytest.fixture(autouse=True)
def _isolate_ag2assistant_home(monkeypatch, tmp_path):
    """Isolate tests from the developer's real ~/.ag2assistant state.

    A real Google token/credentials would make build_agent_tools append the 8
    Google tools and break tool-set assertions. We point the Google paths at an
    empty tmp dir so `is_configured()`/`has_token()` are naturally False; tests
    that need a token write to their own (separately-monkeypatched) paths, which
    run after this fixture and therefore override it.

    We also redirect HOME to a tmp dir so anything resolving `~/.ag2assistant`
    (PermissionStore, the gateway's task/inquiry stores) writes to disposable
    space instead of the developer's real state.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    try:
        import assistant.integrations.google_auth as ga

        monkeypatch.setattr(ga, "token_path", lambda: tmp_path / "no_token.json")
        monkeypatch.setattr(ga, "credentials_path", lambda: tmp_path / "no_creds.json")
        monkeypatch.setattr(ga, "account_path", lambda: tmp_path / "no_account.txt")
    except Exception:
        pass

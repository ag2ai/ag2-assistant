"""Builders for the system under test: an install layout, a ProfileManager, an app.

Everything here is a plain function, so tests import it directly instead of relying
on fixtures — the fixtures in ``conftest.py`` are thin wrappers over these.
"""

import json
import time

from assistant.codex_auth import CodexAuthError
from assistant.config import load_config
from assistant.gateway.app import create_app
from assistant.gateway.profile_manager import ProfileManager
from assistant.paths import Paths
from assistant.profiles import ProfileRegistry
from tests.support.fakes import (
    fake_agent_factory,
    fake_channel_factory,
    fake_summary_factory,
    fake_title_factory,
)


def make_paths(root) -> Paths:
    """An isolated layout under ``root`` — the plain-function form of the ``paths``
    fixture, for helpers that already receive a tmp dir."""
    return Paths(
        root=root / "state",
        workspace=root / "workspace",
        codex_auth=root / "codex-cli-auth.json",
    )


def make_manager(
    paths=None,
    *,
    env=None,
    persist=False,
    memory=False,
    agent_factory=None,
    channel_factory=None,
    title_factory=None,
    summary_factory=None,
    made=None,
):
    """A ProfileManager over ``paths`` (or the HOME-isolated layout) whose every
    network-touching collaborator is a fake unless the caller injects its own.
    ``made`` collects the channels the default channel factory builds."""
    return ProfileManager(
        paths,
        env=env,
        memory=memory,
        persist=persist,
        agent_factory=agent_factory or fake_agent_factory(),
        channel_factory=channel_factory or fake_channel_factory(made),
        title_factory=title_factory or fake_title_factory(),
        summary_factory=summary_factory or fake_summary_factory(),
    )


def make_profile_app(
    paths=None,
    *,
    name="Test",
    accent="#109e91",
    env=None,
    persist=False,
    memory=False,
    agent_factory=None,
    channel_factory=None,
    title_factory=None,
    summary_factory=None,
    **app_kwargs,
):
    """Build a create_app FastAPI app around a ProfileManager with ONE profile.

    Returns ``(app, pid)``. ``paths`` is the install layout to run on; omitted, it
    comes from the isolated environment the autouse HOME fixture sets up. The profile
    is created in the registry before start() so lifespan boots it; hit
    ``/api/p/{pid}/…``. Every collaborator that would reach the network (agent,
    channels, the cheap-model helpers) defaults to a fake; ``app_kwargs`` go straight
    to ``create_app`` (codex_client, google, llm_probe, …). ``env`` is the ambient
    environment the install resolves from (PATH, AG2ASSISTANT_*), empty by default.
    """

    paths = paths if paths is not None else load_config().paths
    meta = ProfileRegistry(paths).create_profile(name, accent)
    paths.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    manager = make_manager(
        paths,
        env=env,
        memory=memory,
        persist=persist,
        agent_factory=agent_factory,
        channel_factory=channel_factory,
        title_factory=title_factory,
        summary_factory=summary_factory,
    )
    app = create_app(
        manager,
        persist=persist,
        code_reader=no_loopback_code_reader,
        **app_kwargs,
    )
    return app, meta.id


def no_loopback_code_reader(state: str) -> str:
    """A ChatGPT sign-in code reader that gives up at once. The real one binds the
    loopback callback port and blocks for five minutes, which would outlive the test."""
    raise CodexAuthError("no loopback listener in tests")


def api(pid: str, path: str = "") -> str:
    """Profile-scoped route prefix helper for tests: api('work', '/chats')."""
    return f"/api/p/{pid}{path}"


def write_codex_session(paths, *, access_token="TOK", refresh_token="RX", account_id="acc") -> None:
    """Put a real signed-in ChatGPT session on disk, so ``CodexAuth`` reports it
    without the network (a refresh_token that is still fresh is never exchanged)."""
    paths.codex_tokens.parent.mkdir(parents=True, exist_ok=True)
    paths.codex_tokens.write_text(
        json.dumps(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "account_id": account_id,
                "expires_at": time.time() + 3600,
            }
        )
    )

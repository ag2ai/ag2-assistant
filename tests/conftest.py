"""Shared test fixtures. Helpers live in ``tests/support`` — import them from there."""

from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from assistant.config import Config
from assistant.paths import Paths
from tests.support.apps import make_paths, make_profile_app


@pytest.fixture
def paths(tmp_path) -> Paths:
    """An isolated on-disk layout. Neither the environment nor $HOME take part."""
    return make_paths(tmp_path)


@pytest.fixture
def config(paths) -> Config:
    """A Config over an isolated layout; every other field keeps its default."""
    return Config.for_paths(paths)


@pytest.fixture
def profile_app(paths):
    """A started single-profile app + its pid on the isolated layout, agent faked.
    Yields ``(client, pid)`` inside a TestClient context (lifespan boots the runtime)."""

    app, pid = make_profile_app(paths, persist=True)
    with TestClient(app) as client:
        yield client, pid


@pytest.fixture
def profile_app_factory(paths):
    """Like ``profile_app`` but callable, for tests that must configure the app
    (e.g. hand it an httpx client) before it starts. Yields a builder returning
    ``(client, pid)`` inside a managed TestClient context."""
    stack = ExitStack()

    def build(**kwargs):
        app, pid = make_profile_app(paths, persist=True, **kwargs)
        return stack.enter_context(TestClient(app)), pid

    with stack:
        yield build

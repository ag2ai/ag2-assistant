"""Tests for the Docker sandbox backend and its wiring into the tool list.

The unit tests never touch Docker (construction is lazy). The integration test
runs a real container and is skipped when Docker isn't available.
"""

from pathlib import Path, PurePosixPath

import pytest

from agclaw.tools.docker_sandbox import (
    DockerEnvironment,
    DockerSandbox,
    docker_available,
)


def test_sandbox_paths_without_starting():
    sb = DockerSandbox(workdir="/work")
    assert sb.workdir == PurePosixPath("/work")
    assert sb.host_workdir is None  # container fs not on the host


def test_rejects_nonpositive_timeout():
    with pytest.raises(ValueError):
        DockerSandbox(timeout=0)


async def test_exec_empty_argv_short_circuits():
    sb = DockerSandbox()
    result = await sb.exec([])
    assert result.exit_code == 2  # never starts a container for empty argv


async def test_exec_after_close_raises():
    sb = DockerSandbox()
    await sb.aclose()  # closing an unstarted sandbox is a no-op
    with pytest.raises(RuntimeError):
        await sb.exec(["echo", "hi"])


# --- build_agent_tools wiring (no real Docker) ---


def test_build_tools_falls_back_when_docker_unavailable(monkeypatch):
    import agclaw.tools as tools_mod
    import agclaw.tools.docker_sandbox as ds

    monkeypatch.setattr(ds, "docker_available", lambda: False)
    calls = {"approval": 0}
    real_approval = tools_mod.require_command_approval

    def counting_approval(*a, **k):
        calls["approval"] += 1
        return real_approval(*a, **k)

    monkeypatch.setattr(tools_mod, "require_command_approval", counting_approval)

    with pytest.warns(UserWarning, match="Docker sandbox requested"):
        tools = tools_mod.build_agent_tools(provider="gemini", sandbox="docker")
    assert len(tools) == 5
    assert calls["approval"] == 1  # local fallback keeps the approval gate


def test_build_tools_uses_docker_and_drops_approval(monkeypatch):
    import agclaw.tools as tools_mod
    import agclaw.tools.docker_sandbox as ds

    monkeypatch.setattr(ds, "docker_available", lambda: True)
    calls = {"approval": 0}

    def counting_approval(*a, **k):
        calls["approval"] += 1
        return None  # not used on the docker path

    monkeypatch.setattr(tools_mod, "require_command_approval", counting_approval)

    tools = tools_mod.build_agent_tools(provider="gemini", sandbox="docker")
    assert len(tools) == 5
    assert calls["approval"] == 0  # container is the boundary → no approval gate


# --- real Docker (integration) ---


@pytest.mark.integration
@pytest.mark.skipif(not docker_available(), reason="Docker not available")
async def test_docker_exec_and_put_file_roundtrip():
    sb = DockerSandbox(network="none")
    try:
        echoed = await sb.exec(["echo", "hello-from-container"])
        assert echoed.exit_code == 0
        assert "hello-from-container" in echoed.output

        await sb.put_file(PurePosixPath("note.txt"), b"persisted")
        read = await sb.exec(["cat", "note.txt"])
        assert "persisted" in read.output

        # no host filesystem is mounted
        assert sb.host_workdir is None
    finally:
        await sb.aclose()


@pytest.mark.integration
@pytest.mark.skipif(not docker_available(), reason="Docker not available")
async def test_docker_environment_factory_opens_same_sandbox():
    env = DockerEnvironment(network="none")
    try:
        async with env.open() as a, env.open() as b:
            assert a is b  # singleton: shell + code share one container
    finally:
        await env.aclose()

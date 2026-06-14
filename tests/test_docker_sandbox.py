"""Tests for the Docker sandbox backend and its wiring into the tool list.

The unit tests never touch Docker (construction is lazy). The integration test
runs a real container and is skipped when Docker isn't available.
"""

from pathlib import Path, PurePosixPath

import pytest

from agclaw.tools.docker_sandbox import (
    DockerEnvironment,
    DockerMountSandbox,
    DockerSandbox,
    build_docker_skill_runtime,
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


# --- skill-script sandbox (mounted, one-shot) ---


def test_mount_sandbox_builds_bind_mount_argv(tmp_path):
    sb = DockerMountSandbox(host_dir=tmp_path, image="python:3.12-slim", network="none")
    argv = sb._build_argv(["python3", "./x.py"], {"K": "v"})
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "-v" in argv and f"{tmp_path.resolve()}:/skill" in argv
    assert "--network" in argv and "none" in argv
    assert argv[-3:] == ["python:3.12-slim", "python3", "./x.py"]
    assert "-e" in argv and "K=v" in argv
    assert sb.host_workdir == tmp_path.resolve()


def test_docker_skill_runtime_uses_mounted_sandbox(tmp_path):
    rt = build_docker_skill_runtime(install_dir=tmp_path, network="none")
    scripts = tmp_path / "myskill" / "scripts"
    scripts.mkdir(parents=True)
    adapter = rt.shell(scripts)
    # the adapter runs against a one-shot mounted Docker sandbox rooted at scripts/
    assert isinstance(adapter._factory.sandbox, DockerMountSandbox)
    assert adapter.workdir == scripts.resolve()  # host view = the mounted dir


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


@pytest.mark.integration
@pytest.mark.skipif(not docker_available(), reason="Docker not available")
async def test_mounted_sandbox_runs_script_in_isolation(tmp_path):
    (tmp_path / "hello.py").write_text("print('hi-from-skill')\n")
    sb = DockerMountSandbox(host_dir=tmp_path, network="none")
    result = await sb.exec(["python3", "./hello.py"])
    assert result.exit_code == 0
    assert "hi-from-skill" in result.output
    # the container can't see the host root — only the mounted dir
    listing = await sb.exec(["ls", "/skill"])
    assert "hello.py" in listing.output


@pytest.mark.integration
@pytest.mark.skipif(not docker_available(), reason="Docker not available")
async def test_docker_skill_runtime_runs_script(tmp_path):
    rt = build_docker_skill_runtime(install_dir=tmp_path, network="none")
    scripts = tmp_path / "myskill" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "go.py").write_text("print('skill-ran')\n")
    out = await rt.shell(scripts).run("python3 ./go.py")
    assert "skill-ran" in out

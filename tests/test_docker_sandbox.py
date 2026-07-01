"""Tests for the skill-script Docker sandbox and the tool-list wiring.

The shell/code tools use AG2's official `DockerEnvironment`; this module keeps the
one-shot mounted sandbox for skill scripts. Unit tests never touch Docker
(construction is lazy); the integration tests run a real container and are skipped
when Docker isn't available.
"""

import pytest

from assistant.tools.docker_sandbox import (
    DockerMountSandbox,
    build_docker_skill_runtime,
    docker_available,
)

# --- build_agent_tools wiring (no real Docker) ---


def test_build_tools_falls_back_when_docker_unavailable(monkeypatch):
    import assistant.tools as tools_mod
    import assistant.tools.docker_sandbox as ds

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


def test_build_tools_offers_sandboxed_and_local_when_docker(monkeypatch):
    """With Docker available the agent gets BOTH a sandboxed runner (no approval)
    and a host runner (approval-gated) for code AND shell, and chooses per call."""
    pytest.importorskip("docker")  # AG2's DockerEnvironment needs the docker lib
    import assistant.tools as tools_mod
    import assistant.tools.docker_sandbox as ds

    monkeypatch.setattr(ds, "docker_available", lambda: True)
    calls = {"approval": 0}
    real_approval = tools_mod.require_command_approval

    def counting_approval(*a, **k):
        calls["approval"] += 1
        return real_approval(*a, **k)

    monkeypatch.setattr(tools_mod, "require_command_approval", counting_approval)

    names = {
        t.name
        for t in tools_mod.build_agent_tools(
            provider="gemini", sandbox="docker", capabilities=["code"]
        )
    }
    # four distinct tools: isolated (silent) + host (approval-gated), code + shell
    assert names == {
        "run_code_sandboxed",
        "run_shell_sandboxed",
        "run_code_local",
        "run_shell_local",
    }
    # one approval middleware, shared by the two host tools; the sandboxed pair has none
    assert calls["approval"] == 1


def test_build_tools_wires_ag2_docker_environment(monkeypatch):
    """The docker path uses AG2's official DockerEnvironment, passing our image and
    network through (network_mode), rather than a custom sandbox."""
    pytest.importorskip("docker")  # AG2's DockerEnvironment needs the docker lib
    import ag2.extensions.docker as agdoc

    import assistant.tools as tools_mod
    import assistant.tools.docker_sandbox as ds

    monkeypatch.setattr(ds, "docker_available", lambda: True)
    captured = {}
    real_env = agdoc.DockerEnvironment

    def recorder(**kwargs):
        captured.update(kwargs)
        return real_env(**kwargs)  # a real factory (no container until .open())

    monkeypatch.setattr(agdoc, "DockerEnvironment", recorder)
    tools_mod.build_agent_tools(provider="gemini", sandbox="docker", docker_network="bridge")
    assert captured.get("network_mode") == "bridge"
    assert captured.get("image")  # the configured image is forwarded


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

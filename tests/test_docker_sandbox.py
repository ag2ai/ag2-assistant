"""Tests for the skill-script Docker sandbox and the tool-list wiring.

The shell/code tools use AG2's official `DockerEnvironment`; this module keeps the
one-shot mounted sandbox for skill scripts. Unit tests never touch Docker: whether
Docker exists is decided by a real `docker` stub on an explicit search path, and
the container backend arrives through `environment_factory`. The integration tests
run a real container and are skipped when Docker isn't available.
"""

import os
from pathlib import PurePosixPath

import pytest

import assistant.tools as tools_mod
from assistant.coding.detect import default_search_path
from assistant.config import Config
from assistant.tools.docker_sandbox import (
    DockerMountSandbox,
    build_docker_skill_runtime,
    docker_available,
)
from tests.support.stubs import write_stub

_HOST_SEARCH_PATH = default_search_path(os.environ)


class RecordingEnvironment:
    """The slice of AG2's environment contract the sandbox tools construct against."""

    supported_languages = ("python",)
    workdir = PurePosixPath("/workspace")
    host_workdir = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _middleware(tool):
    """The middleware chain AG2 attached to a tool — the approval gate, if any."""
    return tuple(tool._tool._middleware)


def _docker_config(paths, tmp_path) -> Config:
    """A config whose host facts point at a real, answering `docker` stub."""
    bin_dir = tmp_path / "bin"
    write_stub(bin_dir / "docker", stdout="Docker version 27.0.0")
    return Config.for_paths(paths, search_path=[bin_dir])


# --- docker discovery ---


def test_docker_availability_follows_a_real_docker_on_the_search_path(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    assert docker_available([bin_dir]) is False
    write_stub(bin_dir / "docker", stdout="Docker version 27.0.0")
    assert docker_available([bin_dir]) is True


def test_docker_is_unavailable_when_the_daemon_does_not_answer(tmp_path):
    bin_dir = tmp_path / "bin"
    write_stub(bin_dir / "docker", stderr="Cannot connect to the Docker daemon", exit_code=1)
    assert docker_available([bin_dir]) is False


def test_an_empty_search_path_means_no_docker():
    """No host facts must never fall back to the process PATH."""
    assert docker_available([]) is False


# --- build_agent_tools wiring (no real Docker) ---


def test_build_tools_falls_back_when_docker_is_not_on_the_search_path():
    with pytest.warns(UserWarning, match="Docker sandbox requested"):
        tools = tools_mod.build_agent_tools("gemini", sandbox="docker")
    assert len(tools) == 12  # incl. chat-only ask_user + the two coding-agent tools
    local = [t for t in tools if t.name in ("run_shell_command", "run_code")]
    assert len(local) == 2
    # the local fallback keeps the approval gate, one middleware shared by both tools
    assert {_middleware(t) for t in local} == {_middleware(local[0])}
    assert len(_middleware(local[0])) == 1


def test_build_tools_offers_sandboxed_and_local_when_docker(paths, tmp_path):
    """With Docker available the agent gets BOTH a sandboxed runner (no approval)
    and a host runner (approval-gated) for code AND shell, and chooses per call."""
    tools = tools_mod.build_agent_tools(
        "gemini",
        sandbox="docker",
        capabilities=["code"],
        config=_docker_config(paths, tmp_path),
        environment_factory=RecordingEnvironment,
    )
    by_name = {t.name: t for t in tools}
    # four distinct tools: isolated (silent) + host (approval-gated), code + shell
    assert set(by_name) == {
        "run_code_sandboxed",
        "run_shell_sandboxed",
        "run_code_local",
        "run_shell_local",
    }
    assert _middleware(by_name["run_code_sandboxed"]) == ()
    assert _middleware(by_name["run_shell_sandboxed"]) == ()
    # one approval middleware, shared by the two host tools
    host = [by_name["run_code_local"], by_name["run_shell_local"]]
    assert len(_middleware(host[0])) == 1
    assert _middleware(host[0]) == _middleware(host[1])


def test_build_tools_wires_the_container_backend_with_image_and_network(paths, tmp_path):
    """The docker path builds the container backend through the injected factory,
    passing our image and network through (network_mode)."""
    made = []

    def recorder(**kwargs):
        made.append(RecordingEnvironment(**kwargs))
        return made[-1]

    tools_mod.build_agent_tools(
        "gemini",
        sandbox="docker",
        docker_network="bridge",
        capabilities=["code"],
        config=_docker_config(paths, tmp_path),
        environment_factory=recorder,
    )
    assert len(made) == 1
    assert made[0].kwargs["network_mode"] == "bridge"
    assert made[0].kwargs["image"]  # the configured image is forwarded


def test_the_default_environment_factory_builds_ag2s_docker_environment():
    """The production default is AG2's official DockerEnvironment, not a custom sandbox."""
    pytest.importorskip("docker")  # AG2's DockerEnvironment needs the docker lib
    from ag2.extensions.docker import DockerEnvironment

    env = tools_mod.docker_environment(image="python:3.12-slim", network_mode="none")
    assert isinstance(env, DockerEnvironment)  # no container until .open()


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
@pytest.mark.skipif(not docker_available(_HOST_SEARCH_PATH), reason="Docker not available")
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
@pytest.mark.skipif(not docker_available(_HOST_SEARCH_PATH), reason="Docker not available")
async def test_docker_skill_runtime_runs_script(tmp_path):
    rt = build_docker_skill_runtime(install_dir=tmp_path, network="none")
    scripts = tmp_path / "myskill" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "go.py").write_text("print('skill-ran')\n")
    out = await rt.shell(scripts).run("python3 ./go.py")
    assert "skill-ran" in out

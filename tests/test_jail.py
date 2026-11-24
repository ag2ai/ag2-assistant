"""The Landlock jail launcher and the `jailed` code/shell backend.

Enforcement tests need Linux with Landlock ABI >= 4 and skip elsewhere.
"""

import contextlib
import json
import socket
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest
from ag2.events import ToolCallEvent, ToolResultEvent

import assistant.tools as tools_mod
from assistant.tools import jail
from assistant.tools.jail import JailLimits
from assistant.tools.jail_sandbox import JailedEnvironment, JailedSandbox
from tests.support.apps import real_gateway
from tests.support.fakes import ScriptedModels

ABI = jail.landlock_abi()
ENFORCED = ABI >= jail.MIN_ABI
requires_jail = pytest.mark.skipif(not ENFORCED, reason="needs Linux with Landlock ABI >= 4")
LAUNCHER = Path(jail.__file__).resolve()


def _jailed(workdir: Path, *cmd: str) -> subprocess.CompletedProcess[str]:
    """Run ``cmd`` through the launcher CLI with ``workdir`` as its scratch directory."""
    argv = jail.build_argv(LAUNCHER, sys.executable, workdir=workdir, cmd=cmd, limits=JailLimits())
    return subprocess.run(argv, capture_output=True, text=True, timeout=60)


def _python(workdir: Path, snippet: str) -> subprocess.CompletedProcess[str]:
    return _jailed(workdir, sys.executable, "-I", "-c", snippet)


def _warns_without_jail():
    """Expects the missing-Landlock warning on hosts that cannot enforce the jail."""
    if ENFORCED:
        return contextlib.nullcontext()
    return pytest.warns(UserWarning, match="Landlock ABI")


class RecordingEnvironment:
    """The slice of AG2's environment contract the sandbox tools construct against."""

    supported_languages = ("python",)
    workdir = PurePosixPath("/workspace")
    host_workdir = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs


# --- launcher logic (every platform) ---------------------------------------


def test_build_argv_passes_the_command_verbatim_after_the_separator(tmp_path):
    argv = jail.build_argv(
        LAUNCHER, "/usr/bin/python3", workdir=tmp_path, cmd=["sh", "-c", "--"], limits=JailLimits()
    )
    assert argv[:3] == ["/usr/bin/python3", "-I", str(LAUNCHER)]
    assert argv[argv.index("--workdir") + 1] == str(tmp_path)
    assert argv[argv.index("--") + 1 :] == ["sh", "-c", "--"]


def test_clean_env_keeps_only_the_allow_list(tmp_path):
    env = jail.clean_env(
        {
            "PATH": "/usr/bin",
            "LANG": "C.UTF-8",
            "ANTHROPIC_API_KEY": "sk-secret",
            "DATABASE_URL": "postgres://u:p@db",
            "HOME": "/home/agent",
        },
        tmp_path,
    )
    assert env == {
        "PATH": "/usr/bin",
        "LANG": "C.UTF-8",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "PWD": str(tmp_path),
    }


def test_handled_fs_mask_grows_with_abi():
    assert jail._handled_fs(1) == 0x1FFF
    assert jail._handled_fs(2) == 0x1FFF | (1 << 13)
    assert jail._handled_fs(3) == 0x1FFF | (1 << 13) | (1 << 14)
    assert jail._handled_fs(5) & (1 << 15)
    assert not jail._workdir_access(5) & (1 << 15)


def test_ruleset_attr_carries_only_the_fields_the_abi_knows():
    assert len(jail._ruleset_attr(3, allow_net=True)) == 8
    assert len(jail._ruleset_attr(4, allow_net=False)) == 16
    assert len(jail._ruleset_attr(6, allow_net=False)) == 24
    assert jail._ruleset_attr(4, allow_net=True)[8:16] == bytes(8)


@pytest.mark.skipif(sys.platform == "linux", reason="checks the non-Linux answer")
def test_landlock_abi_is_zero_off_linux():
    assert jail.landlock_abi() == 0


@pytest.mark.skipif(ENFORCED, reason="checks the refusal on hosts without the jail")
def test_the_launcher_refuses_to_run_without_an_enforceable_jail(tmp_path):
    out = _jailed(tmp_path, "/bin/echo", "ran")
    assert out.returncode == 70
    assert "ran" not in out.stdout
    assert "[jail] refusing to run unconfined" in out.stderr


async def test_a_jailed_sandbox_runs_every_command_through_the_launcher(tmp_path):
    sandbox = JailedSandbox(tmp_path)
    try:
        result = await sandbox.exec(["/bin/echo", "ran"])
    finally:
        await sandbox.aclose()
    if ENFORCED:
        assert (result.exit_code, result.output) == (0, "ran")
    else:
        assert result.exit_code == 70 and "refusing" in result.output


# --- wiring (every platform) -----------------------------------------------


def test_isolated_environment_follows_the_sandbox_mode():
    assert tools_mod.isolated_environment("jailed") is JailedEnvironment
    assert tools_mod.isolated_environment("docker") is tools_mod.docker_environment


def test_jailed_offers_one_silent_runner_pair_and_no_host_runner():
    with _warns_without_jail():
        tools = tools_mod.build_agent_tools(
            "gemini",
            sandbox="jailed",
            capabilities=["code"],
            environment_factory=RecordingEnvironment,
        )
    by_name = {t.name: t for t in tools}
    assert set(by_name) == {"run_code", "run_shell"}
    assert tuple(by_name["run_code"]._tool._middleware) == ()
    assert tuple(by_name["run_shell"]._tool._middleware) == ()


async def test_the_gateway_runs_jailed_shell_commands_under_the_launcher(config):
    config.tools.sandbox = "jailed"
    models = ScriptedModels(
        script=lambda cfg, model: [
            ToolCallEvent(name="run_shell", arguments=json.dumps({"command": "echo jailed-ran"})),
            "done",
        ]
    )
    gateway = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await gateway.start()
    results = []

    async def collect(event):
        if isinstance(event, ToolResultEvent):
            results.append(event.result.parts[0].content)

    (await gateway.stream_for("jail")).subscribe(collect)
    try:
        with _warns_without_jail():
            await gateway.send_message("run it", chat_id="jail")
    finally:
        await gateway.close()
    assert len(results) == 1
    expected = "jailed-ran" if ENFORCED else "[jail] refusing to run unconfined"
    assert expected in results[0]


# --- enforcement (Linux, Landlock ABI >= 4) ---------------------------------


@requires_jail
def test_a_file_outside_the_workdir_is_denied(tmp_path_factory):
    secret = tmp_path_factory.mktemp("secrets") / "llm-key"
    secret.write_text("sk-secret-value")
    out = _python(tmp_path_factory.mktemp("work"), f"print(open({str(secret)!r}).read())")
    assert "sk-secret-value" not in out.stdout
    assert "PermissionError" in out.stderr


@requires_jail
def test_the_parent_process_environment_is_unreadable(tmp_path):
    out = _python(tmp_path, "import os; open(f'/proc/{os.getppid()}/environ').read()")
    assert "PermissionError" in out.stderr


@requires_jail
def test_the_workdir_is_writable(tmp_path):
    out = _python(tmp_path, "open('ok.txt', 'w').write('hi'); print(open('ok.txt').read())")
    assert (out.returncode, out.stdout.strip()) == (0, "hi")
    assert (tmp_path / "ok.txt").read_text() == "hi"


@requires_jail
def test_tcp_to_a_listening_port_is_denied(tmp_path):
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        out = _python(
            tmp_path,
            "import socket\n"
            "try:\n"
            f"    socket.create_connection(('127.0.0.1', {port}), timeout=2); print('CONNECTED')\n"
            "except OSError as e:\n"
            "    print(type(e).__name__)",
        )
    assert out.stdout.strip() == "PermissionError"


@requires_jail
@pytest.mark.skipif(ABI < 6, reason="signal scoping needs Landlock ABI 6")
def test_the_parent_process_cannot_be_signalled(tmp_path):
    out = _python(tmp_path, "import os; os.kill(os.getppid(), 0)")
    assert "PermissionError" in out.stderr


@requires_jail
def test_ordinary_shell_work_runs_inside_the_jail(tmp_path):
    out = _jailed(
        tmp_path,
        "/bin/sh",
        "-c",
        "echo a > f.txt && ls | wc -l && cat f.txt > /dev/null && date +%Y >/dev/null && echo ok",
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["1", "ok"]

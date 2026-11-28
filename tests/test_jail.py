"""The Landlock jail launcher and the `jailed` code/shell backend.

Enforcement tests need Linux with Landlock ABI >= 4 and skip elsewhere.
"""

import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import warnings
from pathlib import Path, PurePosixPath

import pytest
from ag2.events import ToolCallEvent, ToolResultEvent

import assistant.tools as tools_mod
from assistant.config import Config, apply_env_overrides
from assistant.tools import jail
from assistant.tools.jail import JailLimits
from assistant.tools.jail_sandbox import JailedEnvironment, JailedSandbox
from tests.support.apps import real_gateway
from tests.support.fakes import ScriptedModels
from tests.support.stubs import write_stub

ABI = jail.landlock_abi()
ENFORCED = ABI >= jail.MIN_ABI
requires_jail = pytest.mark.skipif(not ENFORCED, reason="needs Linux with Landlock ABI >= 6")
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
    assert len(jail._ruleset_attr(3)) == 8
    assert len(jail._ruleset_attr(4)) == 16
    assert len(jail._ruleset_attr(6)) == 24
    assert jail._ruleset_attr(6)[16:24] == (3).to_bytes(8, "little")


def _bpf(prog, *, arch: int, nr: int, arg0: int = 0) -> int:
    """Run a classic-BPF seccomp program over one syscall and return its action."""
    data = {jail._DATA_ARCH: arch, jail._DATA_NR: nr, jail._DATA_ARG0: arg0}
    acc, pc = 0, 0
    while True:
        code, jt, jf, k = prog[pc]
        if code == jail._BPF_RET_K:
            return k
        if code == jail._BPF_LD_W_ABS:
            acc, pc = data[k], pc + 1
        elif code == jail._BPF_JEQ_K:
            pc += 1 + (jt if acc == k else jf)
        elif code == jail._BPF_JGE_K:
            pc += 1 + (jt if acc >= k else jf)
        else:
            raise AssertionError(f"unexpected opcode {code:#x}")


REFUSE = jail._RET_ERRNO | 13


@pytest.mark.parametrize("machine", sorted(jail._ARCHES))
def test_the_socket_filter_refuses_every_socket_but_a_unix_pair(machine):
    prog = jail.socket_filter(machine)
    arch, nr_socket, nr_socketpair = jail._ARCHES[machine]
    assert _bpf(prog, arch=arch, nr=nr_socket, arg0=socket.AF_INET) == REFUSE
    assert _bpf(prog, arch=arch, nr=nr_socket, arg0=socket.AF_UNIX) == REFUSE
    assert _bpf(prog, arch=arch, nr=nr_socketpair, arg0=socket.AF_INET) == REFUSE
    assert _bpf(prog, arch=arch, nr=nr_socketpair, arg0=socket.AF_UNIX) == jail._RET_ALLOW
    for nr in jail._IO_URING:
        assert _bpf(prog, arch=arch, nr=nr) == REFUSE
    assert _bpf(prog, arch=arch, nr=0) == jail._RET_ALLOW
    assert _bpf(prog, arch=0x40000003, nr=nr_socket) == jail._RET_KILL_PROCESS


def test_the_socket_filter_kills_x32_syscalls():
    prog = jail.socket_filter("x86_64")
    arch = jail._ARCHES["x86_64"][0]
    assert _bpf(prog, arch=arch, nr=jail._X32_BIT | 41) == jail._RET_KILL_PROCESS


def test_an_unknown_machine_has_no_socket_filter():
    with pytest.raises(jail.JailError):
        jail.socket_filter("riscv64")


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


@pytest.mark.parametrize("value", ["Jailed", "jailed ", "bogus", ""])
def test_an_unknown_sandbox_offers_no_runner(value):
    with pytest.warns(UserWarning, match="Unknown sandbox"):
        tools = tools_mod.build_agent_tools(
            "gemini", sandbox=value, capabilities=["code"], environment_factory=RecordingEnvironment
        )
    assert tools == []


def test_the_sandbox_env_value_is_normalised(paths):
    config = Config.for_paths(paths)
    apply_env_overrides(config, {"AG2ASSISTANT_SANDBOX": " Jailed "})
    assert config.tools.sandbox == "jailed"


def test_runner_tools_names_every_runner_in_every_mode(paths, tmp_path):
    config = Config.for_paths(paths)
    config.search_path = [write_stub(tmp_path / "bin" / "docker").parent]
    offered = set()
    for sandbox in sorted(tools_mod.SANDBOXES):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tools = tools_mod.build_agent_tools(
                "gemini",
                sandbox=sandbox,
                capabilities=["code"],
                config=config,
                environment_factory=RecordingEnvironment,
                local_environment_factory=RecordingEnvironment,
            )
        offered |= {t.name for t in tools}
    assert offered == tools_mod.RUNNER_TOOLS


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


_SOCKETS = {
    "udp": "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.sendto(b'x', ('1.1.1.1', 53))",
    "mptcp": "socket.socket(socket.AF_INET, socket.SOCK_STREAM, 262)",
    "tcp_fastopen": "socket.socket().sendto(b'x', 0x20000000, ('127.0.0.1', 9))",
    "listen": "socket.socket().listen()",
    "unix": "socket.socket(socket.AF_UNIX).connect('/run/any.sock')",
    "icmp": "socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)",
    "ipv6": "socket.socket(socket.AF_INET6)",
}


@requires_jail
@pytest.mark.parametrize("kind", sorted(_SOCKETS))
def test_no_socket_can_be_opened(tmp_path, kind):
    out = _python(tmp_path, f"import socket\n{_SOCKETS[kind]}\nprint('OPENED')")
    assert "OPENED" not in out.stdout
    assert "PermissionError" in out.stderr


@requires_jail
def test_a_unix_socketpair_and_asyncio_still_work(tmp_path):
    out = _python(
        tmp_path,
        "import asyncio, socket\n"
        "a, b = socket.socketpair(); a.send(b'x'); assert b.recv(1) == b'x'\n"
        "asyncio.run(asyncio.sleep(0)); print('ok')",
    )
    assert out.stdout.strip() == "ok", out.stderr


def _alive(marker: str) -> list[int]:
    """Pids whose command line carries ``marker``."""
    found = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmdline = Path("/proc", pid, "cmdline").read_bytes()
        except OSError:
            continue
        if marker.encode() in cmdline:
            found.append(int(pid))
    return found


@requires_jail
def test_nothing_a_command_starts_outlives_it(tmp_path):
    quiet = ">/dev/null 2>&1"
    script = f"(setsid sleep 41.25 {quiet} &); sleep 41.5 {quiet} & echo started"
    out = _jailed(tmp_path, "/bin/sh", "-c", script)
    assert (out.returncode, out.stdout.strip()) == (0, "started")
    time.sleep(0.2)
    assert _alive("41.25") == [] and _alive("41.5") == []


@requires_jail
async def test_nothing_outlives_a_timed_out_command(tmp_path):
    sandbox = JailedSandbox(tmp_path, timeout=1)
    try:
        result = await sandbox.exec(["/bin/sh", "-c", "(setsid sleep 42.25 &); sleep 42.5"])
    finally:
        await sandbox.aclose()
    assert result.exit_code == 124
    for _ in range(50):
        if not _alive("42.25") and not _alive("42.5"):
            break
        await asyncio.sleep(0.05)
    assert _alive("42.25") == [] and _alive("42.5") == []


@requires_jail
def test_the_exit_code_is_passed_through(tmp_path):
    assert _jailed(tmp_path, "/bin/sh", "-c", "exit 7").returncode == 7
    assert _jailed(tmp_path, "/bin/sh", "-c", "kill -9 $$").returncode == 137


@requires_jail
def test_a_fork_bomb_stops_at_the_process_limit(tmp_path):
    out = _python(
        tmp_path,
        "import os, time\n"
        "n = 0\n"
        "try:\n"
        "    while n < 10000:\n"
        "        if os.fork() == 0:\n"
        "            time.sleep(5); os._exit(0)\n"
        "        n += 1\n"
        "except BlockingIOError:\n"
        "    print('stopped', n)",
    )
    assert out.stdout.startswith("stopped"), out.stderr
    assert int(out.stdout.split()[1]) <= JailLimits().procs


@requires_jail
def test_a_huge_allocation_fails_inside_the_jail(tmp_path):
    out = _python(tmp_path, "bytearray(8 * 1024**3)")
    assert "MemoryError" in out.stderr

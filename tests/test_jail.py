# Copyright (c) 2026, AG2ai, Inc., AG2ai open-source projects maintainers and core contributors
#
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Landlock code jail (assistant.tools.jail / jail_sandbox).

Two layers:
  * Logic tests — run everywhere (incl. macOS CI): argv construction, env
    scrubbing, ABI gating, and the fail-closed exit path.
  * Enforcement tests — Linux + Landlock only (skipped elsewhere): actually
    confine a child and prove it cannot read a secret outside the workdir but
    can use the workdir. These are the real proof and run in the pod image's CI.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from assistant.tools import jail

LINUX_LANDLOCK = sys.platform == "linux" and jail.landlock_abi() >= 1


# --- logic (all platforms) --------------------------------------------------

def test_build_argv_shape():
    argv = jail.build_argv("/x/jail.py", "/usr/bin/python3", workdir="/w",
                           cmd=["python3", "s.py"], allow_net=False, trust_host=False,
                           limits=jail.JAIL_DEFAULTS)
    assert argv[:3] == ["/usr/bin/python3", "-I", "/x/jail.py"]
    assert "--workdir" in argv and argv[argv.index("--workdir") + 1] == "/w"
    assert "--allow-net" not in argv and "--trust-host" not in argv
    # everything after `--` is the verbatim command
    assert argv[argv.index("--") + 1:] == ["python3", "s.py"]


def test_build_argv_flags_opt_in():
    argv = jail.build_argv("/x/jail.py", "py", workdir="/w", cmd=["echo", "hi"],
                           allow_net=True, trust_host=True, limits=jail.JAIL_DEFAULTS)
    assert "--allow-net" in argv and "--trust-host" in argv


def test_clean_env_strips_secrets(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("AG2SPACE_PERMISSIONS", "deny")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "tok")
    monkeypatch.setenv("MY_API_TOKEN", "t")
    monkeypatch.setenv("RANDOM_UNLISTED", "x")
    env = jail.clean_env("/work")
    assert env["PATH"] == "/usr/bin"
    assert env["LANG"] == "C.UTF-8"
    assert env["HOME"] == "/work" and env["TMPDIR"] == "/work"
    for leaked in ("ANTHROPIC_API_KEY", "AG2SPACE_PERMISSIONS", "AWS_SESSION_TOKEN",
                   "MY_API_TOKEN", "RANDOM_UNLISTED"):
        assert leaked not in env


def test_handled_fs_mask_grows_with_abi():
    # v1 has bits 0..12; refer(v2), truncate(v3), ioctl_dev(v5) add in later ABIs.
    assert jail._handled_fs(1) == 0x1FFF
    assert jail._handled_fs(2) == 0x1FFF | (1 << 13)
    assert jail._handled_fs(3) == 0x1FFF | (1 << 13) | (1 << 14)
    assert jail._handled_fs(5) & (1 << 15)
    # workdir access never includes device ioctl
    assert not (jail._workdir_access(5) & (1 << 15))


def test_landlock_abi_zero_off_linux():
    if sys.platform != "linux":
        assert jail.landlock_abi() == 0


def test_confine_fails_closed_without_landlock():
    # Where Landlock is absent, confine_and_exec must NOT exec; it returns 70.
    if LINUX_LANDLOCK:
        pytest.skip("Landlock present; fail-closed path exercised only without it")
    rc = jail.confine_and_exec(["/bin/echo", "should-not-run"], workdir=tempfile.gettempdir(),
                               ro_paths=[], allow_net=False, cpu_sec=5, mem_mb=256,
                               fsize_mb=8, nofile=64, nproc=16, trust_host=False)
    assert rc == 70


def test_main_fail_closed_via_subprocess(tmp_path):
    # End-to-end through the launcher CLI. On Linux+Landlock the echo runs
    # confined (rc 0); elsewhere it must fail closed (rc 70, nothing printed).
    out = subprocess.run(
        [sys.executable, "-I", os.path.abspath(jail.__file__), "--workdir", str(tmp_path),
         "--", "/bin/echo", "ran"],
        capture_output=True, text=True,
    )
    if LINUX_LANDLOCK:
        assert out.returncode == 0 and "ran" in out.stdout
    else:
        assert out.returncode == 70 and "ran" not in out.stdout


# --- enforcement (Linux + Landlock only) ------------------------------------

requires_landlock = pytest.mark.skipif(not LINUX_LANDLOCK, reason="needs Linux Landlock")


def _run_confined(tmp_path: Path, snippet: str, secret: Path):
    """Run `python -c snippet` under the launcher, workdir=tmp_path."""
    return subprocess.run(
        [sys.executable, "-I", os.path.abspath(jail.__file__), "--workdir", str(tmp_path),
         "--", sys.executable, "-I", "-c", snippet],
        capture_output=True, text=True,
    )


@requires_landlock
def test_secret_outside_workdir_is_denied(tmp_path):
    secret = Path(tempfile.mkdtemp()) / "llm-key"
    secret.write_text("sk-secret-value")
    snip = (f"open({str(secret)!r}).read(); print('READ_SECRET')")
    out = _run_confined(tmp_path, snip, secret)
    assert "READ_SECRET" not in out.stdout
    assert out.returncode != 0  # PermissionError inside the jail


@requires_landlock
def test_workdir_is_writable(tmp_path):
    snip = ("open('ok.txt','w').write('hi'); print('WROTE', open('ok.txt').read())")
    out = _run_confined(tmp_path, snip, tmp_path / "ok.txt")
    assert "WROTE hi" in out.stdout and out.returncode == 0


@requires_landlock
def test_network_is_denied(tmp_path):
    snip = ("import socket\n"
            "try:\n"
            "    socket.create_connection(('127.0.0.1', 4000), timeout=2); print('CONNECTED')\n"
            "except OSError as e:\n"
            "    print('BLOCKED', type(e).__name__)")
    out = _run_confined(tmp_path, snip, tmp_path)
    assert "CONNECTED" not in out.stdout
    assert "BLOCKED" in out.stdout


# --- build_agent_tools wiring (no Landlock needed) --------------------------

class _RecordingEnvironment:
    """The slice of AG2's environment contract the sandbox tools construct against."""

    supported_languages = ("python",)
    from pathlib import PurePosixPath as _P
    workdir = _P("/workspace")
    host_workdir = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _middleware(tool):
    return tuple(tool._tool._middleware)


def test_jailed_offers_one_runner_pair_without_approval():
    """`jailed` gives exactly run_code + run_shell, both silent (the jail — not a
    human — is the guard), and NO unconfined host runner."""
    import assistant.tools as tools_mod

    tools = tools_mod.build_agent_tools(
        "gemini",
        sandbox="jailed",
        capabilities=["code"],
        environment_factory=_RecordingEnvironment,
    )
    by_name = {t.name: t for t in tools}
    assert set(by_name) == {"run_code", "run_shell"}
    assert _middleware(by_name["run_code"]) == ()
    assert _middleware(by_name["run_shell"]) == ()
    # no host runners are offered in jailed mode
    assert not ({"run_code_local", "run_shell_local", "run_code_sandboxed"} & set(by_name))

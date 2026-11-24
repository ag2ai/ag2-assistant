# Copyright (c) 2026, AG2ai, Inc., AG2ai open-source projects maintainers and core contributors
#
# SPDX-License-Identifier: Apache-2.0
"""A Landlock + rlimit jail for model-run code and shell commands.

Why this exists
---------------
The `local` sandbox runs model-written code as a plain subprocess with the
*same authority as the agent process*: it can read every file the process can
(including mounted secrets) and open any network connection. In a cloud pod
that means a prompt-injected or owner-driven `run_code` can read the instance's
own credentials off disk and reach internal endpoints. `docker` fixes this but
needs a Docker daemon, which a Kubernetes pod does not have.

This module is the **launcher**: it confines the current process and then
``execve``s the real command, using Linux Landlock (an unprivileged LSM,
kernel >= 5.13) plus rlimits and an environment allow-list. It is
**stdlib-only** so it can be run as a standalone script
(``python -I jail.py --workdir ... -- cmd``) without importing the agent or
its heavy dependencies. The AG2 sandbox/environment classes that invoke it
live in :mod:`assistant.tools.jail_sandbox`.

What it confines
----------------
  * filesystem: read+write ONLY inside the per-call work directory; read+exec
    on a small set of runtime paths (interpreter, shared libs, /etc); every
    other path — notably any secrets mount — is denied, because no rule grants
    it under Landlock's deny-by-default model.
  * network: on Landlock ABI >= 4 (kernel >= 6.7) all TCP bind/connect is
    denied by handling the net rights and adding no port rule, so jailed code
    cannot reach the LLM proxy or other in-cluster services. (UDP is not
    governed by Landlock; pair with a NetworkPolicy to also cut UDP egress.)
  * environment: the child gets a minimal allow-listed env; secret-bearing
    names (``*_KEY``, ``*TOKEN*``, ``AG2SPACE_*``, ``AWS_*`` …) are dropped.
  * resources: CPU time, address space, file size, open files and process
    count are capped via rlimits.

**Fail closed.** If Landlock is unavailable or any confinement step fails, the
launcher refuses to run the command (exit 70, no execution) unless explicitly
told the host is trusted (``--trust-host``). A bug here must never downgrade to
running code unconfined. Landlock rules and ``NO_NEW_PRIVS`` survive ``execve``,
so the command and all its children stay confined.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import resource
import struct
import sys

# --- Landlock ABI constants (see linux/landlock.h) --------------------------

_NR_landlock_create_ruleset = 444
_NR_landlock_add_rule = 445
_NR_landlock_restrict_self = 446

_LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
_LANDLOCK_RULE_PATH_BENEATH = 1

# Filesystem access rights, with the first ABI version that defines each. A
# ruleset's handled mask must contain ONLY rights the running kernel knows, or
# the create call fails with EINVAL — so we build the mask from the detected ABI.
_FS_RIGHTS = {
    "execute": (1 << 0, 1),
    "write_file": (1 << 1, 1),
    "read_file": (1 << 2, 1),
    "read_dir": (1 << 3, 1),
    "remove_dir": (1 << 4, 1),
    "remove_file": (1 << 5, 1),
    "make_char": (1 << 6, 1),
    "make_dir": (1 << 7, 1),
    "make_reg": (1 << 8, 1),
    "make_sock": (1 << 9, 1),
    "make_fifo": (1 << 10, 1),
    "make_block": (1 << 11, 1),
    "make_sym": (1 << 12, 1),
    "refer": (1 << 13, 2),
    "truncate": (1 << 14, 3),
    "ioctl_dev": (1 << 15, 5),
}
_NET_BIND_TCP = 1 << 0  # ABI >= 4
_NET_CONNECT_TCP = 1 << 1  # ABI >= 4

_READ_EXEC = _FS_RIGHTS["read_file"][0] | _FS_RIGHTS["read_dir"][0] | _FS_RIGHTS["execute"][0]
_READ_WRITE_DEV = _FS_RIGHTS["read_file"][0] | _FS_RIGHTS["write_file"][0]

# Default read-only roots the interpreter/libs/shell need to start. Missing
# paths are skipped. Secret mounts are deliberately absent, so they are denied.
_DEFAULT_RO = (
    "/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc",
    sys.base_prefix, sys.prefix,
)
# Device nodes ordinary programs expect; granted read+write, never exec.
_DEV_NODES = ("/dev/null", "/dev/zero", "/dev/full", "/dev/random", "/dev/urandom", "/dev/tty")

# Environment variables to keep for the child; everything else is dropped.
_ENV_ALLOW = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM", "PYTHONHASHSEED"}
# Extra belt: even if allow-listed, never pass a secret-shaped name.
_ENV_DENY_SUBSTR = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "SESSION")
_ENV_DENY_PREFIX = ("AG2SPACE_", "AG2_", "SUTANDO_", "ANTHROPIC_", "OPENAI_", "GEMINI_",
                    "GOOGLE_", "LITELLM_", "AWS_", "KUBERNETES_")


class JailError(RuntimeError):
    """A confinement step failed; the caller must not run the command."""


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)


def landlock_abi() -> int:
    """The kernel's Landlock ABI version, or 0 if Landlock is unavailable.

    Returns 0 on any non-Linux platform without issuing a syscall, so the
    launcher is safe to import and exercise (its fail-closed path) off Linux."""
    if sys.platform != "linux":
        return 0
    try:
        v = _libc().syscall(ctypes.c_long(_NR_landlock_create_ruleset),
                            None, ctypes.c_size_t(0),
                            ctypes.c_uint32(_LANDLOCK_CREATE_RULESET_VERSION))
    except Exception:
        return 0
    return int(v) if v and v > 0 else 0


def _handled_fs(abi: int) -> int:
    return sum(bit for bit, min_abi in _FS_RIGHTS.values() if abi >= min_abi)


def _workdir_access(abi: int) -> int:
    # Everything we handle except device ioctl — full control inside the workdir.
    return _handled_fs(abi) & ~_FS_RIGHTS["ioctl_dev"][0]


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64),
                ("handled_access_net", ctypes.c_uint64)]


def _add_path_rule(libc: ctypes.CDLL, ruleset_fd: int, path: str, access: int) -> None:
    try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError:
        return  # a ro path absent on this image: nothing to grant
    try:
        # struct landlock_path_beneath_attr { __u64 allowed_access; __s32 parent_fd; }
        # packed to 12 bytes. Built with struct.pack (not a packed ctypes.Structure,
        # which warns under Python >= 3.14) so no warning reaches the command's stderr.
        attr = struct.pack("<Qi", access, fd)
        buf = (ctypes.c_char * len(attr)).from_buffer_copy(attr)
        rc = libc.syscall(ctypes.c_long(_NR_landlock_add_rule),
                          ctypes.c_int(ruleset_fd),
                          ctypes.c_int(_LANDLOCK_RULE_PATH_BENEATH),
                          ctypes.byref(buf), ctypes.c_uint32(0))
        if rc != 0:
            raise JailError(f"landlock_add_rule({path}) failed: errno {ctypes.get_errno()}")
    finally:
        os.close(fd)


def apply_landlock(workdir: str, ro_paths: list[str], allow_net: bool) -> None:
    """Restrict the current process to ``workdir`` (rw) + ``ro_paths`` (ro),
    denying everything else and (unless ``allow_net``) all TCP. Raises
    :class:`JailError` if Landlock is unavailable or any step fails."""
    abi = landlock_abi()
    if abi < 1:
        raise JailError("Landlock unavailable on this kernel")
    libc = _libc()
    # Handle (i.e. mediate) TCP only when we intend to deny it. If allow_net,
    # leave net unhandled so connections are not mediated by this ruleset.
    handled_net = (_NET_BIND_TCP | _NET_CONNECT_TCP) if (abi >= 4 and not allow_net) else 0
    attr = _RulesetAttr(handled_access_fs=_handled_fs(abi), handled_access_net=handled_net)
    size = ctypes.sizeof(_RulesetAttr) if abi >= 4 else 8
    ruleset_fd = libc.syscall(ctypes.c_long(_NR_landlock_create_ruleset),
                              ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
    if ruleset_fd < 0:
        raise JailError(f"landlock_create_ruleset failed: errno {ctypes.get_errno()}")
    try:
        _add_path_rule(libc, ruleset_fd, workdir, _workdir_access(abi))
        for p in ro_paths:
            _add_path_rule(libc, ruleset_fd, p, _READ_EXEC)
        for d in _DEV_NODES:
            _add_path_rule(libc, ruleset_fd, d, _READ_WRITE_DEV)
        # No net port rule is ever added: when net is handled, all TCP is denied.
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            raise JailError("prctl(PR_SET_NO_NEW_PRIVS) failed")
        rc = libc.syscall(ctypes.c_long(_NR_landlock_restrict_self),
                          ctypes.c_int(ruleset_fd), ctypes.c_uint32(0))
        if rc != 0:
            raise JailError(f"landlock_restrict_self failed: errno {ctypes.get_errno()}")
    finally:
        os.close(ruleset_fd)


def apply_rlimits(*, cpu_sec: int, mem_mb: int, fsize_mb: int, nofile: int, nproc: int) -> None:
    """Cap CPU time, address space, output file size, open fds and processes.

    Only ever *lowers* a limit: an unprivileged process cannot raise its hard
    limit, so we clamp each request to the current hard cap and set soft=hard so
    the child cannot raise it back. A limit already tighter than requested is
    left alone."""
    def _cap(what: int, want: int) -> None:
        soft, hard = resource.getrlimit(what)
        new = want if hard == resource.RLIM_INFINITY else min(want, hard)
        resource.setrlimit(what, (new, new))
    try:
        _cap(resource.RLIMIT_CPU, cpu_sec)
        _cap(resource.RLIMIT_FSIZE, fsize_mb * 1024 * 1024)
        _cap(resource.RLIMIT_NOFILE, nofile)
        if hasattr(resource, "RLIMIT_NPROC"):
            _cap(resource.RLIMIT_NPROC, nproc)
        # Address space is capped only when asked: RLIMIT_AS set too low breaks
        # the interpreter's own mmap reservations, and the pod's cgroup already
        # bounds memory. mem_mb <= 0 (the default) leaves it to the container.
        if mem_mb > 0:
            _cap(resource.RLIMIT_AS, mem_mb * 1024 * 1024)
    except (ValueError, OSError) as e:
        raise JailError(f"rlimit setup failed: {e}") from e


def clean_env(workdir: str) -> dict[str, str]:
    """A minimal allow-listed environment for the child, with secrets removed."""
    out: dict[str, str] = {}
    for k, v in os.environ.items():
        if k not in _ENV_ALLOW:
            continue
        up = k.upper()
        if any(s in up for s in _ENV_DENY_SUBSTR) or up.startswith(_ENV_DENY_PREFIX):
            continue
        out[k] = v
    out.setdefault("PATH", "/usr/bin:/bin")
    out["HOME"] = workdir
    out["TMPDIR"] = workdir
    out["PWD"] = workdir
    return out


def confine_and_exec(cmd: list[str], *, workdir: str, ro_paths: list[str], allow_net: bool,
                     cpu_sec: int, mem_mb: int, fsize_mb: int, nofile: int, nproc: int,
                     trust_host: bool) -> int:
    """Apply all confinement to this process, then ``execve`` ``cmd``.

    Returns an exit code only on failure to launch; on success the process is
    replaced and this does not return. Fails closed: any confinement error
    means the command is NOT executed unless ``trust_host`` is set."""
    if not cmd:
        return 2
    env = clean_env(workdir)
    try:
        apply_rlimits(cpu_sec=cpu_sec, mem_mb=mem_mb, fsize_mb=fsize_mb, nofile=nofile, nproc=nproc)
        apply_landlock(workdir, ro_paths, allow_net)
    except JailError as e:
        if not trust_host:
            sys.stderr.write(f"[jail] refusing to run unconfined: {e}\n")
            return 70  # EX_SOFTWARE — fail closed
        sys.stderr.write(f"[jail] WARNING: running without confinement ({e})\n")
    try:
        os.execvpe(cmd[0], cmd, env)
    except OSError as e:
        sys.stderr.write(f"[jail] exec failed: {e}\n")
        return 127
    return 0  # unreachable on success


def build_argv(launcher_py: str, python: str, *, workdir: str, cmd: list[str],
               allow_net: bool, trust_host: bool, limits: dict) -> list[str]:
    """The ``argv`` that runs ``cmd`` under this launcher. Pure function so it
    can be unit-tested without Linux."""
    argv = [python, "-I", launcher_py, "--workdir", workdir,
            "--cpu-sec", str(limits["cpu_sec"]), "--mem-mb", str(limits["mem_mb"]),
            "--fsize-mb", str(limits["fsize_mb"]), "--nofile", str(limits["nofile"]),
            "--nproc", str(limits["nproc"])]
    if allow_net:
        argv.append("--allow-net")
    if trust_host:
        argv.append("--trust-host")
    return [*argv, "--", *cmd]


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="jail", description="Run a command under a Landlock jail.")
    p.add_argument("--workdir", required=True)
    p.add_argument("--ro", action="append", default=[], help="extra read-only path")
    p.add_argument("--allow-net", action="store_true", help="do NOT block TCP (default: blocked)")
    p.add_argument("--cpu-sec", type=int, default=30)
    p.add_argument("--mem-mb", type=int, default=0, help="RLIMIT_AS cap; 0 = leave to the cgroup")
    p.add_argument("--fsize-mb", type=int, default=64)
    p.add_argument("--nofile", type=int, default=256)
    p.add_argument("--nproc", type=int, default=64)
    p.add_argument("--trust-host", action="store_true",
                   help="downgrade to unconfined if Landlock is unavailable (default: fail closed)")
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    ns = p.parse_args(argv)
    if ns.cmd and ns.cmd[0] == "--":
        ns.cmd = ns.cmd[1:]
    return ns


def main(argv: list[str]) -> int:
    ns = _parse(argv)
    ro = [p for p in (*_DEFAULT_RO, *ns.ro) if p]
    return confine_and_exec(
        ns.cmd, workdir=ns.workdir, ro_paths=ro, allow_net=ns.allow_net,
        cpu_sec=ns.cpu_sec, mem_mb=ns.mem_mb, fsize_mb=ns.fsize_mb,
        nofile=ns.nofile, nproc=ns.nproc, trust_host=ns.trust_host,
    )


#: Default per-call resource limits for a jailed command. mem_mb=0 leaves
#: address space to the container cgroup (see apply_rlimits).
JAIL_DEFAULTS = {"cpu_sec": 30, "mem_mb": 0, "fsize_mb": 64, "nofile": 256, "nproc": 64}


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

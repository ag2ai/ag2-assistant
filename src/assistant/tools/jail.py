"""Landlock + rlimit launcher: confine this process, then exec the command.

Stdlib-only, so it also runs standalone: ``python -I jail.py --workdir DIR -- CMD``.
"""

import argparse
import ctypes
import os
import resource
import stat
import struct
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# Landlock syscalls and flags (linux/landlock.h); the numbers are shared by every arch.
_NR_CREATE_RULESET = 444
_NR_ADD_RULE = 445
_NR_RESTRICT_SELF = 446
_CREATE_RULESET_VERSION = 1 << 0
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38
_O_PATH = getattr(os, "O_PATH", 0)

# Filesystem rights as (bit, first ABI that defines it).
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
# The rights a rule on a non-directory may carry; any other bit makes add_rule fail.
_FILE_ONLY = sum(
    _FS_RIGHTS[name][0] for name in ("execute", "write_file", "read_file", "truncate", "ioctl_dev")
)
_READ_EXEC = _FS_RIGHTS["read_file"][0] | _FS_RIGHTS["read_dir"][0] | _FS_RIGHTS["execute"][0]
_READ_WRITE = _FS_RIGHTS["read_file"][0] | _FS_RIGHTS["write_file"][0]
_NET_TCP = (1 << 0) | (1 << 1)  # bind + connect, ABI 4
_SCOPE_ALL = (1 << 0) | (1 << 1)  # abstract unix sockets + signals, ABI 6

#: The lowest ABI that can deny TCP; below it the jail refuses unless --allow-net.
MIN_ABI = 4

# Read-only roots the interpreter, shared libraries and coreutils need; absent ones are skipped.
_DEFAULT_RO = (
    "/usr",
    "/lib",
    "/lib64",
    "/bin",
    "/sbin",
    "/etc/ld.so.cache",
    "/etc/localtime",
    "/etc/passwd",
    "/etc/group",
    "/etc/nsswitch.conf",
    "/etc/ssl/openssl.cnf",
    sys.base_prefix,
    sys.prefix,
)
_DEV_NODES = ("/dev/null", "/dev/zero", "/dev/full", "/dev/random", "/dev/urandom")
_ENV_ALLOW = frozenset({"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM", "PYTHONHASHSEED"})


class JailError(RuntimeError):
    """A confinement step failed, so the command must not run."""


@dataclass(frozen=True)
class JailLimits:
    """Per-command rlimits; memory and process count are left to the container cgroup."""

    cpu_sec: int = 30
    fsize_mb: int = 64
    nofile: int = 256


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)


def landlock_abi() -> int:
    """The kernel's Landlock ABI version: 0 off Linux or when Landlock is disabled."""
    if sys.platform != "linux":
        return 0
    version = _libc().syscall(
        ctypes.c_long(_NR_CREATE_RULESET),
        None,
        ctypes.c_size_t(0),
        ctypes.c_uint32(_CREATE_RULESET_VERSION),
    )
    return max(int(version), 0)


def _handled_fs(abi: int) -> int:
    return sum(bit for bit, min_abi in _FS_RIGHTS.values() if abi >= min_abi)


def _workdir_access(abi: int) -> int:
    return _handled_fs(abi) & ~_FS_RIGHTS["ioctl_dev"][0]


def _ruleset_attr(abi: int, allow_net: bool) -> bytes:
    """``struct landlock_ruleset_attr``, cut to the size this ABI knows."""
    net = _NET_TCP if abi >= 4 and not allow_net else 0
    scoped = _SCOPE_ALL if abi >= 6 else 0
    size = 24 if abi >= 6 else 16 if abi >= 4 else 8
    return struct.pack("<QQQ", _handled_fs(abi), net, scoped)[:size]


def _add_path_rule(libc: ctypes.CDLL, ruleset_fd: int, path: Path, access: int) -> None:
    try:
        fd = os.open(path, _O_PATH | os.O_CLOEXEC)
    except FileNotFoundError:
        return
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            access &= _FILE_ONLY
        attr = struct.pack("<Qi", access, fd)  # packed landlock_path_beneath_attr
        buf = ctypes.create_string_buffer(attr, len(attr))
        rc = libc.syscall(
            ctypes.c_long(_NR_ADD_RULE),
            ctypes.c_int(ruleset_fd),
            ctypes.c_int(_RULE_PATH_BENEATH),
            buf,
            ctypes.c_uint32(0),
        )
        if rc != 0:
            raise JailError(f"landlock_add_rule({path}) failed: errno {ctypes.get_errno()}")
    finally:
        os.close(fd)


def apply_landlock(workdir: Path, ro_paths: Sequence[Path], allow_net: bool) -> None:
    """Restrict this process to ``workdir`` (rw) and ``ro_paths`` (read+exec), with no TCP.

    Raises :class:`JailError` when the kernel cannot enforce that or any step fails."""
    abi = landlock_abi()
    if abi < 1:
        raise JailError("Landlock is unavailable on this kernel")
    if abi < MIN_ABI and not allow_net:
        raise JailError(f"Landlock ABI {abi} cannot block TCP (needs {MIN_ABI}, Linux 6.7)")
    libc = _libc()
    attr = _ruleset_attr(abi, allow_net)
    buf = ctypes.create_string_buffer(attr, len(attr))
    ruleset_fd = libc.syscall(
        ctypes.c_long(_NR_CREATE_RULESET), buf, ctypes.c_size_t(len(attr)), ctypes.c_uint32(0)
    )
    if ruleset_fd < 0:
        raise JailError(f"landlock_create_ruleset failed: errno {ctypes.get_errno()}")
    try:
        _add_path_rule(libc, ruleset_fd, workdir, _workdir_access(abi))
        for path in ro_paths:
            _add_path_rule(libc, ruleset_fd, path, _READ_EXEC)
        for node in _DEV_NODES:
            _add_path_rule(libc, ruleset_fd, Path(node), _READ_WRITE)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise JailError(f"prctl(PR_SET_NO_NEW_PRIVS) failed: errno {ctypes.get_errno()}")
        rc = libc.syscall(
            ctypes.c_long(_NR_RESTRICT_SELF), ctypes.c_int(ruleset_fd), ctypes.c_uint32(0)
        )
        if rc != 0:
            raise JailError(f"landlock_restrict_self failed: errno {ctypes.get_errno()}")
    finally:
        os.close(ruleset_fd)


def apply_rlimits(limits: JailLimits) -> None:
    """Lower CPU time, file size and open-fd limits, soft and hard, never raising one."""

    def cap(what: int, want: int) -> None:
        _, hard = resource.getrlimit(what)
        new = want if hard == resource.RLIM_INFINITY else min(want, hard)
        resource.setrlimit(what, (new, new))

    try:
        cap(resource.RLIMIT_CPU, limits.cpu_sec)
        cap(resource.RLIMIT_FSIZE, limits.fsize_mb * 1024 * 1024)
        cap(resource.RLIMIT_NOFILE, limits.nofile)
    except (ValueError, OSError) as e:
        raise JailError(f"rlimit setup failed: {e}") from e


def clean_env(env: Mapping[str, str], workdir: Path) -> dict[str, str]:
    """The allow-listed slice of ``env``, with home and temp pointed at ``workdir``."""
    out = {k: v for k, v in env.items() if k in _ENV_ALLOW}
    out.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    out.update(HOME=str(workdir), TMPDIR=str(workdir), PWD=str(workdir))
    return out


def confine_and_exec(
    cmd: Sequence[str],
    *,
    workdir: Path,
    ro_paths: Sequence[Path],
    allow_net: bool,
    limits: JailLimits,
    env: Mapping[str, str],
) -> int:
    """Confine this process and ``execve`` ``cmd``; returns only when it did not run.

    Any confinement failure returns 70 before anything is changed or executed."""
    if not cmd:
        return 2
    try:
        apply_landlock(workdir, ro_paths, allow_net)
        apply_rlimits(limits)
        os.chdir(workdir)
    except (JailError, OSError) as e:
        sys.stderr.write(f"[jail] refusing to run unconfined: {e}\n")
        return 70
    try:
        os.execvpe(cmd[0], list(cmd), clean_env(env, workdir))
    except OSError as e:
        sys.stderr.write(f"[jail] exec failed: {e}\n")
    return 127


def build_argv(
    launcher: Path, python: str, *, workdir: Path, cmd: Sequence[str], limits: JailLimits
) -> list[str]:
    """The ``argv`` that runs ``cmd`` under this launcher."""
    return [
        python,
        "-I",
        str(launcher),
        "--workdir",
        str(workdir),
        "--cpu-sec",
        str(limits.cpu_sec),
        "--fsize-mb",
        str(limits.fsize_mb),
        "--nofile",
        str(limits.nofile),
        "--",
        *cmd,
    ]


def _parse(argv: Sequence[str]) -> argparse.Namespace:
    defaults = JailLimits()
    p = argparse.ArgumentParser(prog="jail", description="Run a command under a Landlock jail.")
    p.add_argument("--workdir", type=Path, required=True)
    p.add_argument("--allow-net", action="store_true", help="leave TCP unblocked")
    p.add_argument("--cpu-sec", type=int, default=defaults.cpu_sec)
    p.add_argument("--fsize-mb", type=int, default=defaults.fsize_mb)
    p.add_argument("--nofile", type=int, default=defaults.nofile)
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    ns = p.parse_args(argv)
    if ns.cmd[:1] == ["--"]:
        ns.cmd = ns.cmd[1:]
    return ns


def main(argv: Sequence[str], env: Mapping[str, str]) -> int:
    ns = _parse(argv)
    return confine_and_exec(
        ns.cmd,
        workdir=ns.workdir.resolve(),
        ro_paths=[Path(p) for p in _DEFAULT_RO],
        allow_net=ns.allow_net,
        limits=JailLimits(cpu_sec=ns.cpu_sec, fsize_mb=ns.fsize_mb, nofile=ns.nofile),
        env=env,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ))

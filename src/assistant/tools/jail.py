"""Landlock + seccomp + rlimit launcher: run a command confined, then kill what it left.

Stdlib-only, so it also runs standalone: ``python -I jail.py --workdir DIR -- CMD``.
"""

import argparse
import ctypes
import os
import resource
import signal
import stat
import struct
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# Landlock syscalls and flags (linux/landlock.h); the numbers are shared by every arch.
_NR_CREATE_RULESET = 444
_NR_ADD_RULE = 445
_NR_RESTRICT_SELF = 446
_CREATE_RULESET_VERSION = 1 << 0
_RULE_PATH_BENEATH = 1
_PR_SET_PDEATHSIG = 1
_PR_SET_SECCOMP = 22
_PR_SET_CHILD_SUBREAPER = 36
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

#: The lowest ABI that also scopes signals; below it the jail refuses to run.
MIN_ABI = 6

# seccomp BPF (linux/filter.h, linux/seccomp.h): opcodes, return actions, seccomp_data offsets.
_BPF_LD_W_ABS = 0x20
_BPF_JEQ_K = 0x15
_BPF_JGE_K = 0x35
_BPF_RET_K = 0x06
_SECCOMP_MODE_FILTER = 2
_RET_KILL_PROCESS = 0x80000000
_RET_ERRNO = 0x00050000
_RET_ALLOW = 0x7FFF0000
_DATA_NR, _DATA_ARCH, _DATA_ARG0 = 0, 4, 16
_AF_UNIX = 1
_X32_BIT = 0x40000000

# Per machine: (AUDIT_ARCH, socket, socketpair); io_uring_setup/enter/register are 425-427 on both.
_ARCHES = {
    "x86_64": (0xC000003E, 41, 53),
    "aarch64": (0xC00000B7, 198, 199),
}
_IO_URING = (425, 426, 427)

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
    """Per-command rlimits; ``procs`` is how many more tasks the command's user may start."""

    cpu_sec: int = 30
    fsize_mb: int = 64
    nofile: int = 256
    as_mb: int = 4096
    procs: int = 128


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


def _ruleset_attr(abi: int) -> bytes:
    """``struct landlock_ruleset_attr``, cut to the size this ABI knows."""
    net = _NET_TCP if abi >= 4 else 0
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


def apply_landlock(workdir: Path, ro_paths: Sequence[Path]) -> None:
    """Restrict this process to ``workdir`` (rw) and ``ro_paths`` (read+exec), signals scoped.

    Raises :class:`JailError` when the kernel cannot enforce that or any step fails."""
    abi = landlock_abi()
    if abi < MIN_ABI:
        raise JailError(f"Landlock ABI {abi} is too old (needs {MIN_ABI}, Linux 6.12)")
    libc = _libc()
    attr = _ruleset_attr(abi)
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


def socket_filter(machine: str) -> list[tuple[int, int, int, int]]:
    """BPF ``(code, jt, jf, k)`` that refuses socket(), io_uring and non-unix socketpair().

    A foreign syscall ABI (x32, 32-bit compat) kills the process."""
    if machine not in _ARCHES:
        raise JailError(f"no socket filter for machine {machine!r}")
    arch, nr_socket, nr_socketpair = _ARCHES[machine]
    refuse = _RET_ERRNO | 13  # EACCES
    prog = [
        (_BPF_LD_W_ABS, 0, 0, _DATA_ARCH),
        (_BPF_JEQ_K, 1, 0, arch),
        (_BPF_RET_K, 0, 0, _RET_KILL_PROCESS),
        (_BPF_LD_W_ABS, 0, 0, _DATA_NR),
    ]
    if machine == "x86_64":
        prog += [(_BPF_JGE_K, 0, 1, _X32_BIT), (_BPF_RET_K, 0, 0, _RET_KILL_PROCESS)]
    for nr in (nr_socket, *_IO_URING):
        prog += [(_BPF_JEQ_K, 0, 1, nr), (_BPF_RET_K, 0, 0, refuse)]
    prog += [
        (_BPF_JEQ_K, 0, 3, nr_socketpair),
        (_BPF_LD_W_ABS, 0, 0, _DATA_ARG0),
        (_BPF_JEQ_K, 1, 0, _AF_UNIX),
        (_BPF_RET_K, 0, 0, refuse),
        (_BPF_RET_K, 0, 0, _RET_ALLOW),
    ]
    return prog


class _SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    ]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_SockFilter))]


def apply_socket_filter() -> None:
    """Install :func:`socket_filter` on this process; needs no_new_privs already set."""
    prog = socket_filter(os.uname().machine)
    filters = (_SockFilter * len(prog))(*(_SockFilter(*ins) for ins in prog))
    fprog = _SockFprog(len(prog), filters)
    if _libc().prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(fprog), 0, 0) != 0:
        raise JailError(f"seccomp filter install failed: errno {ctypes.get_errno()}")


def _user_tasks(uid: int) -> int:
    """How many tasks (threads included) ``uid`` runs now, the count RLIMIT_NPROC checks."""
    total = 0
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            status = Path(entry.path, "status").read_text()
        except OSError:
            continue
        fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
        if int(fields.get("Uid", "-1").split()[0]) == uid:
            total += int(fields.get("Threads", "1"))
    return total


def apply_rlimits(limits: JailLimits) -> None:
    """Lower CPU, file size, open-fd, address-space and process limits, never raising one."""

    def cap(what: int, want: int) -> None:
        _, hard = resource.getrlimit(what)
        new = want if hard == resource.RLIM_INFINITY else min(want, hard)
        resource.setrlimit(what, (new, new))

    try:
        cap(resource.RLIMIT_CPU, limits.cpu_sec)
        cap(resource.RLIMIT_FSIZE, limits.fsize_mb * 1024 * 1024)
        cap(resource.RLIMIT_NOFILE, limits.nofile)
        cap(resource.RLIMIT_AS, limits.as_mb * 1024 * 1024)
        cap(resource.RLIMIT_NPROC, _user_tasks(os.getuid()) + limits.procs)
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
    limits: JailLimits,
    env: Mapping[str, str],
) -> int:
    """Confine this process and ``execve`` ``cmd``; returns only when it did not run.

    Any confinement failure returns 70 before anything is executed."""
    if not cmd:
        return 2
    try:
        apply_rlimits(limits)
        apply_landlock(workdir, ro_paths)
        apply_socket_filter()
        os.chdir(workdir)
    except (JailError, OSError) as e:
        sys.stderr.write(f"[jail] refusing to run unconfined: {e}\n")
        return 70
    try:
        os.execvpe(cmd[0], list(cmd), clean_env(env, workdir))
    except OSError as e:
        sys.stderr.write(f"[jail] exec failed: {e}\n")
    return 127


def _children(pid: int) -> list[int]:
    """The live and zombie children of ``pid``, read from /proc."""
    found = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            stat_line = Path(entry.path, "stat").read_text()
        except OSError:
            continue
        if int(stat_line.rsplit(")", 1)[1].split()[1]) == pid:
            found.append(int(entry.name))
    return found


def kill_descendants() -> None:
    """SIGKILL and reap every descendant; as subreaper, orphans come back here to be killed."""
    me = os.getpid()
    while True:
        for child in _children(me):
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            reaped, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if reaped == 0:
            time.sleep(0.01)


def _exit_code(status: int) -> int:
    code = os.waitstatus_to_exitcode(status)
    return 128 - code if code < 0 else code


def _on_launcher_death(*_) -> None:
    kill_descendants()
    os._exit(137)


def _keep(launcher: int, run_child) -> int:
    """Subreaper for one command: wait for it, then kill whatever it left behind.

    The launcher dying (a timeout's killpg) ends the run the same way."""
    libc = _libc()
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        return 70
    signal.signal(signal.SIGTERM, _on_launcher_death)
    libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
    if os.getppid() != launcher:
        return 137
    pid = os.fork()
    if pid == 0:
        os._exit(run_child())
    while True:
        reaped, status = os.waitpid(-1, 0)
        if reaped == pid:
            break
    kill_descendants()
    return _exit_code(status)


def run_confined(
    cmd: Sequence[str],
    *,
    workdir: Path,
    ro_paths: Sequence[Path],
    limits: JailLimits,
    env: Mapping[str, str],
) -> int:
    """Run ``cmd`` jailed under a keeper in its own session; nothing it starts outlives it."""
    if landlock_abi() < MIN_ABI:
        sys.stderr.write(
            f"[jail] refusing to run unconfined: Landlock ABI {landlock_abi()} "
            f"is too old (needs {MIN_ABI}, Linux 6.12)\n"
        )
        return 70
    launcher = os.getpid()
    keeper = os.fork()
    if keeper == 0:
        os.setsid()
        code = _keep(
            launcher,
            lambda: confine_and_exec(
                cmd, workdir=workdir, ro_paths=ro_paths, limits=limits, env=env
            ),
        )
        os._exit(code)
    _, status = os.waitpid(keeper, 0)
    return _exit_code(status)


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
        "--as-mb",
        str(limits.as_mb),
        "--procs",
        str(limits.procs),
        "--",
        *cmd,
    ]


def _parse(argv: Sequence[str]) -> argparse.Namespace:
    defaults = JailLimits()
    p = argparse.ArgumentParser(prog="jail", description="Run a command under a Landlock jail.")
    p.add_argument("--workdir", type=Path, required=True)
    p.add_argument("--cpu-sec", type=int, default=defaults.cpu_sec)
    p.add_argument("--fsize-mb", type=int, default=defaults.fsize_mb)
    p.add_argument("--nofile", type=int, default=defaults.nofile)
    p.add_argument("--as-mb", type=int, default=defaults.as_mb)
    p.add_argument("--procs", type=int, default=defaults.procs)
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    ns = p.parse_args(argv)
    if ns.cmd[:1] == ["--"]:
        ns.cmd = ns.cmd[1:]
    return ns


def main(argv: Sequence[str], env: Mapping[str, str]) -> int:
    ns = _parse(argv)
    if not ns.cmd:
        return 2
    return run_confined(
        ns.cmd,
        workdir=ns.workdir.resolve(),
        ro_paths=[Path(p) for p in _DEFAULT_RO],
        limits=JailLimits(
            cpu_sec=ns.cpu_sec,
            fsize_mb=ns.fsize_mb,
            nofile=ns.nofile,
            as_mb=ns.as_mb,
            procs=ns.procs,
        ),
        env=env,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ))

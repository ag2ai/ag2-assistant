"""One-shot Docker sandbox for running untrusted SKILL scripts.

AG2 now ships an official `ag2.extensions.docker.DockerEnvironment` (a
long-lived, cached container), which AG2 Assistant uses for the shell/code tools
— see `tools.build_agent_tools`. This module keeps the one piece AG2's model
doesn't fit: a **one-shot** `docker run --rm` bind-mount sandbox for skill
scripts. Each run gets a fresh container that mounts ONLY the skill's `scripts/`
directory (nothing else on the host) and is removed immediately — the right
hygiene for untrusted, potentially-throwaway skill code, where a reused
long-lived container would accumulate and carry state between runs.

`docker_available()` gates whether the Docker path is used at all (here and for
the shell/code tools).
"""

import asyncio
import shutil
import subprocess
from pathlib import Path, PurePosixPath

from ag2.tools.sandbox.base import ExecResult, SandboxBase

_DEFAULT_IMAGE = "python:3.12-slim"
_DEFAULT_WORKDIR = "/workspace"


def _capture(cmd: list[str], timeout: float, max_output: int) -> ExecResult:
    """Run a docker command, returning combined output trimmed to `max_output`."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ExecResult(output=f"Command timed out after {timeout}s", exit_code=124)
    except FileNotFoundError as exc:
        return ExecResult(output=f"Command not found: {exc}", exit_code=127)
    output = (result.stdout + result.stderr).strip()
    if (total := len(output)) > max_output:
        output = output[:max_output]
        output += f"\n[truncated: showing first {max_output} of {total} chars]"
    return ExecResult(output=output, exit_code=result.returncode or 0)


def docker_available() -> bool:
    """True if the Docker CLI exists and the daemon is reachable."""
    exe = shutil.which("docker")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


class DockerMountSandbox(SandboxBase):
    """One-shot Docker sandbox that bind-mounts a single host directory.

    Each `exec` runs `docker run --rm -v <host_dir>:<workdir> -w <workdir> ...`,
    so the command runs with `<host_dir>` as its working directory and the
    container can see *only* that directory — nothing else on the host. Used to
    run skill scripts: the skill's `scripts/` dir is mounted, `./script.py` runs
    inside, and the container is removed immediately after.
    """

    def __init__(
        self,
        *,
        host_dir: str | Path,
        image: str = _DEFAULT_IMAGE,
        network: str = "bridge",
        workdir: str = "/skill",
        timeout: float = 60,
        max_output: int = 100_000,
        memory: str = "512m",
        cpus: str = "1.0",
    ) -> None:
        if timeout <= 0:
            raise ValueError("`timeout` must be > 0 seconds.")
        self._host_dir = Path(host_dir).resolve()
        self._image = image
        self._network = network
        self._workdir = PurePosixPath(workdir)
        self._default_timeout = timeout
        self._max_output = max_output
        self._memory = memory
        self._cpus = cpus

    @property
    def workdir(self) -> PurePosixPath:
        return self._workdir

    @property
    def host_workdir(self) -> Path:
        return self._host_dir  # the mounted dir is the host-side view

    def _build_argv(self, argv: list[str], env: dict[str, str] | None) -> list[str]:
        cmd = [
            "docker",
            "run",
            "--rm",
            "--network",
            self._network,
            "--memory",
            self._memory,
            "--cpus",
            self._cpus,
            "-v",
            f"{self._host_dir}:{self._workdir}",
            "-w",
            str(self._workdir),
        ]
        for key, value in (env or {}).items():
            cmd += ["-e", f"{key}={value}"]
        cmd += [self._image, *argv]
        return cmd

    async def exec(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        if not argv:
            return ExecResult(output="", exit_code=2)
        cmd = self._build_argv(argv, env)
        eff_timeout = timeout if timeout is not None else self._default_timeout
        return await asyncio.to_thread(_capture, cmd, eff_timeout, self._max_output)


def build_docker_skill_runtime(
    *,
    install_dir: str | Path,
    blocked: list[str] | None = None,
    image: str = _DEFAULT_IMAGE,
    network: str = "bridge",
    timeout: float = 60,
    max_output: int = 100_000,
    extra_paths: list[str] | None = None,
):
    """A `SkillRuntime` that executes each skill script inside a one-shot,
    bind-mounted Docker container (the skill's `scripts/` dir is the only host
    path it can see). Storage/discovery stay local — only execution is sandboxed.
    """
    from ag2.tools.sandbox.adapter import ShellAdapter
    from ag2.tools.skills import LocalRuntime

    class _DockerSkillRuntime(LocalRuntime):
        def shell(self, scripts_dir: Path) -> "ShellAdapter":
            sandbox = DockerMountSandbox(
                host_dir=scripts_dir,
                image=image,
                network=network,
                timeout=timeout,
                max_output=max_output,
            )
            return ShellAdapter(sandbox, blocked=self.blocked or None, timeout=timeout)

    return _DockerSkillRuntime(
        dir=str(install_dir),
        blocked=blocked or [],
        timeout=timeout,
        max_output=max_output,
        extra_paths=extra_paths,
    )


__all__ = [
    "DockerMountSandbox",
    "build_docker_skill_runtime",
    "docker_available",
]

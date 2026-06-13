"""Docker-backed sandbox for AGClaw's shell/code tools.

AG2 ships a `LocalEnvironment` (subprocess on the host) but, as of the current
version, no Docker backend — so this implements one against AG2's public
`Sandbox` protocol. Commands run inside a throwaway container that does **not**
mount the host filesystem, so model-written code/shell can't read or modify the
user's files. That isolation is the safety boundary, which is why the agent
factory drops the per-command approval prompt when this backend is active.

This is a deliberate "build it on top, migrate to native when it lands" move —
when AG2 adds an official `DockerEnvironment`, swap this out for it.
"""

import asyncio
import atexit
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path, PurePosixPath

from autogen.beta.tools.sandbox.base import ExecResult, SandboxBase
from autogen.beta.tools.sandbox.factory import SingletonFactory

_DEFAULT_IMAGE = "python:3.12-slim"
_DEFAULT_WORKDIR = "/workspace"


def docker_available() -> bool:
    """True if the Docker CLI exists and the daemon is reachable."""
    exe = shutil.which("docker")
    if not exe:
        return False
    try:
        return (
            subprocess.run(
                [exe, "info"], capture_output=True, timeout=10
            ).returncode
            == 0
        )
    except Exception:
        return False


class DockerSandbox(SandboxBase):
    """A `Sandbox` that runs commands inside a long-lived Docker container.

    The container is started lazily on first use (`docker run -d ... sleep
    infinity`) and removed on `aclose` / process exit. Each `exec` runs via
    `docker exec`. No host path is bind-mounted, so the container can't touch
    the user's files.
    """

    def __init__(
        self,
        *,
        image: str = _DEFAULT_IMAGE,
        network: str = "bridge",
        workdir: str = _DEFAULT_WORKDIR,
        timeout: float = 60,
        max_output: int = 100_000,
        env_vars: dict[str, str] | None = None,
        memory: str = "512m",
        cpus: str = "1.0",
        name: str | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("`timeout` must be > 0 seconds.")
        self._image = image
        self._network = network
        self._workdir = PurePosixPath(workdir)
        self._default_timeout = timeout
        self._max_output = max_output
        self._env_vars = dict(env_vars or {})
        self._memory = memory
        self._cpus = cpus
        self._name = name or f"agclaw_sbx_{uuid.uuid4().hex[:12]}"
        self._started = False
        self._closed = False
        self._lock = asyncio.Lock()
        self._atexit_registered = False

    @property
    def workdir(self) -> PurePosixPath:
        return self._workdir

    @property
    def host_workdir(self) -> Path | None:
        return None  # container filesystem is not visible on the host

    async def _ensure_started(self) -> None:
        if self._started:
            return
        async with self._lock:
            if self._started:
                return
            argv = [
                "docker", "run", "-d", "--rm",
                "--name", self._name,
                "--network", self._network,
                "--memory", self._memory,
                "--cpus", self._cpus,
                "-w", str(self._workdir),
                self._image,
                "sleep", "infinity",
            ]
            result = await asyncio.to_thread(
                subprocess.run, argv, capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Failed to start Docker sandbox: {result.stderr.strip()}"
                )
            self._started = True
            if not self._atexit_registered:
                atexit.register(self._atexit_cleanup)
                self._atexit_registered = True

    async def exec(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        if self._closed:
            raise RuntimeError("DockerSandbox has been closed.")
        if not argv:
            return ExecResult(output="", exit_code=2)
        await self._ensure_started()

        merged = {**self._env_vars, **(env or {})}
        cmd = ["docker", "exec"]
        for key, value in merged.items():
            cmd += ["-e", f"{key}={value}"]
        cmd += ["-w", str(self._workdir), self._name, *argv]

        eff_timeout = timeout if timeout is not None else self._default_timeout
        return await asyncio.to_thread(self._run_capture, cmd, eff_timeout)

    def _run_capture(self, cmd: list[str], timeout: float) -> ExecResult:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            return ExecResult(output=f"Command timed out after {timeout}s", exit_code=124)
        except FileNotFoundError as exc:
            return ExecResult(output=f"Command not found: {exc}", exit_code=127)
        output = (result.stdout + result.stderr).strip()
        if (total := len(output)) > self._max_output:
            output = output[: self._max_output]
            output += f"\n[truncated: showing first {self._max_output} of {total} chars]"
        return ExecResult(output=output, exit_code=result.returncode or 0)

    async def put_file(self, path: PurePosixPath, content: bytes) -> None:
        if path.is_absolute():
            raise ValueError(f"Absolute paths are not allowed in put_file: {path}")
        await self._ensure_started()
        target = self._workdir / path
        await self.exec(["mkdir", "-p", str(target.parent)])
        cmd = [
            "docker", "exec", "-i", self._name,
            "sh", "-c", f"cat > {shlex.quote(str(target))}",
        ]
        await asyncio.to_thread(
            subprocess.run, cmd, input=content, capture_output=True,
            timeout=self._default_timeout,
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._started:
            await asyncio.to_thread(
                subprocess.run,
                ["docker", "rm", "-f", self._name],
                capture_output=True,
                timeout=30,
            )
        self._unregister_atexit()

    def _unregister_atexit(self) -> None:
        if self._atexit_registered:
            try:
                atexit.unregister(self._atexit_cleanup)
            except ValueError:
                pass
            self._atexit_registered = False

    def _atexit_cleanup(self) -> None:
        if self._started and not self._closed:
            subprocess.run(
                ["docker", "rm", "-f", self._name], capture_output=True, timeout=30
            )


class DockerEnvironment(SingletonFactory):
    """A `SandboxFactory` over one `DockerSandbox` — pass it to
    `SandboxShellTool`/`SandboxCodeTool` exactly like `LocalEnvironment`."""

    def __init__(
        self,
        *,
        image: str = _DEFAULT_IMAGE,
        network: str = "bridge",
        workdir: str = _DEFAULT_WORKDIR,
        timeout: float = 60,
        max_output: int = 100_000,
        env_vars: dict[str, str] | None = None,
        memory: str = "512m",
        cpus: str = "1.0",
    ) -> None:
        super().__init__(
            DockerSandbox(
                image=image,
                network=network,
                workdir=workdir,
                timeout=timeout,
                max_output=max_output,
                env_vars=env_vars,
                memory=memory,
                cpus=cpus,
            )
        )

    async def aclose(self) -> None:
        await self.sandbox.aclose()


__all__ = ["DockerEnvironment", "DockerSandbox", "docker_available"]

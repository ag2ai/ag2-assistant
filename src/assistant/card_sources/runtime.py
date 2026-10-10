"""Retained code executed by LocalRuntime in the selected skills sandbox."""

import asyncio
import json
import os
import shutil
import signal
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from ag2.context import ConversationContext
from ag2.stream import MemoryStream
from ag2.tools.sandbox.base import ExecResult, SandboxBase
from ag2.tools.skills import LocalRuntime

from assistant.card_sources.schema import MAX_OUTPUT, TIMEOUT
from assistant.skills import SKILL_BLOCKED
from assistant.tools.docker_sandbox import DockerMountSandbox, docker_available


class SourceSandbox(SandboxBase):
    """A bounded subprocess with only the explicitly supplied environment."""

    def __init__(self, directory: Path, env: dict):
        self.directory = directory
        self.env = env

    @property
    def workdir(self) -> PurePosixPath:
        return PurePosixPath(str(self.directory))

    @property
    def host_workdir(self) -> Path:
        return self.directory

    async def exec(self, argv, *, env=None, timeout=None) -> ExecResult:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self.directory,
            env={**self.env, **(env or {})},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        output = bytearray()
        try:
            async with asyncio.timeout(timeout or TIMEOUT):
                assert process.stdout is not None
                while block := await process.stdout.read(4096):
                    output.extend(block)
                    if len(output) > MAX_OUTPUT:
                        raise ValueError("Source output exceeds its JSON size limit")
                await process.wait()
        finally:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        return ExecResult(
            output=output.decode(errors="replace").strip(), exit_code=process.returncode or 0
        )


class SourceDockerSandbox(DockerMountSandbox):
    """Supply only selected source Secrets to the existing one-shot sandbox."""

    def __init__(self, env, cli, search_path, **kwargs):
        super().__init__(**kwargs)
        self.env = env
        self.cli = cli
        self.host = SourceSandbox(self.host_workdir, {"PATH": search_path})

    async def exec(self, argv, *, env=None, timeout=None):
        name = f"ag2-card-source-{uuid.uuid4().hex}"
        command = self._build_argv(argv, {**self.env, **(env or {})})
        command[0] = self.cli
        command[3:3] = ["--name", name]
        try:
            return await self.host.exec(command, timeout=timeout)
        finally:
            await asyncio.shield(self.host.exec([self.cli, "rm", "-f", name], timeout=5))


async def execute_source(config, source, arguments, keys) -> str:
    """Materialize one retained artifact, execute it, then remove its temporary files."""
    with tempfile.TemporaryDirectory(prefix="card-source-") as temporary:
        root = Path(temporary)
        skill = root / "retained-source"
        scripts = skill / "scripts"
        scripts.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: retained-source\ndescription: Retained Card source\n---\nReturn source JSON.\n"
        )
        (scripts / "source.py").write_text(source.code)
        for name, contents in source.files.items():
            file = scripts / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(contents)
        sandbox: SandboxBase
        search = os.pathsep.join(str(path) for path in config.search_path)
        if config.tools.sandbox == "docker":
            if not await asyncio.to_thread(docker_available, config.search_path):
                raise ValueError("Selected Docker skills runtime is unavailable")
            sandbox = SourceDockerSandbox(
                keys,
                cli=shutil.which("docker", path=search),
                search_path=search,
                host_dir=scripts,
                image=config.tools.docker_image,
                network=config.tools.docker_network,
                timeout=TIMEOUT,
                max_output=MAX_OUTPUT,
            )
        else:
            python = shutil.which("python3", path=search)
            if python is None:
                raise ValueError("Python is unavailable in the selected skills runtime")
            sandbox = SourceSandbox(scripts, {"PATH": search, **keys})
        runtime = LocalRuntime(
            dir=str(root),
            blocked=SKILL_BLOCKED,
            sandbox=sandbox,
            timeout=TIMEOUT,
            max_output=MAX_OUTPUT,
        )
        return await runtime.execute(
            "retained-source",
            "source.py",
            ConversationContext(stream=MemoryStream()),
            [json.dumps(arguments)],
        )

"""The `jailed` code/shell backend: a LocalSandbox whose commands run under the jail launcher."""

import os
import sys
from pathlib import Path

from ag2.tools.code.environment.base import CodeLanguage
from ag2.tools.sandbox.base import ExecResult
from ag2.tools.sandbox.factory import SingletonFactory
from ag2.tools.sandbox.local import LocalSandbox

from assistant.tools import jail
from assistant.tools.jail import JailLimits, build_argv

_LAUNCHER = Path(jail.__file__).resolve()


class JailedSandbox(LocalSandbox):
    """A LocalSandbox that confines every command to its work directory via Landlock."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        limits: JailLimits | None = None,
        **kwargs,
    ) -> None:
        super().__init__(path, **kwargs)
        self._limits = limits or JailLimits()

    async def exec(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        if not argv:
            return await super().exec(argv, env=env, timeout=timeout)
        wrapped = build_argv(
            _LAUNCHER,
            sys.executable,
            workdir=self.host_workdir,
            cmd=argv,
            limits=self._limits,
        )
        return await super().exec(wrapped, env=env, timeout=timeout)


class JailedEnvironment(SingletonFactory):
    """Drop-in for AG2's LocalEnvironment that runs every command in a JailedSandbox."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        cleanup: bool | None = None,
        timeout: float = 60,
        max_output: int = 100_000,
        languages: tuple[CodeLanguage, ...] = ("python", "bash"),
        limits: JailLimits | None = None,
    ) -> None:
        super().__init__(
            JailedSandbox(
                path,
                cleanup=cleanup,
                timeout=timeout,
                max_output=max_output,
                languages=languages,
                limits=limits,
            )
        )

    async def aclose(self) -> None:
        await self.sandbox.aclose()


__all__ = ("JailedEnvironment", "JailedSandbox")

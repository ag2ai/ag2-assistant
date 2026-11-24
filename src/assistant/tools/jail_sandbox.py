# Copyright (c) 2026, AG2ai, Inc., AG2ai open-source projects maintainers and core contributors
#
# SPDX-License-Identifier: Apache-2.0
"""AG2 sandbox/environment backed by the Landlock jail launcher.

:class:`JailedSandbox` is a :class:`~ag2.tools.sandbox.local.LocalSandbox` whose
every command is run through :mod:`assistant.tools.jail` — the model-written
code executes confined to the sandbox work directory, with no network and with
secret-bearing environment variables stripped. File staging (``put_file`` /
``get_file`` / ``workdir``) is inherited unchanged, so the AG2 code/shell tools
use it exactly as they use ``LocalEnvironment``.

Select it with ``AG2ASSISTANT_SANDBOX=jailed`` (wired in
:func:`assistant.tools.build_agent_tools`). Unlike ``local`` it needs no
approval prompts, because the jail — not a human — is what prevents access to
the user's files and secrets; unlike ``docker`` it needs no daemon.
"""

from __future__ import annotations

import os
import sys

from ag2.tools.code.environment.base import CodeLanguage
from ag2.tools.sandbox.factory import SingletonFactory
from ag2.tools.sandbox.local import LocalSandbox

from assistant.tools import jail

_LAUNCHER = os.path.abspath(jail.__file__)


class JailedSandbox(LocalSandbox):
    """A :class:`LocalSandbox` whose every ``exec`` runs under the Landlock jail."""

    def __init__(self, *args, allow_net: bool = False, trust_host: bool = False,
                 limits: dict | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._allow_net = allow_net
        self._trust_host = trust_host
        self._limits = {**jail.JAIL_DEFAULTS, **(limits or {})}

    async def exec(self, argv, *, env=None, timeout=None):  # type: ignore[override]
        if not argv:
            return await super().exec(argv, env=env, timeout=timeout)
        wrapped = jail.build_argv(
            _LAUNCHER, sys.executable, workdir=str(self.host_workdir),
            cmd=list(argv), allow_net=self._allow_net, trust_host=self._trust_host,
            limits=self._limits,
        )
        return await super().exec(wrapped, env=env, timeout=timeout)


class JailedEnvironment(SingletonFactory):
    """Landlock-jailed local backend — a drop-in for
    :class:`~ag2.tools.LocalEnvironment` that confines model-run code to its
    work directory with no network and no secret environment."""

    def __init__(
        self,
        path=None,
        *,
        cleanup: bool | None = None,
        timeout: float = 60,
        max_output: int = 100_000,
        env_vars: dict[str, str] | None = None,
        languages: tuple[CodeLanguage, ...] = ("python", "bash"),
        allow_net: bool = False,
        trust_host: bool = False,
        limits: dict | None = None,
    ) -> None:
        super().__init__(
            JailedSandbox(
                path, cleanup=cleanup, timeout=timeout, max_output=max_output,
                env_vars=env_vars, languages=languages,
                allow_net=allow_net, trust_host=trust_host, limits=limits,
            )
        )

    async def aclose(self) -> None:
        await self.sandbox.aclose()


__all__ = ("JailedEnvironment", "JailedSandbox")

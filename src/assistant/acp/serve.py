"""Serve one profile's Agent over ACP stdio.

Reuses the same construction path as the CLI ``chat`` command: resolve the
profile, build a ``Gateway``, take its agent, wrap it in ``ag2.acp.ACPAgent``.
Never touches the onboarding path — that opens a browser popup and would hang
a stdio client forever.
"""

import contextlib
import sys
from collections.abc import Mapping

from acp.core import DEFAULT_STDIO_BUFFER_LIMIT_BYTES
from acp.stdio import stdio_streams
from ag2 import Agent
from ag2.acp import ACPAgent, SessionConfig
from ag2.acp.guard import serve as guard_serve

from assistant.acp.approvals import install_owner_side_approvals
from assistant.acp.auth import choose_auth
from assistant.acp.chats import ChatBackedStorage, ChatTrackingACPAgent
from assistant.acp.sdk_watch import watch_send_loop
from assistant.gateway.core import Gateway
from assistant.gateway.profile_manager import (
    ArchivedProfile,
    UnknownProfile,
    resolve_active_profile,
)
from assistant.hitl import DesktopAsker
from assistant.paths import Paths
from assistant.version import __version__

_SETUP_HINT = "run: ag2-assistant profiles create <name>"


class _NoProfileClient:
    """LLM client stand-in that fails every turn with a clear setup message."""

    def __init__(self, message: str) -> None:
        self._message = message

    async def __call__(self, messages, context, **kwargs):
        raise RuntimeError(self._message)


class _NoProfileConfig:
    """Model config stand-in so ``session/prompt`` fails with our own message.

    ``ACPAgent``'s executor rejects ``agent.config is None`` before anything else
    runs, with a generic message we do not control — so the cold-start Agent needs
    a real (if inert) config object to reach ``_NoProfileClient`` instead.
    """

    def __init__(self, message: str) -> None:
        self._message = message

    @property
    def provider(self):
        raise NotImplementedError

    @property
    def model(self) -> str:
        raise NotImplementedError

    def copy(self) -> "_NoProfileConfig":
        return self

    def create(self) -> _NoProfileClient:
        return _NoProfileClient(self._message)

    def create_files_client(self):
        raise NotImplementedError


def cold_start_agent(reason: str) -> Agent:
    """An Agent that completes ``initialize`` but fails every turn, cleanly.

    Used when no profile could be resolved: the handshake must still succeed
    (§ Acceptance), and the failure has to name the fix rather than hang or crash.
    """
    message = f"{reason} — {_SETUP_HINT}"
    return Agent(name="ag2-assistant", config=_NoProfileConfig(message))


async def _run_stdio_guarded(acp_agent: ACPAgent) -> None:
    """Serve ``acp_agent`` over stdio with fd 1 reserved for the protocol writer.

    ``stdio_streams`` captures the real stdout fd once, at connect time — POSIX
    pipe transports read ``sys.stdout.fileno()`` and write through that raw fd
    from then on (see ``asyncio.unix_events._UnixWritePipeTransport``), so
    reassigning ``sys.stdout`` only after this call cannot disturb the wire.
    Everything after this point — our code, ag2, any transitive dependency — has
    its ``print()`` land on stderr instead.
    """
    reader, writer = await stdio_streams(limit=DEFAULT_STDIO_BUFFER_LIMIT_BYTES)
    with contextlib.redirect_stdout(sys.stderr), watch_send_loop():
        await guard_serve(acp_agent.bind, reader, writer)


async def serve_stdio(
    profile: str | None,
    paths: Paths,
    *,
    memory: bool = True,
    env: Mapping[str, str] | None = None,
    connection_id: str = "acp:stdio",
) -> None:
    """Serve one profile's Agent over ACP stdio until the Client disconnects.

    ``paths``/``env`` are the caller's resolved layout — this module stays below
    the environment boundary (``cli.py`` resolves both and passes them in).
    Cold start (no profile configured yet) still completes ``initialize``; the
    setup gap surfaces as a per-turn error instead of blocking the handshake.
    ``connection_id`` names the Peer rows this listener's sessions persist
    under; a stored listener passes its real Connection id
    Connection.
    """
    gateway: Gateway | None = None
    asker: DesktopAsker | None = None
    acp_agent: ACPAgent
    try:
        pid, config, factory = resolve_active_profile(profile, paths=paths, env=env)
    except (UnknownProfile, ArchivedProfile) as exc:
        agent = cold_start_agent(str(exc))
        # Unconfigured ⇒ advertise terminal/env_var and gate sessions (ADR-0035).
        acp_agent = ACPAgent(
            agent, name="AG2 Assistant", version=__version__, auth=choose_auth(None, env or {})
        )
    else:
        gateway = Gateway(config=config, memory=memory, platform="acp", config_factory=factory)
        await gateway.start()
        agent = gateway.require_agent()
        # Owner-side approvals — never the ACP client. See approvals.py.
        asker = DesktopAsker()
        install_owner_side_approvals(agent, gateway, asker)
        # Sessions persist as Chats in this profile's own chats.db (ADR 0034).
        chat_storage = ChatBackedStorage(
            paths=paths, data_dir=config.data_dir, profile=pid, mirror=gateway.emit_event
        )
        acp_agent = ChatTrackingACPAgent(
            agent,
            name="AG2 Assistant",
            version=__version__,
            sessions=SessionConfig(storage=chat_storage),
            chat_storage=chat_storage,
            connection_id=connection_id,
            auth=choose_auth(config, env or {}),
        )
    try:
        await _run_stdio_guarded(acp_agent)
    finally:
        if asker is not None:
            await asker.aclose()
        if gateway is not None:
            await gateway.close()

"""Serve an ALREADY-BUILT agent over ACP WebSocket — the manager-owned
counterpart to ``serve_ws.serve_ws``, which builds its own ``Gateway`` per listener.

``ProfileManager`` boots one ``ProfileRuntime`` (and its ``Gateway``) per profile
already; a listener it supervises reuses that runtime's agent instead of standing up
a second ``Gateway`` for the same profile. ``acp-serve`` (standalone hosting) keeps
using ``serve_ws``, which owns its whole stack end to end.

Also holds the ``acp-serve`` CLI's find-or-create helpers, so the auto-registration
logic is unit-testable apart from the typer command.
"""

from ag2 import Agent
from ag2.acp import ACPAgent, SessionConfig
from ag2.acp.auth import AuthProvider

from assistant.acp.chats import ChatBackedStorage, ChatTrackingACPAgent
from assistant.acp.serve_ws import serve_acp_agent
from assistant.connections import AcpConnection, ConnectionStore
from assistant.version import __version__


async def serve_listener(
    agent: Agent,
    *,
    host: str = "127.0.0.1",
    port: int,
    token: str = "",
    name: str = "AG2 Assistant",
    chat_storage: ChatBackedStorage | None = None,
    connection_id: str = "",
    auth: AuthProvider | None = None,
) -> None:
    """Serve ``agent`` over ACP WebSocket until cancelled. ``agent`` is expected to
    already be built (a running profile's gateway agent) — this never constructs a
    ``Gateway`` of its own. With ``chat_storage``, sessions persist as
    Chats attributed to ``connection_id`` — the stored listener's real id. ``auth``
    is the caller's ``choose_auth`` result."""
    acp_agent: ACPAgent
    if chat_storage is not None:
        acp_agent = ChatTrackingACPAgent(
            agent,
            name=name,
            version=__version__,
            sessions=SessionConfig(storage=chat_storage, retain_history=True),
            chat_storage=chat_storage,
            connection_id=connection_id or "acp:listener",
            auth=auth,
        )
    else:
        acp_agent = ACPAgent(agent, name=name, version=__version__, auth=auth)
    await serve_acp_agent(acp_agent, host=host, port=port, token=token)


def find_acp_connection(store: ConnectionStore, id_or_name: str) -> AcpConnection | None:
    """A stored ACP listener by id, then by exact display name."""
    listener = store.get_acp_connection(id_or_name)
    if listener is not None:
        return listener
    return next((c for c in store.list_acp_connections() if c.connection.name == id_or_name), None)


def ensure_acp_connection(
    store: ConnectionStore, profile: str, port: int, *, token: str = "", name: str = ""
) -> AcpConnection:
    """Find-or-create the listener for ``(profile, port)`` — what a bare
    ``acp-serve --profile X --port N`` relies on so every listener that exists is
    visible in Settings, instead of serving invisibly. Reuses an
    identical existing record rather than creating a duplicate."""
    for listener in store.list_acp_connections():
        if listener.profile == profile and listener.port == port:
            return listener
    return store.create_acp_connection(profile, name=name, port=port, token=token)


class UnknownAcpConnection(Exception):
    """Raised when ``--connection`` names no stored ACP listener."""


def stdio_connection_target(store: ConnectionStore, id_or_name: str) -> tuple[str, str]:
    """The ``(profile, connection_id)`` a stdio listener serves for a stored record.

    Port is ignored: stdio has no port, and a portless record is precisely a stdio
    listener — the case ``acp-serve`` rejects.
    """
    listener = find_acp_connection(store, id_or_name)
    if listener is None:
        raise UnknownAcpConnection(f"unknown ACP connection: {id_or_name!r}")
    return listener.profile, listener.connection.id

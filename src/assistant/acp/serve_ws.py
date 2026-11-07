"""Serve one profile's Agent over ACP WebSocket — experimental remote listener.

Reuses ``serve.py``'s profile resolution and cold-start Agent and mounts
the SDK's own WS-capable ASGI server (``acp.http.asgi.create_asgi_app``) around
``ACPAgent.bind``, whose ``(client) -> scope`` signature matches the SDK's
``AgentFactory`` exactly — the same seam ``ag2.acp.guard.serve`` uses for stdio.
One standalone uvicorn instance per listener, one profile, one port: this does not
mount into the shared gateway app, which serves every profile.
"""

import asyncio
import contextlib
import json
import secrets
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import uvicorn
from acp.http.asgi import create_asgi_app
from ag2 import Agent
from ag2.acp.agent import ACPAgent
from ag2.acp.sessions import SessionConfig

from assistant.acp.approvals import install_owner_side_approvals
from assistant.acp.auth import choose_auth
from assistant.acp.chats import ChatBackedStorage, ChatTrackingACPAgent
from assistant.acp.sdk_watch import watch_send_loop
from assistant.acp.serve import cold_start_agent
from assistant.activity import Activity, holding_send
from assistant.gateway.core import Gateway
from assistant.gateway.profile_manager import (
    ArchivedProfile,
    UnknownProfile,
    resolve_active_profile,
)
from assistant.hitl import DesktopAsker
from assistant.paths import Paths
from assistant.version import __version__

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]

# Loopback names ``--host`` may legitimately be; anything else needs a token.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class NonLoopbackTokenRequired(RuntimeError):
    """Raised at startup: a non-loopback bind with no token would be reachable by
    anyone on that network, so refuse rather than serve an unauthenticated agent."""


def require_token_for_non_loopback(host: str, token: str) -> None:
    if token or host in _LOOPBACK_HOSTS:
        return
    raise NonLoopbackTokenRequired(
        f"refusing to bind {host!r} with no --token set: an ACP listener on a "
        "non-loopback interface with no token would let anyone reaching that "
        "network drive this profile's agent. Pass --token, or bind a loopback "
        "host (127.0.0.1 / ::1 / localhost)."
    )


def _bearer_token(headers: list[tuple[bytes, bytes]]) -> str | None:
    """The token from an ``Authorization: Bearer <token>`` header, if present."""
    for name, value in headers:
        if name.lower() == b"authorization":
            scheme, _, rest = value.decode("latin-1").partition(" ")
            return rest if scheme.lower() == "bearer" else None
    return None


def _token_authorized(scope: Scope, token: str) -> bool:
    """Constant-time check that the WS upgrade carried the shared token."""
    if not token:
        return True
    presented = _bearer_token(scope.get("headers", []))
    return presented is not None and secrets.compare_digest(presented, token)


def _items(message: Any) -> list[dict]:
    """The JSON-RPC objects a WebSocket frame carries (none when it cannot be read)."""
    raw = message.get("text")
    if raw is None:
        raw = message.get("bytes")
    if raw is None:
        return []
    try:
        payload = json.loads(raw)
    except Exception:
        return []
    items = payload if isinstance(payload, list) else [payload]
    return [item for item in items if isinstance(item, dict)]


def _key(item: dict) -> str:
    return json.dumps(item["id"])  # 1 and "1" are different JSON-RPC ids


def request_ids(message: Any) -> list[str]:
    """The ids of the requests (not notifications) an incoming frame carries, admitted
    the way the SDK's inbound pump admits them: one JSON object in a text frame. A
    binary frame or a batch is ignored by the SDK, so it would never be answered."""
    raw = message.get("text")
    if not isinstance(raw, str):
        return []
    try:
        item = json.loads(raw)
    except Exception:
        return []
    if not isinstance(item, dict) or "method" not in item or "id" not in item:
        return []
    return [_key(item)]


def response_ids(message: Any) -> list[str]:
    """The ids answered (result or error) by an outgoing frame."""
    return [
        _key(item)
        for item in _items(message)
        if "id" in item and "method" not in item and ("result" in item or "error" in item)
    ]


class RequestHolds:
    """One connection's in-flight JSON-RPC requests as ``work``: a request holds from
    the frame that brought it to the frame carrying its response, matched by id, so a
    backpressured or interleaved frame for another request never frees it."""

    def __init__(self, activity: Activity, detail: str = "acp_request") -> None:
        self._activity = activity
        self._detail = detail
        self._pending: dict[str, Callable[[], None]] = {}

    def on_receive(self, message: Any) -> None:
        if message.get("type") != "websocket.receive":
            return
        for rid in request_ids(message):
            previous = self._pending.pop(rid, None)
            if previous is not None:
                previous()  # an id reused before its answer: the old wait is over
            self._pending[rid] = self._activity.acquire(self._detail)

    def on_send(self, message: Any) -> None:
        if message.get("type") != "websocket.send":
            return
        for rid in response_ids(message):
            release = self._pending.pop(rid, None)
            if release is not None:
                release()

    def close(self) -> None:
        """The connection is gone: nothing more will be answered."""
        while self._pending:
            self._pending.popitem()[1]()


def build_ws_app(acp_agent: ACPAgent, token: str) -> AsgiApp:
    """The listener's whole ASGI front door: WebSocket-only, token at the upgrade,
    and per-connection session teardown.

    - An ``http`` scope gets 404 before the SDK ever sees it (the SDK app also
      speaks Streamable HTTP, which this listener deliberately does not serve —
      ADR 0032: WebSocket only), token or no token.
    - The WS token is checked against ``scope["headers"]`` (set on the handshake
      request); a bad token gets ``websocket.close`` instead of ``websocket.accept``,
      failing the upgrade itself (uvicorn answers it with HTTP 403).
    - Each accepted connection gets its own SDK app around a bind that records the
      connection scope; when the socket closes, that scope is ``aclose()``d, which
      drops its ``SessionStore`` and detaches its Client from the listener's
      outbound side. Nothing upstream does this — the SDK only clears its own
      registry on disconnect — so without it a quit client leaves its sessions
      (and the chat's live badge) alive forever.
    """
    base = create_asgi_app(acp_agent.bind)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # lifespan passthrough only
    activity: Activity = getattr(acp_agent, "activity", None) or Activity()

    async def guarded(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            if not _token_authorized(scope, token):
                message = await receive()
                if message["type"] == "websocket.connect":
                    await send({"type": "websocket.close", "code": 4401})
                return
            scopes: list[Any] = []

            def tracked_bind(client: Any) -> Any:
                conn_scope = acp_agent.bind(client)
                scopes.append(conn_scope)  # the one teardown handle
                return conn_scope

            conn_app = create_asgi_app(tracked_bind)
            held = holding_send(send, activity, "acp_send")
            requests = RequestHolds(activity)

            async def receiving() -> Any:
                message = await receive()
                requests.on_receive(message)
                return message

            async def delivering(message: Any) -> None:
                await held(message)
                requests.on_send(message)

            try:
                await conn_app(scope, receiving, delivering)
            finally:
                requests.close()
                # Close the connection's sessions and detach its outbound receiver.
                for conn_scope in scopes:
                    with contextlib.suppress(Exception):
                        await conn_scope.aclose()
            return
        if scope["type"] == "http":
            await _respond_ws_only(receive, send)
            return
        await base(scope, receive, send)

    return guarded


async def _respond_ws_only(receive: Receive, send: Send) -> None:
    """404 for any plain-HTTP request: this door serves ACP over WebSocket only."""
    while (await receive())["type"] == "http.request":
        break
    body = b"this listener serves ACP over WebSocket only\n"
    await send(
        {
            "type": "http.response.start",
            "status": 404,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def serve_acp_agent(acp_agent: ACPAgent, *, host: str, port: int, token: str) -> None:
    """Serve an already-wrapped ``ACPAgent`` on the WebSocket door until cancelled."""
    app = build_ws_app(acp_agent, token)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    try:
        with watch_send_loop():
            await server.serve()
    except asyncio.CancelledError:
        # uvicorn only runs its own shutdown (closing the listening socket) after
        # main_loop() returns normally; cancellation skips straight past it.
        await server.shutdown()
        raise


async def serve_ws(
    profile: str | None,
    paths: Paths,
    *,
    host: str = "127.0.0.1",
    port: int = 8802,
    token: str = "",
    memory: bool = True,
    env: Mapping[str, str] | None = None,
    agent_factory: Callable[..., Agent] | None = None,
    connection_id: str = "acp:ws",
) -> None:
    """Serve one profile's Agent over ACP WebSocket until the server is stopped.

    Cold start (no profile configured yet) still completes ``initialize``, same as
    ``serve_stdio``. ``agent_factory`` is a test seam only (forwarded to ``Gateway``
    unchanged); production always leaves it unset so the real agent is built.
    ``connection_id`` names the Peer rows this listener's sessions persist under
    a stored listener passes its real Connection id instead.
    """
    require_token_for_non_loopback(host, token)

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
        gateway = Gateway(
            config=config,
            memory=memory,
            platform="acp",
            config_factory=factory,
            agent_factory=agent_factory,
        )
        await gateway.start()
        agent = await gateway.acp_agent()
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
            sessions=SessionConfig(storage=chat_storage, retain_history=True),
            chat_storage=chat_storage,
            connection_id=connection_id,
            auth=choose_auth(config, env or {}),
        )
    try:
        await serve_acp_agent(acp_agent, host=host, port=port, token=token)
    finally:
        if asker is not None:
            await asker.aclose()
        if gateway is not None:
            await gateway.close()

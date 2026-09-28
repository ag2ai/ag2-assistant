"""ACP over WebSocket: the SDK's own ``create_asgi_app`` served under
uvicorn, gated by a per-listener token checked at the upgrade. No real LLM: the
happy path serves a ``TestConfig``-backed Agent; cold start needs no profile at all.
"""

import asyncio
import contextlib
import socket

import acp
import pytest
import websockets.exceptions
from ag2 import Agent as Ag2Agent
from ag2.acp import ACPRemoteConfig
from ag2.testing import TestConfig

from assistant.acp.serve_ws import (
    NonLoopbackTokenRequired,
    require_token_for_non_loopback,
    serve_ws,
)
from assistant.profiles import ProfileRegistry
from tests.support.fakes import fake_agent_factory


def _free_port() -> int:
    """A loopback port nothing is listening on yet — released before the caller binds it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_listening(host: str, port: int, timeout: float = 5.0) -> None:
    """Poll a raw TCP connect until ``serve_ws``'s uvicorn instance is accepting."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            _, writer = await asyncio.open_connection(host, port)
        except OSError:
            if loop.time() > deadline:
                raise
            await asyncio.sleep(0.02)
            continue
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return


@contextlib.asynccontextmanager
async def _running_server(paths, *, profile=None, host="127.0.0.1", token="", agent_factory=None):
    """Run ``serve_ws`` as a background task on a fresh ephemeral port; cancel + drain
    it on exit, mirroring how a supervisor would stop one listener."""
    port = _free_port()
    task = asyncio.create_task(
        serve_ws(profile, paths, host=host, port=port, token=token, agent_factory=agent_factory)
    )
    try:
        await _wait_listening(host, port)
        yield port
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_ag2_remote_config_drives_the_served_agent_over_websocket(paths, monkeypatch):
    """End-to-end: ag2's own ``ACPRemoteConfig`` (the client this door is proven
    against) dials in over a real WebSocket with the shared token and gets the
    scripted reply back from a served, TestConfig-backed Agent."""
    ProfileRegistry(paths).create_profile("ws-test", "#336699")
    # fixture profile has no real key; it stands in for a credentialed install
    monkeypatch.setattr("assistant.acp.auth.profile_has_credentials", lambda config, env: True)
    scripted = fake_agent_factory(
        agent=lambda config, **kwargs: Ag2Agent(name="acp-ws-test", config=TestConfig("pong")),
    )
    token = "s3cret-listener-token"  # noqa: S105 - test fixture, not a real credential

    async with _running_server(paths, token=token, agent_factory=scripted) as port:
        remote = ACPRemoteConfig(
            url=f"ws://127.0.0.1:{port}/acp",
            headers={"Authorization": f"Bearer {token}"},
            expose_tools=False,
            permission_policy="deny",
        )
        client_side = Ag2Agent(name="ws-client", config=remote)
        reply = await client_side.ask("hi")
        assert "pong" in str(await reply.content()).lower()


async def test_wrong_or_missing_token_is_rejected_at_the_upgrade(paths):
    """A bad (or absent) token never reaches ACP traffic: the WebSocket handshake
    itself is refused (HTTP 403 on the upgrade), before ``initialize`` or any
    session exists — the same failure shape for both a wrong and a missing token."""
    token = "the-real-token"  # noqa: S105 - test fixture, not a real credential

    async with _running_server(paths, token=token) as port:
        for headers in ({"Authorization": "Bearer wrong-token"}, {}):
            remote = ACPRemoteConfig(
                url=f"ws://127.0.0.1:{port}/acp",
                headers=headers,
                expose_tools=False,
                permission_policy="deny",
            )
            client_side = Ag2Agent(name="ws-client-bad-token", config=remote)
            with pytest.raises(websockets.exceptions.InvalidStatus):
                await client_side.ask("hi")


async def test_non_loopback_bind_with_no_token_is_refused_before_binding(paths):
    """Startup refuses a non-loopback host with no token, naming why — asserted
    without ever actually binding 0.0.0.0 (the guard runs before uvicorn does)."""
    with pytest.raises(NonLoopbackTokenRequired, match="0.0.0.0"):
        await serve_ws(None, paths, host="0.0.0.0", token="")

    # Same guard, exercised directly: a loopback host or a non-empty token passes.
    require_token_for_non_loopback("127.0.0.1", "")
    require_token_for_non_loopback("0.0.0.0", "some-token")
    with pytest.raises(NonLoopbackTokenRequired):
        require_token_for_non_loopback("0.0.0.0", "")


async def test_cold_start_over_ws_mirrors_stdio_initialize_ok_prompt_errors(paths):
    """No profile registered in ``paths``: the handshake still completes (as it does
    over stdio), and the unconfigured session is refused with auth_required (ADR-0035)."""
    async with _running_server(paths) as port:
        remote = ACPRemoteConfig(
            url=f"ws://127.0.0.1:{port}/acp",
            expose_tools=False,
            permission_policy="deny",
        )
        client_side = Ag2Agent(name="ws-client-cold", config=remote)
        with pytest.raises(acp.RequestError) as exc_info:
            await client_side.ask("hi")
        assert "auth" in str(exc_info.value).lower()  # session refused (ADR-0035)


async def test_plain_http_is_refused_even_with_a_valid_token(paths):
    """The SDK app behind the guard also speaks Streamable HTTP; this door must not
    (ADR 0032: WebSocket only) — 404 regardless of Authorization."""
    import httpx

    token = "s3cret-listener-token"  # noqa: S105 - test fixture, not a real credential
    async with _running_server(paths, token=token) as port:
        async with httpx.AsyncClient() as client:
            for headers in ({}, {"Authorization": f"Bearer {token}"}):
                resp = await client.post(
                    f"http://127.0.0.1:{port}/acp",
                    headers={"content-type": "application/json", **headers},
                    content=b"{}",
                )
                assert resp.status_code == 404


async def test_ws_door_installs_owner_side_approvals(paths):
    """The approvals middleware guards this door too — the mechanism is
    agent-level and transport-independent, so presence on the served agent is the
    contract (behavior is covered in tests/test_acp_approvals.py)."""
    from assistant.acp.approvals import _OwnerApprovalMiddleware

    ProfileRegistry(paths).create_profile("ws-approvals", "#336699")
    served: list[Ag2Agent] = []

    def build(config, **kwargs):
        agent = Ag2Agent(name="acp-ws-appr", config=TestConfig("ok"))
        served.append(agent)
        return agent

    async with _running_server(paths, agent_factory=fake_agent_factory(agent=build)):
        assert served, "server never built the agent"
        # each entry is DescribedMiddleware(.middleware=Middleware(.cls=<class>))
        classes = [getattr(getattr(m, "middleware", m), "cls", None) for m in served[0].middleware]
        assert _OwnerApprovalMiddleware in classes


async def test_client_disconnect_closes_the_sessions_and_clears_live(paths, monkeypatch):
    """A client that simply quits must not leave its session (and the chat's live
    badge) alive forever: the SDK only clears its own registry on disconnect, so
    the guard aclose()s the connection's SessionStore when the socket closes."""
    from assistant.acp.chats import LIVE_SESSIONS

    ProfileRegistry(paths).create_profile("ws-drop", "#336699")
    monkeypatch.setattr("assistant.acp.auth.profile_has_credentials", lambda config, env: True)
    scripted = fake_agent_factory(
        agent=lambda config, **kwargs: Ag2Agent(name="acp-ws-drop", config=TestConfig("ok")),
    )
    token = "s3cret-listener-token"  # noqa: S105 - test fixture, not a real credential

    async with _running_server(paths, token=token, agent_factory=scripted) as port:
        remote = ACPRemoteConfig(
            url=f"ws://127.0.0.1:{port}/acp",
            headers={"Authorization": f"Bearer {token}"},
            expose_tools=False,
            permission_policy="deny",
        )
        client = Ag2Agent(name="drop-client", config=remote)
        reply = await client.ask("hi")
        assert "ok" in str(await reply.content()).lower()
        assert LIVE_SESSIONS.live_chat_ids(), "session should be live while connected"

        # the 'user quit the script' moment: reap the transport behind the config
        from assistant.structured import aclose_config

        await aclose_config(remote)

        # the server is still up — the guard's per-connection finally must clear it
        for _ in range(100):
            if not LIVE_SESSIONS.live_chat_ids():
                break
            await asyncio.sleep(0.05)
        assert not LIVE_SESSIONS.live_chat_ids(), "disconnect must clear the live registry"

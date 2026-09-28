"""The ACP stdio door: a real client end to end, cold start, stdout purity.

Protocol tests drive a genuine ``ag2.acp`` connection in-process via
``ag2.acp.testing.connect`` — real JSON-RPC framing and dispatch over a socket
pair, no subprocess, no real LLM (``ag2.testing.TestConfig``).
"""

import contextlib
import sys

import acp
import pytest
from acp import schema
from ag2 import Agent
from ag2.acp import ACPAgent
from ag2.acp.testing import connect
from ag2.testing import TestConfig

from assistant.acp import serve as serve_module
from assistant.acp.serve import cold_start_agent


async def test_prompt_streams_updates_and_final_text():
    agent = Agent(name="acp-test", config=TestConfig("42"))
    acp_agent = ACPAgent(agent, name="AG2 Assistant", version="0.0.0-test")

    async with connect(acp_agent) as (client, recorder):
        session = await client.new_session(cwd=".")
        response = await client.prompt(
            session_id=session.session_id,
            prompt=[acp.text_block("what is the answer")],
        )

    assert response.stop_reason == "end_turn"
    texts = [
        u.content.text
        for u in recorder.updates_for(session.session_id)
        if isinstance(u, schema.AgentMessageChunk)
    ]
    assert texts == ["42"]


async def test_cold_start_agent_completes_handshake_and_fails_the_turn_clearly():
    agent = cold_start_agent("no profile to use")
    acp_agent = ACPAgent(agent, name="AG2 Assistant", version="0.0.0-test")

    async with connect(acp_agent, initialize=False) as (client, _recorder):
        init = await client.initialize(protocol_version=acp.PROTOCOL_VERSION)
        assert init.agent_capabilities is not None

        session = await client.new_session(cwd=".")
        with pytest.raises(acp.RequestError) as exc_info:
            await client.prompt(session_id=session.session_id, prompt=[acp.text_block("hi")])

    assert "profiles create" in exc_info.value.data["reason"]


async def test_serve_stdio_wires_cold_start_agent_when_no_profile_exists(paths, monkeypatch):
    """The production ``serve_stdio`` entry, with the transport stubbed out — an
    isolated layout with no registered profile, so this exercises the same
    ``UnknownProfile`` branch a cold ``uvx`` install would hit."""
    captured: dict[str, ACPAgent] = {}

    async def fake_run_stdio_guarded(acp_agent: ACPAgent) -> None:
        captured["agent"] = acp_agent

    monkeypatch.setattr(serve_module, "_run_stdio_guarded", fake_run_stdio_guarded)

    await serve_module.serve_stdio(None, paths, env={})

    acp_agent = captured["agent"]
    async with connect(acp_agent, initialize=False) as (client, _recorder):
        init = await client.initialize(protocol_version=acp.PROTOCOL_VERSION)
        ids = {m.id for m in init.auth_methods or []}
        assert {"terminal", "env_var"} <= ids  # the registry gate reads exactly this line
        with pytest.raises(acp.RequestError):
            await client.new_session(cwd=".")  # unconfigured ⇒ auth_required (ADR-0035)


def test_stdout_guard_redirects_stray_prints_to_stderr(capsys):
    """The exact mechanism ``_run_stdio_guarded`` applies after the transport has
    captured the real fd 1: any stray ``print()`` from here on lands on stderr."""
    with contextlib.redirect_stdout(sys.stderr):
        print("stray output")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "stray output" in captured.err


async def test_real_acp_command_emits_nothing_but_jsonrpc_on_stdout(tmp_path):
    """fd 1 of the real ``acp`` command carries nothing but
    JSON-RPC frames — asserted on the raw subprocess pipe across a full
    initialize → session/new → session/prompt handshake (cold start: isolated
    data dir, so no profile and no LLM)."""
    import asyncio
    import json
    import os

    env = {**os.environ, "AG2ASSISTANT_DATA_DIR": str(tmp_path)}
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "from assistant.cli import app; app()",
        "acp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
    )
    assert proc.stdin is not None and proc.stdout is not None

    async def request(rid: int, method: str, params: dict) -> dict:
        frame = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        proc.stdin.write((json.dumps(frame) + "\n").encode())
        await proc.stdin.drain()
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
            assert line, "agent closed stdout before responding"
            msg = json.loads(line)  # a non-JSON line here IS the failure
            assert msg.get("jsonrpc") == "2.0"
            if msg.get("id") == rid:
                return msg

    try:
        init = await request(0, "initialize", {"protocolVersion": 1})
        assert "agentCapabilities" in init["result"]
        methods = json.dumps(init["result"].get("authMethods", []))
        assert "terminal" in methods and "env_var" in methods

        # cold start is gated (ADR-0035): session/new fails as a clean error frame
        new = await request(1, "session/new", {"cwd": ".", "mcpServers": []})
        assert "error" in new
    finally:
        proc.stdin.close()
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=10)


async def test_ag2_oss_client_drives_the_served_agent(tmp_path):
    """Interop: AG2 OSS's own ACP *client* (``ACPConfig``) spawns and drives the
    served agent. Cold start, so no LLM — the handshake and session must work and
    the turn must fail with the setup hint, through the real client stack."""
    import os

    from ag2 import Agent as Ag2Agent
    from ag2.acp import ACPConfig

    config = ACPConfig(
        command=[sys.executable, "-c", "from assistant.cli import app; app()", "acp"],
        env={
            "AG2ASSISTANT_DATA_DIR": str(tmp_path),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path),
        },
        expose_tools=False,
        permission_policy="deny",
        startup_timeout=60.0,
    )
    client_side = Ag2Agent(name="interop-cold", config=config)

    with pytest.raises(Exception) as exc_info:
        await client_side.ask("hello")
    # unconfigured install ⇒ the session itself is refused (auth_required, ADR-0035)
    assert (
        "auth" in str(exc_info.value).lower()
        or "auth" in str(getattr(exc_info.value, "data", "")).lower()
    )


@pytest.mark.integration
async def test_ag2_oss_client_gets_a_real_answer_over_ollama(tmp_path):
    """Warm interop over a local Ollama model: a real answer and a real follow-up
    (session continuity) through AG2 OSS's client. Requires Ollama serving
    ``qwen3.5:4b`` on localhost:11434 — skipped otherwise."""
    import os
    import urllib.request

    from ag2 import Agent as Ag2Agent
    from ag2.acp import ACPConfig

    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
    except OSError:
        pytest.skip("no local Ollama")

    from assistant.profiles import ProfileRegistry
    from tests.support.apps import make_paths

    ProfileRegistry(make_paths(tmp_path)).create_profile("interop", "#336699")

    config = ACPConfig(
        command=[sys.executable, "-c", "from assistant.cli import app; app()", "acp"],
        env={
            "AG2ASSISTANT_DATA_DIR": str(tmp_path / "state"),
            "AG2ASSISTANT_LLM_PROVIDER": "ollama",
            "AG2ASSISTANT_MODEL": "qwen3.5:4b",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path),
        },
        expose_tools=False,
        permission_policy="deny",
        startup_timeout=60.0,
        turn_timeout=180.0,
    )
    client_side = Ag2Agent(name="interop-warm", config=config)

    reply = await client_side.ask("Reply with exactly one word: pong")
    assert "pong" in str(await reply.content()).lower()
    followup = await reply.ask("Now reply with exactly one word: ping")
    assert "ping" in str(await followup.content()).lower()

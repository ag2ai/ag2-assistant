"""Distributed gateway spike — an AGClaw agent served over WebSocket via AG2's Hub.

This demonstrates the novel distributed capability AG2 0.13 unlocks: an AGClaw
agent registered on a Hub and reachable over a WebSocket from a *separate
process* (a thin "gateway" client), which could just as easily be on another
machine. Every message flows through the hub, so it's all audited and replayable.

Architecture:

    ┌─────────────── server process ───────────────┐
    │  Hub  ──(LocalLink)──  AGClaw Agent (tools)   │
    │   │                                            │
    │  serve_ws  ws://127.0.0.1:8765                 │
    └────┼───────────────────────────────────────────┘
         │  WebSocket (could be cross-machine)
    ┌────┼──────── client process ─────────────────┐
    │  WsLink → HubClient → HumanClient ("gateway") │
    └───────────────────────────────────────────────┘

Usage — start the server (hosts the agent), then send it a message:

    # Terminal 1
    python examples/network_gateway_spike.py server

    # Terminal 2
    python examples/network_gateway_spike.py client "Search the web: what is AG2?"
"""

import asyncio
import os
import sys

from autogen.beta.knowledge import MemoryKnowledgeStore
from autogen.beta.network import (
    EV_TEXT,
    Hub,
    HubClient,
    LocalLink,
    Passport,
    Resume,
)
from autogen.beta.network.transport import WsLink, serve_ws

from agclaw.agent import create_agent

HOST = "127.0.0.1"
PORT = 8765
URL = f"ws://{HOST}:{PORT}"
AGENT_NAME = "agclaw"


async def run_server() -> None:
    """Boot a Hub, register the AGClaw agent, and serve it over WebSocket."""
    hub = await Hub.open(MemoryKnowledgeStore(), ttl_sweep_interval=0)
    hub_client = HubClient(LocalLink(hub), hub=hub)

    # Register AGClaw as a network agent. Its default handler runs Agent.ask on
    # each inbound question — tools (search/shell/code) and all.
    await hub_client.register(
        create_agent(memory=False),
        Passport(name=AGENT_NAME, model="gemini-3.5-flash"),
        Resume(
            claimed_capabilities=["assistant", "web_search", "code"],
            summary="AGClaw personal assistant with web/search/code tools.",
        ),
    )

    async with serve_ws(hub, HOST, PORT):
        print(f"AGClaw agent '{AGENT_NAME}' serving at {URL}")
        print("Waiting for gateway clients. Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()  # run until interrupted
        finally:
            await hub_client.close()
            await hub.close()


async def run_client(question: str) -> None:
    """Connect over WebSocket as a gateway, ask the agent, print the reply."""
    hub_client = HubClient(WsLink(URL))  # no hub= → remote over WebSocket
    # Unique name per process so repeated client runs don't collide on the
    # long-lived hub registry.
    gateway = await hub_client.register_human(
        Passport(name=f"gateway-{os.getpid()}", kind="human")
    )

    # consulting = strict one-question-one-response; auto-closes after the reply.
    channel = await gateway.open(type="consulting", target=AGENT_NAME)
    await gateway.send(channel.channel_id, question)

    print(f"> {question}")
    reply = await gateway.next_envelope(
        predicate=lambda e: e.event_type == EV_TEXT
        and e.sender_id != gateway.agent_id,
        timeout=120.0,
    )
    print(f"\n{AGENT_NAME}: {reply.event_data['text']}")

    await hub_client.close()


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"server", "client"}:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "server":
        asyncio.run(run_server())
    else:
        question = sys.argv[2] if len(sys.argv) > 2 else "Say hello in one sentence."
        asyncio.run(run_client(question))


if __name__ == "__main__":
    main()

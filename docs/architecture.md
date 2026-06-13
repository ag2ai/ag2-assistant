# AGClaw Architecture

## Overview

AGClaw is a personal AI assistant platform built on AG2 Beta. All backend components are Python. UI is a separate concern — any client (web, desktop, mobile) connects via the gateway API.

## Implementation status (June 2026)

Built and tested:
- **Agent** on AG2 0.13.4 / Gemini, with **native AG2 tools** (`DuckDuckSearchTool`, `SandboxShellTool`, `SandboxCodeTool`) + a custom `web_fetch` fallback, selected per provider.
- **Observer memory** — passive user-profile learning persisted in SQLite via AG2's `KnowledgeStore` + `WorkingMemoryAggregate` + `WorkingMemoryPolicy`.
- **Gateway facade** — FastAPI REST + WebSocket (`/api/health`, `/api/message`, `/api/ws`) over a per-session conversation manager. Verified: multi-turn recall, session isolation, tool use over HTTP.
- **Distributed spike** — agent served over WebSocket via AG2 `Hub` + `serve_ws` (`examples/network_gateway_spike.py`).

Not yet built: channel adapters (Telegram/Discord/Slack), UI, skills/plugins.

### Gateway design note: direct-ask vs Hub

The single-agent UI facade uses **direct `AgentReply.ask()` chaining per session** — each session keeps its own isolated multi-turn history. This is simpler and lower-latency than routing each turn through an AG2 network channel, and it avoids a cross-session history-leak we hit when one shared agent served multiple conversation channels. The **AG2 Hub** is retained for what it's strongest at — **distributed transport and multi-agent** coordination (validated by the spike) — and the two compose: the facade's agent can later be placed on a Hub for cross-machine/multi-agent deployments without changing the client-facing API.

## System Architecture

![Architecture Diagram](architecture.svg)

## Layers

### 1. Client Layer

Any application that speaks REST + WebSocket:
- CLI (`agclaw` command)
- Web app (framework TBD)
- Desktop app (Electron, Tauri, etc.)
- Mobile app
- Other agents or services

Clients are never tightly coupled to the backend. The gateway API is the contract.

### 2. Channel Layer

Messaging platform adapters that normalize inbound/outbound messages:
- Each channel adapter implements a common `Channel` protocol
- Inbound: platform-specific message -> normalized `Message`
- Outbound: agent `Response` -> platform-specific format
- Supports: Telegram, Discord, Slack, WhatsApp, and more
- Handles platform-specific concerns: mention-gating, media types, threading, rate limits

### 3. Gateway Layer  *(built)*

A FastAPI facade (`agclaw.gateway`) that any UI client drives over a plain API:
- **REST API**: `GET /api/health`, `POST /api/message` ({text, session_id} → {reply})
- **WebSocket API**: `/api/ws` — send {text, session_id}, receive {type: thinking|reply|error}
- **Session manager** (`Gateway`): one isolated multi-turn conversation per `session_id`, via per-session `AgentReply.ask()` chains; calls within a session are serialised by a per-session lock.
- Launch with `agclaw gateway` (uvicorn).

For distributed/multi-agent deployments, the agent can additionally be served over WebSocket through an AG2 `Hub` (`serve_ws`) — see the distributed spike.

### 4. Agent Layer  *(built)*

AG2 Beta `Agent` (`agclaw.agent.create_agent`):
- **System Prompt**: the agent's personality (SOUL equivalent), from config.
- **Tools** (`agclaw.tools.build_agent_tools`): native AG2 `DuckDuckSearchTool`, `SandboxShellTool`, `SandboxCodeTool`, plus `WebFetchTool` (Anthropic) or a custom `web_fetch` function tool (Gemini & others). Provider-aware selection.
- **Knowledge + Assembly**: profile memory wired via `KnowledgeConfig` + `WorkingMemoryPolicy` (see below).
- **Middleware / Response Schema / HITL**: available from AG2 when needed (not all wired yet).

### 5. Session & Memory Layer  *(built)*

Two distinct concerns:

**Conversation history (per session)** — handled by AG2 `AgentReply.ask()` chaining inside the gateway. Each session's chain is isolated; no cross-session leakage.

**User-profile memory (global, passive)** — `agclaw.memory`:
- **Store**: `SqliteKnowledgeStore` at `~/.agclaw/profile.db` (a shared `LockedKnowledgeStore` when multiple agents must write it).
- **Learning**: `WorkingMemoryAggregate` with a custom 4-dimension prompt (how / when / dislikes / writing style), platform-tagged, fired `on_end` each turn.
- **Recall**: `WorkingMemoryPolicy` injects the profile into every turn.

See `docs/memory.md`.

### 6. Configuration Layer

Pydantic-based configuration:
- **LLM Config**: provider, model, API keys (wraps AG2's provider configs)
- **Agent Config**: name, system prompt, tools, middleware
- **Channel Config**: per-channel credentials and settings
- **Gateway Config**: host, port, auth settings

Loaded from config file + environment variables (.env).

## Message Flow

> The sections below (Message Flow, Concurrency Model, Concrete Message Flow Example, Data Model) describe the **target channel-based architecture** once channel adapters land. The **current** built path is simpler: UI client → gateway REST/WS → `Gateway.send_message()` → per-session `AgentReply.ask()` → reply. The channel-adapter, multi-platform, and session-store details are forward-looking design.

```
1. Message arrives (Telegram, Slack, CLI, Web UI, etc.)
       |
2. Channel Adapter normalizes to Message
       |
3. Gateway routes to Session (by user + channel identity)
       |
4. Session Store loads conversation history
       |
5. Context Assembly builds prompt (history + working memory)
       |
6. AG2 Agent.ask() processes with LLM
       |
7. Agent executes tools if needed (loop back to 6)
       |
8. Response returned
       |
9. Session Store persists new exchange
       |
10. Gateway routes response back
       |
11. Channel Adapter formats for platform
       |
12. UI clients receive event via WebSocket
```

## Concurrency Model

AGClaw must handle multiple users on multiple channels sending messages simultaneously.

### How It Works

```
User A (Telegram) ──┐
                    ├──> Gateway (asyncio event loop)
User B (Discord)  ──┤       |
                    │   Session Router
User A (Slack)    ──┘       |
                      ┌─────┴─────┐
                      ▼           ▼
                  Session A    Session B
                  (queue)      (queue)
                      |           |
                  Agent.ask()  Agent.ask()
                  (concurrent) (concurrent)
```

**Per-session message queue**: Messages for the same session are processed sequentially (FIFO). This prevents race conditions on session state — you can't have two LLM calls modifying the same conversation history simultaneously.

**Cross-session concurrency**: Different sessions run concurrently via asyncio. User A on Telegram and User B on Discord are processed in parallel — no blocking.

**Same user, different channels**: User A on Telegram and User A on Slack are separate sessions (separate history, separate queue). They can run concurrently with no contention.

**Same user, same channel, rapid messages**: Queued. If User A sends three messages on Telegram before the first response returns, they are processed in order. The second message sees the response to the first in its history.

### Gateway Internals

```python
# Simplified concurrency model
class Gateway:
    sessions: dict[str, Session]
    session_locks: dict[str, asyncio.Lock]  # One lock per session

    async def handle_message(self, message: Message):
        session_id = self.resolve_session(message)
        async with self.session_locks[session_id]:
            # Sequential within a session
            session = await self.load_session(session_id)
            response = await self.agent.ask(message.content, history=session.history)
            await self.save_session(session_id, message, response)
            await self.route_response(session_id, response)
```

The asyncio lock ensures ordering within a session. The `await` on `agent.ask()` yields control, allowing other sessions to proceed concurrently.

### What Happens Under Load

| Scenario | Behavior |
|---|---|
| 10 users, 10 channels, all active | 10 concurrent `Agent.ask()` calls (limited by LLM API rate limits) |
| 1 user sends 5 rapid messages | Queued, processed sequentially in order |
| Long-running tool (web search takes 10s) | Other sessions proceed; this session's queue waits |
| Agent uses multiple tools in one turn | Tools execute within the same `Agent.ask()` call — no queue impact |
| LLM provider rate limit hit | AG2 RetryMiddleware handles backoff; session waits, others proceed |

### Scaling Considerations

For a personal assistant (1-5 users), asyncio on a single process is sufficient. If AGClaw grows to serve many users:
- **Worker pool**: Multiple agent worker processes behind the gateway
- **Session affinity**: Route same session to same worker
- **External queue**: Redis or similar for cross-process session queues

This is future work — the single-process asyncio model is the right starting point.

## Concrete Message Flow Example

Here's a real scenario showing what actually happens at each step. User asks AGClaw on Telegram to find information and the agent uses tools.

### Scenario: "Find the latest AG2 release and summarize the changelog"

```
USER on Telegram                          AGCLAW
─────────────────                         ──────

1. User sends message in Telegram DM
   "@agclaw Find the latest AG2 release
    and summarize the changelog"
        |
        ▼
2. Telegram webhook fires
   python-telegram-bot receives update
        |
        ▼
3. TelegramAdapter.normalize()
   ┌─────────────────────────────────┐
   │ Message(                        │
   │   content: "Find the latest..." │
   │   sender_id: "tg:12345"        │
   │   channel_id: "tg:dm:12345"    │
   │   channel_type: "telegram"      │
   │   media: None                   │
   │   timestamp: 2026-04-13T10:30Z  │
   │ )                               │
   └─────────────────────────────────┘
        |
        ▼
4. Gateway.handle_message()
   Session key: "tg:12345:tg:dm:12345"
   Acquires session lock
        |
        ▼
5. SessionStore.load("sess_abc123")
   Returns conversation history:
   ┌─────────────────────────────────┐
   │ [                               │
   │   Exchange(                     │
   │     user: "What is AG2?",      │
   │     agent: "AG2 is an open-..." │
   │     tools_used: []              │
   │     timestamp: 2026-04-12T...   │
   │   ),                            │
   │   ... (14 more exchanges)       │
   │ ]                               │
   └─────────────────────────────────┘
        |
        ▼
6. Context Assembly
   Builds the full prompt:
   ┌─────────────────────────────────┐
   │ SYSTEM: "You are AGClaw, a      │
   │   helpful personal AI assistant │
   │   ..."                          │
   │                                 │
   │ [15 prior exchanges as context] │
   │                                 │
   │ USER: "Find the latest AG2      │
   │   release and summarize the     │
   │   changelog"                    │
   └─────────────────────────────────┘
        |
        ▼
7. Agent.ask() → LLM (Gemini)
   ┌─────────────────────────────────┐
   │ LLM Response:                   │
   │   "I'll look that up for you."  │
   │                                 │
   │   tool_call: web_search(        │
   │     query="AG2 latest release   │
   │       changelog site:github.com │
   │       /ag2ai/ag2"               │
   │   )                             │
   └─────────────────────────────────┘
        |
        ▼
8. Tool Execution: web_search
   WebSearchTool runs the search
   Returns results:
   ┌─────────────────────────────────┐
   │ ToolResult(                     │
   │   "AG2 v0.11.5 released        │
   │    2026-04-10. Release notes:   │
   │    https://github.com/ag2ai/    │
   │    ag2/releases/tag/v0.11.5"    │
   │ )                               │
   └─────────────────────────────────┘
        |
        ▼
9. Agent.ask() continues → LLM (Gemini)
   LLM sees the tool result, decides
   it needs more detail
   ┌─────────────────────────────────┐
   │ tool_call: web_fetch(           │
   │   url="https://github.com/     │
   │     ag2ai/ag2/releases/tag/    │
   │     v0.11.5"                    │
   │ )                               │
   └─────────────────────────────────┘
        |
        ▼
10. Tool Execution: web_fetch
    WebFetchTool fetches the page
    Returns changelog content
        |
        ▼
11. Agent.ask() continues → LLM (Gemini)
    LLM now has all the information
    ┌─────────────────────────────────┐
    │ Final Response:                 │
    │ "AG2 v0.11.5 was released on   │
    │  April 10, 2026. Key changes:  │
    │                                 │
    │  - Gemini config improvements  │
    │  - Bug fix in RetryMiddleware  │
    │  - New Shell tool options      │
    │  ..."                          │
    └─────────────────────────────────┘
        |
        ▼
12. Session Store persists
    New Exchange added to history:
    ┌─────────────────────────────────┐
    │ Exchange(                       │
    │   user: "Find the latest...",  │
    │   agent: "AG2 v0.11.5 was...", │
    │   tools_used: [                │
    │     "web_search", "web_fetch"  │
    │   ],                           │
    │   timestamp: 2026-04-13T10:30Z │
    │ )                              │
    └─────────────────────────────────┘
    Session lock released.
        |
        ▼
13. Gateway routes response
    - TelegramAdapter.format_outbound()
      Sends Markdown reply to Telegram DM
    - WebSocket broadcast to any connected
      UI clients (web dashboard, etc.)
        |
        ▼
14. User sees response in Telegram
    "AG2 v0.11.5 was released on..."
```

### What's Happening Concurrently

While steps 7-11 above are running (LLM calls + tool execution, maybe ~8 seconds total):

- **User B on Discord** sends "remind me to call Bob at 5pm" → their session processes independently, no waiting
- **User A on Slack** sends "what's the weather?" → different session, runs concurrently
- **Web UI client** connected via WebSocket receives streaming events: `agent_thinking`, `tool_call:web_search`, `tool_result`, `tool_call:web_fetch`, `tool_result`, `agent_response`

### Error Scenarios

| What goes wrong | What happens |
|---|---|
| LLM API returns 429 (rate limit) | RetryMiddleware backs off and retries. Session queue waits. Other sessions proceed. |
| Tool execution fails (web_fetch timeout) | ToolErrorEvent returned to LLM. LLM decides: retry, use partial info, or tell user. |
| Session store write fails | Response still delivered to user. Persisted on next successful write (write-ahead log). |
| Channel adapter can't deliver (Telegram down) | Response queued for retry. WebSocket clients still receive it. |
| User sends another message while agent is thinking | Queued behind the current processing. Processed after current response completes. |

## Data Model

### Message (inbound)

```python
@dataclass
class Message:
    content: str                    # Text content
    sender_id: str                  # User identifier on the platform
    channel_id: str                 # Channel identifier
    channel_type: str               # "telegram", "discord", "slack", etc.
    media: list[Media] | None       # Images, files, voice, etc.
    metadata: dict                  # Platform-specific extras
    timestamp: datetime
```

### Response (outbound)

```python
@dataclass
class Response:
    content: str                    # Text content
    media: list[Media] | None       # Generated images, files, etc.
    metadata: dict                  # Platform-specific formatting hints
```

### Session

```python
@dataclass
class Session:
    session_id: str
    user_id: str
    channel_type: str
    channel_id: str
    history: list[Exchange]         # Conversation turns
    working_memory: dict            # Persistent key-value state
    created_at: datetime
    updated_at: datetime
```

## Key Design Principles

1. **UI-agnostic**: Gateway API is the only interface. Any client can connect.
2. **Channel-agnostic agent**: The agent never knows which platform a message came from.
3. **Stateless agent, stateful sessions**: AG2 Agent is stateless per call. Session layer manages persistence.
4. **AG2-compatible interfaces**: Custom implementations designed to align with AG2's unreleased APIs.
5. **Progressive enhancement**: Start simple (CLI + single LLM), add channels and features incrementally.

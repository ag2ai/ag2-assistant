# AGClaw Architecture

## Overview

AGClaw is a personal AI assistant platform built on AG2 Beta. All backend components are Python. UI is a separate concern — any client (web, desktop, mobile) connects via the gateway API.

## Implementation status (June 2026)

Built and tested:
- **Agent** on AG2 0.13.4, multi-provider with **real provider switching** (Gemini default; OpenAI/Anthropic via config), **native AG2 tools** (`DuckDuckSearchTool`, `SandboxShellTool`, `SandboxCodeTool`) + a custom `web_fetch` fallback, a permission-gated `read_file` (vision for PDFs/images), and always-on **behaviour guidance** (use the right tool; never shell-flail; admit when there's no tool).
- **Execution sandbox** — `local` (host, command-filtered + approval-gated) or **`docker`** (isolated container, no host FS; approval dropped since the container is the boundary). Skill scripts run in a one-shot bind-mounted container under Docker.
- **Skills** — `SkillSearchToolkit` (search/install/run from skills.sh) **plus bundled first-party skills** (`web-research`, `pdf-tools`, `email-drafting`) available on first run.
- **Google integration** — Gmail/Calendar/Drive tools behind OAuth (`agclaw[google]`); reads run freely, **sends/writes are HITL-gated**. Sign in via CLI or the web UI's Google panel.
- **Observer memory** — passive user-profile learning in SQLite (`WorkingMemoryAggregate` + `WorkingMemoryPolicy`), platform-tagged, cadence-batched (`every_n_turns`, cheaper aggregation model). Permission decisions excluded.
- **Environment context** — live date/time + location, injected per turn.
- **Gateway facade** — FastAPI REST + WebSocket with **resumable per-session conversations** (each session is a persistent AG2 `Stream` whose events are written via `EventLogWriter` and reloaded on demand), gateway-hosted **HITL** and **Google OAuth**, and a built-in **web UI**.
- **Web UI** — self-contained chat client served at `/` (markdown rendering, file attachments, stop button, system light/dark, inline HITL cards, History drawer; ag2.ai-styled). Verified live with Chrome DevTools.
- **Channels** — Telegram, Discord, Slack live (DM + @mention gating, per-channel formatting, in-chat permission buttons, **attachments**, resumable history). Combined `agclaw run` serves the gateway + all configured channels in one process.
- **HITL & turn-level permissions** — pluggable `Asker` (chat buttons / styled desktop or gateway page), single per-turn `PermissionManager` gating folder access and shell/code.
- **Onboarding** — first-run interview (name/location/hours/style) via the same `Asker`.
- **Tasks (in progress)** — persistent, nestable task primitive with objectives + deliverables-based completion and live amendment (foundation built; runner/scheduler/GUI in progress). See `docs/tasks-design.md`.
- **Distributed spike** — agent served over WebSocket via AG2 `Hub` + `serve_ws`.

Not yet built: task runner/scheduler/GUI view (in progress), WhatsApp channel.

### Gateway design note: per-session streams vs Hub

The single-agent UI facade gives each session its own **persistent AG2 `Stream`** keyed by `session_id`; the conversation lives in the stream's event history, persisted after each turn with `EventLogWriter` and reloaded into a fresh stream on demand — so conversations are **resumable** across restarts (web and all channels), and sessions never cross histories. (This replaced the earlier per-session `AgentReply.ask()` chaining, which wasn't durable.) The **AG2 Hub** is retained for **distributed transport and multi-agent** coordination, and composes: the facade's agent can later sit on a Hub without changing the client API.

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

A FastAPI facade (`agclaw.gateway`) that any UI client drives over a plain API, and which also serves the built-in web UI:
- **Web UI**: `GET /` — the self-contained chat client; favicons at `/favicon*.svg`.
- **REST**: `GET /api/health`, `POST /api/message`, `GET /api/sessions` + `GET /api/sessions/{id}` (resumable history), `GET /api/hitl/pending` + `POST /hitl/{id}/answer`, `GET/POST /api/google/*` (status, login_url, callback, credentials, logout).
- **WebSocket**: `/api/ws` — send `{text, session_id, attachments?}`; receive `{type: thinking|reply|question|cancelled|error}`; answer HITL with `{type:"answer", id, answer}`; stop a turn with `{type:"cancel"}`.
- **Session manager** (`Gateway`): a persistent AG2 `Stream` per `session_id`, hydrated from disk via `EventLogWriter`, with a per-task display transcript; calls within a session are serialised by a per-session lock; turns time out cleanly with an error frame.
- Launch with `agclaw gateway`, or `agclaw run` to also start every configured channel in one process.

For distributed/multi-agent deployments, the agent can additionally be served over WebSocket through an AG2 `Hub` (`serve_ws`).

### 4. Agent Layer  *(built)*

AG2 Beta `Agent` (`agclaw.agent.create_agent`):
- **Prompt**: persona (from config) + always-on **behaviour guidance** + live environment + (when signed in) Google tool-usage guidance, assembled per turn in `turn_prompt`.
- **Model**: `model_config()` builds the right provider config (Gemini/OpenAI/Anthropic) from `config.llm`; aggregation can use a cheaper model.
- **Tools** (`agclaw.tools.build_agent_tools`): `DuckDuckSearchTool`, `SandboxShellTool` + `SandboxCodeTool` (local **or Docker** backend), `read_file` (vision, permission-gated), `web_fetch` (native on Anthropic, custom elsewhere), the skills toolkit (registry + bundled), and — when signed in — the **Google tools** (`gmail_*`, `calendar_*`, `drive_*`; sends/writes HITL-gated).
- **Knowledge + Assembly**: profile memory via `KnowledgeConfig` + `WorkingMemoryPolicy`.
- **Sandbox**: `config.tools.sandbox` = `local` (approval-gated) or `docker` (container-isolated; approval dropped).

### 5. Session & Memory Layer  *(built)*

Two distinct concerns:

**Conversation history (per session)** — each session is a persistent AG2 `Stream` (id = `session_id`); events are written to `~/.agclaw/sessions.db` via `EventLogWriter` after each turn and reloaded on demand, so conversations **resume** across restarts with full context (web + all channels). A lightweight per-session display transcript backs the UI History view. Sessions never cross histories.

**User-profile memory (global, passive)** — `agclaw.memory`:
- **Store**: `SqliteKnowledgeStore` at `~/.agclaw/profile.db`.
- **Learning**: `WorkingMemoryAggregate` with a custom 4-dimension prompt (how / when / dislikes / writing style), platform-tagged, batched `every_n_turns` (cheaper aggregation model), `on_end` only for single-shot CLI. The prompt forbids commentary and never records permission decisions.
- **Recall**: `WorkingMemoryPolicy` injects the profile into every turn.

See `docs/memory.md`.

### 6. HITL & Permissions Layer  *(built)*

Human-in-the-loop and Claude-Code-style permissions, routed to whatever surface made the request.

**Asking (`agclaw.hitl`)** — a pluggable `Asker` seam:
- `Asker.ask(Question)` returns the human's answer (a chosen option, or free text).
- Per-surface implementations: chat buttons (Telegram inline-keyboard, Discord `ui.View`, Slack Block Kit) and a styled **desktop browser page** (`HitlServer` serving concurrent `/hitl/{id}` pages in the ag2.ai look).
- Wired into the agent two ways: AG2's `hitl_hook` (for `context.input()` open questions) and the permission manager (for approvals).
- `PendingAsks` correlates a question with its answer per chat; Telegram needs `concurrent_updates(True)` so a tap resolves while the turn is blocked.

**Permissions (`agclaw.permissions`)** — one **turn-level `PermissionManager`** is the single authority for *all* access:
- Created once per turn (per `send_message`) and injected via `dependencies`; shared by `read_file` (folder access, `check`) and shell/code (command approval, `check_command`).
- Options: **Allow once / Always allow / Deny**. "Always allow" for a folder persists to `~/.agclaw/permissions.json` (survives turns); turn decisions reset next turn.
- Shared turn stance: once the user denies anything, further access prompts auto-deny for that turn (no spam, no tool-escalation); denial results tell the model not to retry.
- Closes the bypass where shell/code could read files outside the `read_file` gate (`require_command_approval` middleware on `SandboxShellTool`/`SandboxCodeTool`).
- Permission decisions are kept **out of** the observer memory (they're operational, not preferences).

### 7. Configuration Layer

Pydantic config resolved by `load_config()` with precedence **env (`AGCLAW_*`) > `~/.agclaw/config.json` > defaults**:
- **LLM**: `provider` (gemini/openai/anthropic), `model`, `api_key_env`, optional cheaper `aggregate_model`.
- **Agent**: name, system prompt, location.
- **Tools**: `sandbox` (local/docker), `docker_image`, `docker_network`.
- **Memory**: `aggregate_every_n_turns`.
- **Channels/Google**: credentials/tokens in `.env` / `~/.agclaw/`.

### 8. Tasks Layer  *(in progress)*

Persistent, user-facing task management (`agclaw.tasks`) — see `docs/tasks-design.md`:
- **`Task`** primitive: nestable (`parent_id` tree), with status, start/end + scheduling fields, **objective**, **deliverables** (acceptance criteria + status + linked asset), progress log, plan, intake Q&A, assets, origin/HITL-routing, per-task event stream.
- **`TaskStore`** (`~/.agclaw/tasks.db`): CRUD, tree/descendants/cascade, **gated completion** (`is_complete` = all deliverables satisfied AND all subtasks done), and **live amendment** (`update`/`reopen`/`add_subtask` — adding work re-opens/uncompletes a task so the runner picks it up).
- **Reuses** AG2's `Task` lifecycle/`TaskProgress`/`run_subtasks`/`EventLogWriter`; AGClaw builds the durable store+tree, the background runner with immediate cascade-cancel, HITL intake, scheduler, gateway API, and GUI view (in progress).

### 9. Google Integration Layer  *(built)*

`agclaw.integrations.google_auth` (OAuth, token cache + refresh) + `agclaw.tools.google`:
- Desktop OAuth flow via CLI (`agclaw google login`) or the gateway (`/api/google/login_url` → `/api/google/callback`, drivable from the web UI's Google panel; `AGCLAW_PUBLIC_URL` for off-machine redirects).
- Tools auto-attach when signed in: Gmail/Calendar/Drive read freely; `gmail_send` and `calendar_create_event` carry the approval middleware (HITL-gated).

### Storage (`~/.agclaw/`)

`profile.db` (learned profile) · `sessions.db` (resumable conversations + transcripts) · `tasks.db` (tasks) · `permissions.json` (folder grants/blocks) · `google_credentials.json` + `google_token.json` + `google_account.txt` (Google) · `onboarded` (first-run marker) · optional `config.json` · `skills/` (installed skills).

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

# AG2 Assistant Architecture

## Overview

AG2 Assistant is a personal AI-assistant platform built on **AG2 Beta**. The backend is
Python; the web UI is a separate Svelte client that talks to the gateway's API.

The defining decision (see `docs/gui-redesign-plan.md`, and the memory
`ag2-primitives-drive-architecture`): **AG2's event `Stream` is the single source
of truth, and everything is a projection of it.** We lean on AG2 primitives
(streams, the event taxonomy, observers/subscriptions, `EventLogWriter`,
knowledge/compaction) rather than inventing parallel machinery, and add an
AG2 Assistant-specific layer only where AG2 main genuinely doesn't reach yet (durable
scheduled tasks, durable HITL).

## The spine: AG2 event streams

Each conversation surface — a web chat, each task, a voice session — is a
per-session AG2 **`Stream`** keyed by `session_id` (`web-…`, `task:<id>`,
`voice:<id>`). Its event history *is* the conversation; after each turn the
events are persisted with `EventLogWriter` to `~/.ag2assistant/sessions.db` and
reloaded on demand, so sessions are **resumable** and never cross histories.

**One wire contract — `wire = log = {type, data}`.** A serialized event is
exactly what `EventLogWriter` persists:

```json
{ "type": "autogen.beta.events.types.ModelResponse", "data": { … } }
{ "type": "ag2assistant.events.DeliverableProduced", "data": { … } }
```

The same representation is used to **persist, replay, and live-stream**. So
*history (replay the log) and live (subscribe to new events) are the same path*,
and the GUI is a thin renderer that maps event `type` → component. Custom
`ag2assistant.events.*` round-trip because deserialization resolves the class by its
fully-qualified name.

**Event taxonomy** (reused AG2-native unless noted):

| Event | Rendered as |
|---|---|
| `ModelRequest` | user message bubble |
| `ModelMessageChunk` / `ModelResponse` | agent bubble (streams, then finalizes) |
| `ToolCallsEvent` / `ToolResultEvent` | ⚙ tool chip / internal |
| `TranscriptionChunkEvent` / `…Completed` | voice user transcript |
| `SynthesizedAudioEvent` | binary audio (own frame, not an item) |
| `HumanInputRequest` | in-stream HITL (durable copy in `InquiryStore`) |
| `TaskStarted/Progress/Completed/Failed/Cancelled` | task lifecycle |
| `ObserverAlert` / `HaltEvent` | alerts / halts |
| `ag2assistant.events.TaskCreated` | task card (chat → spawned task) |
| `ag2assistant.events.TaskScheduled` | schedule note |
| `ag2assistant.events.DeliverableProduced` | deliverable item |
| `ag2assistant.events.InquiryRaised` / `InquiryAnswered` | durable HITL lifecycle |

The custom five exist only where AG2 main has no equivalent.

## System Architecture

![Architecture Diagram](architecture.svg)

## Layers

### 1. Client

The web UI is a **Vite + Svelte 5** SPA in `web/`, built (committed) into
`src/ag2assistant/gateway/static/app/` and served at **`/app`** (`/` and any unknown
path 307-redirect there). It's a pure projection of the event stream:
`transport/stream.js` opens one `/api/stream` WebSocket; `project.js` folds
`{type,data}` events into thread items; per-event-type Svelte components are
reused across chat and task threads. `TaskPanel` reads the durable task tree via
REST; the HITL strip polls `/api/inquiries/pending`; voice, attachments and
Google-connect are first-class. Routing is full-path (`/app/c/<id>`,
`/app/t/<id>`). `web/diag.mjs` is a headless jsdom smoke test that executes the
built bundle to catch mount errors/loops without a browser.

Any other client (CLI, channels, future apps) is equally valid — the gateway API
is the only contract.

### 2. Gateway

A FastAPI facade (`ag2assistant.gateway`) over the agent and task engine:

| Method | Path | Purpose |
|---|---|---|
| WS | `/api/stream?session=<id>` | **Primary transport.** Replays the session's events `{event:{type,data}}` then streams live; send `{text, attachments?}` / `{type:"answer",…}` |
| WS | `/api/voice?session=\|task=` | Full-duplex Gemini Live: binary PCM in/out + transcript/tool/task_card JSON |
| GET | `/api/sessions`, `/api/sessions/{id}` | resumable session list + transcript |
| GET/POST | `/api/tasks*` | task list/create/schedule/detail/cancel/archive/chat |
| GET/POST | `/api/inquiries/pending`, `/api/inquiries/{id}/answer` | durable HITL |
| * | `/api/google/*`, `/hitl/*`, `/api/health` | Google OAuth, transient HITL pages, health |
| GET | `/app`, `/app/{path}` | the Svelte SPA (deep-link fallback) |

`StreamBridge` (the generalized form of our subscriptions, à la the network
`TaskMirror`) replays + forwards a session's events and runs input turns through
`Gateway.send_message`. `Gateway.emit_event(session, event)` appends an event
onto a stream *outside* a turn (the `SoundDeviceRecorder` pattern) and persists
it — used by the task engine and voice transcripts. A per-session lock serialises
turns; failures are snapshotted (see Observability). Launch with `ag2assistant gateway`,
or `ag2assistant run` to also start every configured channel.

### 3. Agent

One **universal `Agent`** (`ag2assistant.agent.create_agent`) backs every surface;
isolation comes from the per-session stream. Per turn, `universal_turn_prompt`
assembles persona + behaviour guidance + capability map + (when signed in) Google
guidance + the **surface context** (e.g. a task snapshot) + live environment
(date/time/location). Tools: web search, sandboxed shell/code (local or Docker),
`read_file` (vision, permission-gated), `web_fetch`, the skills toolkit, Google
tools when signed in, and the **system tools** (`build_system_tools`) that let the
one agent *know and do everything* — list/get/create/schedule/edit/cancel/archive
tasks, answer questions, read chats. Knowledge/compaction via `KnowledgeConfig`;
`LoggingMiddleware` for per-turn logs. A cheaper model (`gemini-3.1-flash-lite`)
runs bulk work (memory aggregation, deliverable verification, leaf subtasks).

### 4. Tasks — a durable engine that is an *event source*

`ag2assistant.tasks` (see `docs/tasks-design.md`) is the one place AG2 main doesn't
cover: a persistent, nestable **`Task`** (`TaskStore`, `~/.ag2assistant/tasks.db`) with
objective, deliverables (criteria + verification + asset), capability scope,
iterative HITL intake, a resilient concurrent runner (`TaskManager`), and a
deterministic no-LLM **scheduler** (one-shot + recurring; recurring tasks stay a
template that clones a run per occurrence).

Crucially, the engine **emits events onto the relevant stream** so the GUI renders
tasks like everything else: `TaskManager` has `on_status`/`on_deliverable` hooks →
`TaskService` translates to AG2 `TaskStarted/Completed/Failed/Cancelled` +
`DeliverableProduced` on `task:<id>`; `create_task`/`schedule_task` emit
`TaskCreated`/`TaskScheduled` (on the chat stream via `Context.send`, for the card).
The durable store remains system-of-record; it speaks the AG2 event vocabulary.
(Migration target when AG2 ships it: A2A `TaskArtifact`/Hub.)

### 5. Voice

`ag2assistant.voice` runs an AG2 **`LiveAgent`** (Gemini Live) per browser voice
session. It has a small basic toolset (read tasks, answer questions) and an
`ask_assistant` tool that **delegates heavy work to the universal agent** on the
same session (continuity). The browser captures 16 kHz PCM via an AudioWorklet
over `/api/voice`; 24 kHz speech streams back; transcripts and tool chips render
as bubbles. Spoken turns are **persisted onto the session stream** (user →
`ModelRequest`, agent → `ModelResponse`) so voice and text share one resumable
conversation.

### 6. HITL & permissions

`ag2assistant.hitl`: a pluggable **`Asker`** (chat buttons / styled desktop pages) wired
via AG2's `hitl_hook` and the permission manager. Inside tasks, prompts are
**durable `Inquiry` primitives** (`InquiryStore`, `~/.ag2assistant/inquiries.db`):
persisted the moment they're raised, answerable from any surface; `DurableAsker`
races live delivery against an out-of-band answer. An `on_change` hook emits
`InquiryRaised`/`InquiryAnswered` onto the task stream. One turn-level
`PermissionManager` (`ag2assistant.permissions`) gates folder access and shell/code
(Allow once / Always / Deny; "always" persists to `permissions.json`; sandbox-mode
aware).

### 7. Observability

`ag2assistant.observability` — file-based so it's readable back without reproducing:
a rolling `~/.ag2assistant/ag2assistant.log` (folds in AG2's `autogen.*` logs + per-turn
`LoggingMiddleware`), and a **failure snapshot** written on any turn exception
(`<data_dir>/debug/<ts>-<session>.json`) capturing the error, traceback, and the
*shape* of the history that triggered it (event-type counts + tail). The full
per-turn event stream (`EventLogWriter`) is the deep record. (OpenTelemetry
`TelemetryMiddleware` is available but not wired — no `opentelemetry` dependency.)

### 8. Memory, Google, Config, Storage

- **Memory** (`ag2assistant.memory`): passive user-profile learning
  (`WorkingMemoryAggregate` + `WorkingMemoryPolicy`, SQLite, platform-tagged,
  cadence-batched on a cheap model; permission decisions excluded). See `docs/memory.md`.
- **Google** (`ag2assistant.integrations.google_auth` + `ag2assistant.tools.google`): OAuth;
  Gmail/Calendar/Drive read freely, sends/writes HITL-gated.
- **Config**: Pydantic, precedence env (`AG2ASSISTANT_*`) > `~/.ag2assistant/config.json` > defaults.
- **Storage** `~/.ag2assistant/`: `sessions.db` (streams + transcripts) · `tasks.db` ·
  `inquiries.db` · `profile.db` · `permissions.json` · Google creds/token ·
  `ag2assistant.log` + `debug/` · `skills/` · optional `config.json`.

## Turn flow (current)

```
client →  WS /api/stream {text}         (or REST /api/message, a channel, voice)
       →  StreamBridge.run_turn → Gateway.send_message(session_id)
       →  load+sanitize stream history → universal Agent.ask(stream=…, prompt=surface+env)
       →  agent emits events on the stream: ModelRequest → ToolCalls/Results → ModelResponse
       →  StreamBridge subscription forwards each {type,data} to the client live
       →  client folds events into thread items (user/agent bubbles, ⚙ chips, cards)
       →  turn persisted via EventLogWriter; on failure → debug snapshot
```

Background tasks run independently in `TaskManager`, emitting lifecycle/deliverable
events onto `task:<id>`; voice runs a `LiveAgent` whose delegated work and spoken
transcripts also land on the session stream. History and live use one path, so a
reload replays exactly what was streamed.

## Concurrency model

Single-process asyncio. A per-session `asyncio.Lock` serialises turns *within* a
session (no two LLM calls mutating one history); different sessions run
concurrently. Same user on two channels = two sessions. The task runner is
concurrency-capped with immediate cascading cancel. For a personal assistant
(1–5 users) this is sufficient; scaling out (worker pool, session affinity,
external queue, or AG2 `Hub` distributed transport) is future work — the agent
composes onto a Hub without changing the client API.

## Key design principles

1. **AG2 primitives are the spine** — streams/events/observers/persistence;
   don't build parallel architecture (`ag2-primitives-drive-architecture`).
2. **The GUI is a projection of the event stream** — `wire = log = {type,data}`;
   history and live are one path.
3. **UI-agnostic** — the gateway API is the only contract; the Svelte app is one client.
4. **One universal agent, many resumable per-session streams** — isolation via streams.
5. **Build on main; migrate to native as it lands** — the durable task/inquiry
   layer is the seam, kept thin and speaking AG2's event vocabulary
   (`ag2-build-on-main`).
6. **Diagnosable by default** — file-based logs + failure snapshots over the event log.

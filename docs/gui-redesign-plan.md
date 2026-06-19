# GUI Redesign — AG2-stream-driven, Vite + Svelte

**Principle (see memory `ag2-primitives-drive-architecture`):** the AG2 event `Stream`
is the single source of truth. The GUI is a *projection* of it; the gateway is a thin
*bridge*. We add the minimum AG2 doesn't provide and migrate that to native as AG2
ships it (`ag2-build-on-main`).

## The one contract everything renders off: wire = log = `{type, data}`

A serialized event is exactly what `EventLogWriter` already writes:

```json
{ "type": "autogen.beta.events.task_events.TaskCompleted", "data": { ... } }
{ "type": "ag2assistant.events.DeliverableProduced", "data": { ... } }
```

- Persist, replay, and live-stream all use this shape.
- Load resolves the class by dynamic import (`import_event_class`), falling back to
  `UnknownEvent` — **verified that custom `ag2assistant.events.*` round-trip.**
- History (replay persisted events) and live (subscribed new events) are the SAME path.
- Transient events (`__transient__`: `ModelMessageChunk`, `TaskProgress`) stream live
  but aren't persisted; their finals (`ModelResponse`, `TaskCompleted`) are what reload shows.

## Event taxonomy (verified present in installed AG2 0.13.4 unless noted)

**Reuse AG2 native:**
| Event | Renders as |
|---|---|
| `ModelMessageChunk` (transient) / `ModelResponse` | agent bubble (streams, then finalizes) |
| `ToolCallEvent` / `ToolCallsEvent` | ⚙ tool chip (dedupe by id) |
| `ToolResultEvent` | internal; `create_task` result is superseded by `TaskCreated` below |
| `TranscriptionChunkEvent` / `TranscriptionCompletedEvent` | voice user transcript |
| `SynthesizedAudioEvent` | binary audio (separate WS frame, not a rendered item) |
| `HumanInputRequest` (`id`, `content`) | HITL inline (backed by durable InquiryStore) |
| `TaskStarted/TaskProgress/TaskCompleted/TaskFailed/TaskCancelled/TaskExpired` | task lifecycle (`TaskEvent`: task_id, agent_name, objective) |
| `ObserverAlert` / `HaltEvent` | alert / "stopped" notice |

No `RunStarted` in main → "thinking" is a client derivation (message sent, no chunk yet).

**New `src/ag2assistant/events.py` (custom `BaseEvent` subclasses — the only new "model"):**
| Event | Fields | Renders as |
|---|---|---|
| `TaskCreated` | `task_id, title, kind` | task card (replaces the `started_tasks_var` hack) |
| `TaskScheduled` | `task_id, scheduled_for, recurrence` | schedule note |
| `DeliverableProduced` | `task_id, deliverable_id, description, preview` | deliverable item (+ "open full") |
| `InquiryRaised` | `inquiry_id, task_id, question, options, kind` | durable HITL (mirrors `HumanInputRequest`) |
| `InquiryAnswered` | `inquiry_id, answer` | resolves the HITL item |

## Phases (each shippable; gateway REST/task engine otherwise untouched)

### Phase 0 — Event module + wire helper (no behavior change)
- `src/ag2assistant/events.py`: the custom events above.
- `src/ag2assistant/gateway/wire.py`: `to_wire(event) -> {type,data}` (reuse `qualified_name` +
  `to_dict`); `WIRE_BINARY` set for audio. One serializer for WS + logs.
- Tests: `test_events.py` round-trips every custom event through serialize→import→from_dict.
- **Risk: none** (additive). 282+ tests stay green.

### Phase 1 — Event bridge over WS (backend)
- `StreamBridge` (in `gateway/stream_bridge.py`): given a session stream, (1) replay
  persisted events on connect, (2) `stream.subscribe` → forward each `to_wire(event)`.
  This is the generalized `TaskMirror`/our existing tool-chip+voice subscription.
- New path **`/api/stream`** (leave `/api/ws` intact during migration): client sends
  `{text, attachments?}`; server runs `send_message` (appends to the stream); bridge
  forwards all events. Voice `/api/voice` aligned to the same `{type,data}` JSON shape
  (audio stays binary).
- Tests: connect → send → assert `ModelResponse`/`ToolCallEvent` frames; reconnect replays.
- **Risk: low** — old WS untouched; new path additive.

### Phase 2 — TaskStore/executor as event source
- Emit onto the task's stream (`task:<id>`): `TaskCreated` (also onto the chat stream that
  spawned it → card), `TaskStarted`, `TaskProgress`, `DeliverableProduced`,
  `TaskScheduled`, terminal `TaskCompleted/Failed/Cancelled`.
- HITL: emit `InquiryRaised`/`InquiryAnswered` alongside the durable `InquiryStore`.
- Remove the `started_tasks_var` contextvar hack once cards come from `TaskCreated`.
- Unify a task's execution + chat onto one `task:<id>` stream so the task thread shows
  everything (execution progress *and* the conversation).
- Tests: creating/running/scheduling a task appends the expected events; replay = full history.
- **Risk: medium** — touches executor/store emission points; covered by event-assertion tests.

### Phase 3 — Vite + Svelte client (built beside `index.html`)
- `web/` (Vite + Svelte 5), builds to `src/ag2assistant/gateway/static/app/`. **Built assets are
  committed** so deploy stays Python-only (Node needed only for dev/build). Dev:
  `npm run dev` proxying `/api` to the gateway. Served at `/app` until cutover.
- Structure:
  ```
  web/src/
    main.js            bootstrap + router (/c/<id>, /t/<id>, /tasks[/<f>])
    store.js           current thread, threads list, ephemeral UI (Svelte stores)
    transport/stream.js  connect /api/stream; dispatch {type,data} → store
    transport/voice.js   /api/voice (WebAudio) → same store
    transport/api.js     REST: tasks tree/schedule/deliverables, sessions list
    lib/markdown.js
    components/
      Thread.svelte      a stream projection (list of Items) + Composer
      Item.svelte        dispatch event.type → component
      Composer.svelte    text + attach + mic
      Drawer.svelte      unified history (chats + tasks)
      items/  AgentMessage UserMessage ToolChips Thinking TaskCard
              Deliverable VoiceTranscript InquiryCard Alert Progress
      task/   TaskPanel.svelte  (tree/schedule/deliverables/actions via REST)
  ```
- Single Chat/Task pane: a Thread renders Items; if `kind==task`, also shows collapsible
  `TaskPanel` above the items — same pane, same composer. Heavy reuse: identical Item
  components in chat and task threads.
- **Risk: low to existing app** — new app is parallel until Phase 4.

### Phase 4 — Cutover
- Point `/` at the Svelte build; move legacy to `/legacy` for one release, then delete
  `index.html` and the old `/api/ws` bespoke-frame code.
- Update `docs/architecture.md` + `.svg`.
- **Rollback:** `/legacy` + old `/api/ws` remain until we're confident.

## What explicitly does NOT change
- The Python task engine, scheduler, planner, executor logic (only *adds* event emission).
- REST endpoints for tasks/sessions/inquiries/google (TaskPanel + history use them).
- The durable InquiryStore and TaskStore as systems-of-record (AG2 main has no durable
  task store; migrate toward A2A/Hub task artifacts when they land on main).

## Not now (revisit later, on AG2)
- Network `task_observation` (capability reputation, not jobs) — only if we route work
  across multiple agents by skill.
- A2A `TaskSnapshot/StatusUpdate/ArtifactUpdate` (feature branch) — the eventual native
  home for durable task + deliverable artifacts; migrate `TaskStore` toward it.

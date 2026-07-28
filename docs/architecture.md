# AG2 Assistant — Architecture

A thorough map of the system: its services, agents, endpoints, event model, data
flow, and on-disk state. Companion diagram: [`architecture.svg`](architecture.svg).

> Conventions: file paths are repo-relative (e.g. `src/assistant/gateway/core.py`).
> Runtime state lives under `~/.ag2assistant/`; generated files under the workspace
> (`~/Documents/AG2 Assistant/` by default).

---

## 1. Overview

AG2 Assistant is a personal AI-assistant platform built on **AG2**
(`ag2`). The backend is Python (FastAPI); the primary client is a Svelte
web app served by the gateway. The same backend also speaks to messaging channels
(Telegram/Discord/Slack), a realtime voice client, and a CLI — all sharing **one
universal agent** and **one event-stream spine**.

The defining decision: **AG2's event `Stream` is the single source of truth,
and every surface is a projection of it.** We lean on AG2 primitives — streams, the
event taxonomy, observers/subscriptions, `EventLogWriter`, knowledge/compaction —
rather than inventing parallel machinery, and add an app-specific layer only where
AG2 main genuinely doesn't reach yet (durable scheduled tasks, durable HITL).

---

## 2. Guiding principle: the event stream is the source of truth

Each conversation surface — a web chat, each task, a voice session — is a
per-chat AG2 **`Stream`** keyed by `chat_id`:

| Chat id             | Surface                                    |
| ------------------- | ------------------------------------------- |
| `web-<uuid>`        | a web chat                                  |
| `task-run:<run_id>` | one run of a task — a real chat, own stream |
| `voice:<id>`        | a realtime voice session                    |
| `default`           | CLI single-shot                             |

A stream's event history *is* the conversation. After each turn the events are
persisted with AG2's `EventLogWriter` to `~/.ag2assistant/chats.db` and reloaded
on demand, so chats are **resumable** and never cross histories.

**One wire contract — `wire = log = {type, data}`.** A serialized event is exactly
what `EventLogWriter` persists:

```json
{ "type": "ag2.events.types.ModelResponse", "data": { … } }
{ "type": "ag2assistant.events.TaskCreated",  "data": { … } }
```

The same representation is used to **persist, replay, and live-stream**, so *history
(replay the log) and live (subscribe to new events) are the same path*. The GUI is a
thin renderer that maps event `type` → Svelte component. Custom
`ag2assistant.events.*` classes round-trip because deserialization resolves the class
by its fully-qualified name.

---

## 3. Process & deployment topology

Everything runs in **one process** by default. The CLI (`src/assistant/cli.py`)
exposes:

| Command                  | What it starts                                                        |
| ------------------------ | --------------------------------------------------------------------- |
| `ag2-assistant run`       | gateway + web UI **+** every channel whose token is set (one agent)   |
| `ag2-assistant gateway`   | REST + WebSocket API + web UI only (`--host`, `--port`, default 8800) |
| `ag2-assistant chat`      | interactive terminal chat                                             |
| `ag2-assistant agent "…"` | single-shot prompt → reply                                            |
| `ag2-assistant onboard`   | first-run interview (name, location, hours, style)                    |
| `ag2-assistant telegram \| discord \| slack` | a single messaging channel                         |
| `ag2-assistant version`   | version string                                                        |

`run` builds the gateway + task service via `build_gateway()`
(`src/assistant/gateway/core.py:638`), starts the scheduler, attaches channels whose
tokens are present, and serves the FastAPI app from `create_app()`
(`src/assistant/gateway/app.py`).

**Scale.** For a single user (1–5 chats) one process is sufficient; scaling out
(worker pool, session affinity, external queue, or AG2 `Hub` distributed transport)
is future work — the agent composes onto a Hub without changing the client API.

**Security posture:** the gateway is single-user and local-first. The only access
control is an **origin guard** on `/api/*` plus a per-socket origin check on the
WebSocket routes (close code 1008). There are no auth headers; a remote deployment
must sit behind its own auth proxy. See §12.

---

## 4. System diagram

See [`architecture.svg`](architecture.svg) for the layered diagram: clients →
gateway (REST + two WebSockets) → universal agent + tools, with the task subsystem,
memory, voice, HITL, and the persistent stores hanging off the event-stream spine.

---

## 5. Services & subsystems

### 5.1 Gateway core — `src/assistant/gateway/core.py`, `gateway/app.py`

`Gateway` owns chats, streams, persistence, and the shared agent.

- **Chats & streams.** `stream_for(chat_id)` returns the live per-chat
  `Stream`, hydrating it from disk on first use (`_get_stream`), repairing broken
  compaction with `sanitize_history()` before resume.
- **Turns.** `send_message()` resolves the stream, injects surface context, builds a
  per-turn permission manager + HITL hook (`_ask_kwargs`), runs `agent.ask(...)`,
  subscribes to the stream to tally usage and forward events, then persists events +
  a display transcript. Persistence is best-effort and never fails the user's turn.
- **Persistence.** Events → `EventLogWriter` → `chats.db`. A separate compact
  transcript (role/text) is written for fast chat listing/restore.
- **Reload.** `reload()` (`core.py:186`) reference-swaps the agent: in-flight turns
  finish on the old agent; the next turn uses a freshly-built one (new keys/config).
  Per-chat streams are untouched, so no history is lost. The task service rebuilds
  its planner lazily.
- **app.py** wires it together: builds/owns the gateway + `TaskService`, mounts the
  REST routes and the two WebSockets, serves the Svelte bundle at `/app`, and runs
  the FastAPI `lifespan` (start/stop of gateway, task service, scheduler).
- Helpers: `gateway/stream_bridge.py` (replay-then-subscribe bridge to a client),
  `gateway/wire.py` (`to_wire()`, `is_binary_event()` — audio is binary, not JSON).

No background loop lives here; the only long-running loop is the scheduler (§5.5).

### 5.2 Universal agent — `src/assistant/agent.py`

One shared `ag2` `Agent`, built by `create_agent()` (`agent.py:320`).
`model_config()` maps the provider to a config class (Gemini default 8192 max
output; OpenAI via the Responses API; Anthropic; Ollama). Per-turn system prompts
are assembled by `turn_prompt()` (chat) and `universal_turn_prompt()` (gateway/tasks
— adds the capability map + surface description), both appending live
`environment_context()` (date/time/location). Tools, memory policies, observers, and
the HITL hook are attached at construction. See §6 for the full agent inventory.

### 5.3 Tools & capabilities — `src/assistant/tools/`

`build_agent_tools()` selects tools by **capability** (`web`, `code`, `files`,
`images`, `skills`, `mcp`, `gmail`, `calendar`, `drive`). Chat gets the full set;
task subagents are scoped to their declared capabilities.

| Tool / group     | File                         | Notes                                             |
| ---------------- | ---------------------------- | ------------------------------------------------- |
| Web search       | `tools/__init__.py`          | native AG2 `DuckDuckSearchTool`                    |
| Shell / code     | `tools/__init__.py`, `docker_sandbox.py` | host or Docker sandbox; approval-gated |
| Web fetch        | `tools/web_fetch.py`         | function-tool fallback (native WF conflicts on Gemini) |
| Read file        | `tools/files.py`             | permission-gated host-path reader                 |
| Image generation | `tools/image_gen.py`         | provider-aware (Gemini native / OpenAI Responses) |
| Google           | `tools/google.py`            | Gmail/Calendar/Drive when signed in               |
| Skills           | skills registry toolkit      | search/install/run from skills.sh                 |
| Approval         | `tools/approval.py`          | command-approval middleware → permissions         |

### 5.4 MCP integration — `tools/mcp.py`, `tools/_mcp_compat.py`

`build_mcp_tools()` builds a `NamespacedMCPToolkit` per configured server. Raw MCP
tool names are namespaced (`<server>_<tool>`, e.g. `github_list_repos`) to avoid
collisions with native tools, and filtered against `allowed_tools`/`blocked_tools`
**before** namespacing. `_mcp_compat.py` quarantines the private AG2 MCP internals
(`AnyMCPConfig`, `MCPTool`, `_resolve_config`, `_mcp_session`, `_extract_content`,
`_wrap_middleware`) behind stable wrappers, raising `MCPCompatibilityError` on
version drift. Folder access is native now (see §Folders): the host `read_file` /
`list_folder` / `write_file` tools consult the install-wide Folder registry + Grants,
replacing the retired auto-seeded `repo-files` MCP.

### 5.5 Task subsystem — `src/assistant/tasks/*`, `gateway/tasks_service.py`

A **Task** is standing configuration — name, prompt, optional per-task model,
schedule, paused — nothing more. A **Run** is one execution of it, and its
transcript is an ordinary chat on the run's own stream (`task-run:{run_id}`):
a run *is* a chat the user can open live, steer mid-run with a normal message,
stop, or keep talking to after it finishes. There is no separate planner,
subtask tree, deliverable, or intake step — a run is one agent turn.

- **`Task` / `Run`** (`tasks/model.py`) — plain dataclasses, JSON-serializable.
  `Task.schedule` is a `{kind, at, cron}` union (`manual` / `once` / `cron`; the
  UI's hourly/daily/weekly/weekdays presets all serialize to `cron`, custom cron
  passes through as-is). `Run.status` is `running` / `needs_input` / `completed`
  / `failed` / `cancelled`; `Run.trigger` records what started it (`schedule` /
  `once` / `manual`); `Run.summary` is a cheap-model one-liner of the outcome.
- **`TaskStore`** (`tasks/store.py`) — CRUD over two doc kinds in one
  `tasks.db` (`/tasks/{id}.json`, `/runs/{id}.json`), serialized by
  `SerialStore`. `get_task()`/`get_run()` raise `TaskStoreCorruptionError` on a
  corrupt record; the listers log-and-skip so one bad record can't blank the
  list. `last_summaries()` returns a task's recent completed-run summaries,
  fed into the *next* run's prompt so a recurring task doesn't repeat itself.
- **`Scheduler`** (`tasks/scheduling.py`) — deterministic poll loop (`interval`
  default **30.0s**); `tick()` fires every armed, unpaused task whose
  `next_run_at` has passed. Recurrence is standard 5-field cron (`cronsim`) plus
  `@nicknames`, described for humans via `cron-descriptor`. **Single-owner:**
  exactly one scheduler runs per data dir — `TaskService.start` takes
  `scheduler=` (channel commands pass `False`; they keep the task tools but not
  the loop), backed by a cross-process `flock` leader lock (`scheduler_lock.py`,
  `~/.ag2assistant/scheduler.lock`), preventing the multi-process race where N
  schedulers fire the same `tasks.db` tasks.
- **`TaskService`** (`gateway/tasks_service.py`) — the bridge: owns the store +
  scheduler and runs each turn through the same `Gateway.send_message()` path a
  chat uses (`chat_id=run.stream_id`, `llm_config_id=task.model` picks the
  task's own cached per-model agent when set, `surface=` frames it as an
  unattended run and appends recent outcomes). `_fire()` (the scheduler
  callback) re-arms a `cron` task's `next_run_at` — or disarms a spent `once`
  back to `manual` — **before** running, so a slow run can never double-fire
  its slot. After a run finishes, `tasks/summary.py` distills a one-line
  outcome (cheap model), stored on the run and, for a task created from
  Telegram/Slack/Discord, pushed back to that chat (`_deliver()` /
  `set_notifier`). `stop_run()` cancels the run's turn
  (`Gateway.cancel_turn`), keeping whatever it already produced on the stream.
  `delete_task()` stops and deletes every run and its chat stream —
  irreversible.

### 5.6 HITL (human-in-the-loop) — `src/assistant/hitl/*`

Two stores, two lifetimes:

- **Durable task inquiries** — `hitl/inquiry.py` (`Inquiry`, `InquiryStore` →
  `inquiries.db`). Clarifications/permissions raised during a task run (the
  inquiry's `task_id` field actually carries the *run* id); survive restarts;
  answerable out-of-band from any surface. A raised inquiry flips its run to
  `needs_input`; answering flips it back to `running`
  (`TaskService._on_inquiry`). Surfaced as `InquiryRaised`/`InquiryAnswered`
  events on the run's stream and via `/api/inquiries/*`; unanswered ones also
  show in the web app's "Needs your input" strip.
- **Transient chat-turn prompts** — `hitl/gateway.py` (`GatewayAsker` + an in-memory
  `HitlServer` registry). Permission/clarification prompts during a chat turn;
  answered inline (WS `answer` frame) or via the styled `/hitl/{id}` page.
- Asker variants: `NullAsker` wrapped in `DurableAsker` (task runs — always
  durable, regardless of which channel the task came from), per-channel askers
  (Telegram/Discord/Slack), `DesktopAsker` (browser popup, CLI). `build_hitl_hook()`
  turns an asker into the AG2 `hitl_hook` dependency injected per turn.

### 5.7 Memory — `src/assistant/memory.py`, `observers.py`

A rolling user **profile** in `profile.db` (document at `/memory/working.md`), under
four headings (how / when / dislikes / writing style). Three write paths:

1. **Passive aggregation** — AG2's `WorkingMemoryAggregate` distils the profile every
   N turns (default 4); `WorkingMemoryPolicy` injects it into each turn.
2. **Explicit** — the `remember` tool → `record_preference()`.
3. **Feedback learner** — fire-and-forget after a 👍/👎 (see §6).

`record_preference(note, category, remove=…)` inserts/dedupes bullets and can prune
contradicting ones. `observers.py` adds passive guards (e.g. `ToolChurnObserver`)
that emit `ObserverAlert`.

### 5.8 Voice — `src/assistant/voice.py`, `voice_providers.py`

`build_voice_agent()` constructs an AG2 **`LiveAgent`** (Gemini Live or OpenAI
realtime, swappable via `voice_providers`). It carries a small read-only toolset
(list/get task, list/answer questions, `current_time`) and an **`ask_assistant`**
tool that delegates heavy work to the universal agent on the same chat — so voice
shares the chat's context and tools. Audio is full-duplex over `/api/voice`: 16 kHz
mono PCM in, 24 kHz PCM out, with transcript + delegated-event JSON frames
interleaved. No state is persisted per voice session.

### 5.9 Channels — `src/assistant/channels/*`

`Channel` adapters (`telegram.py`, `discord.py`, `slack.py`) normalize inbound
messages to `InboundMessage`, hand them to the shared `ChannelRouter`
(`router.py`) and render the platform-neutral `Outcome` it returns —
`Reply` / `Choose` / `Refuse` / `Ack` / `Nothing` — formatting per platform
(`formatting.py`). The router owns every decision: **mention-gating**
(`should_respond()`: DMs always, groups only on @mention), which runtime the turn
runs on, and `gateway.send_message()` with a per-chat id. Adapters keep only
platform concerns. HITL surfaces through `ChannelAsker`.

### 5.10 Storage, config & cross-cutting — `storage.py`, `config.py`, …

- **`storage.py`** — `SerialStore` (an `asyncio.Lock` over AG2's
  `SqliteKnowledgeStore`, since SQLite isn't safe for concurrent coroutine access),
  plus `now_iso()` / `new_id()`. `EventLogWriter` (AG2) writes the event log.
- **`config.py`** — `Config` resolved defaults ← global `config.yaml` ← active
  `llm_configs` ← env (`AG2ASSISTANT_*` wins). `with_profile()` then overlays that
  profile's `config.yaml` (per-section, profile key wins; env re-applied last).
  `read_yaml`/`write_yaml` + `update_global_section` own the shared-file I/O.
  `data_dir()`/`workspace_dir` derive from `Path.home()` (root overridable via
  `--data-dir` / `AG2ASSISTANT_DATA_DIR`).
- **`secrets.py`** — API keys in `secrets.json` (chmod 0600), loaded into
  `os.environ` at startup/reload (`OPENAI_API_KEY`, `GEMINI_API_KEY`,
  `ANTHROPIC_API_KEY`, GitHub token, Ollama base URL).
- **`settings.py`** — non-secret per-profile UI prefs (voice provider+voice,
  focuses, MCP server list), persisted at the top level of the
  profile's `config.yaml` alongside its Config overlay sections.
- **`permissions.py`** — `PermissionStore` (command grants → `permissions.json`)
  + per-turn `PermissionManager` (also resolves Folder Grants for the profile/chat).
- **`folders.py`** — `FolderStore`: the install-wide Folder registry + per-profile /
  per-chat Grants (`read` / `read_write`) at `root_dir/folders.json` (ADR 0006).
- **`usage.py`** — `UsageLedger` (daily tokens + estimated cost → `usage.json`,
  priced from `pricing.json`).
- **`workspace.py`** — sandbox-safe file I/O: `write_image` (`images/`),
  `write_upload` (`uploads/`), `resolve()` (no traversal escape), `list_files()`,
  `list_dirs()` (folder picker). A task run's output lives in its chat
  transcript (`task-run:{run_id}`), not a workspace file.
- **`observability.py`** — `setup_logging()` (rotating `ag2assistant.log`),
  `agent_logging_middleware()`, `log_suppressed()` (best-effort warnings),
  `capture_failure()` (JSON snapshot under `debug/`).

---

## 6. Agents & LLM calls

Every model-backed call in the backend. Two model tiers: **main**
(`config.llm.model`) for quality-sensitive work, **cheap/aggregate**
(`cheap_model(config)` / `config.llm.aggregate_model`) for background/bulk.

| # | Agent / call            | File                         | Tier  | Trigger                              | Structured output            |
| - | ----------------------- | ---------------------------- | ----- | ------------------------------------ | ---------------------------- |
| 1 | **Universal agent**     | `agent.py:320` (`create_agent`) | main  | every user turn (`agent.ask`) — also every task **run**, on the run's stream | — (conversational) |
| 2 | **Chat title**          | `title.py`                   | cheap | after first exchange (fire-and-forget) | `ChatTitle{title}`         |
| 3 | **Feedback learner**    | `feedback.py`                | cheap | on 👍/👎 (fire-and-forget)            | `FeedbackMemory{note, remove}` |
| 4 | **Run summarizer**      | `tasks/summary.py`           | cheap | after a task run completes            | `RunSummary{summary}`        |
| 5 | **Voice LiveAgent**     | `voice.py`                   | realtime | voice session; delegates via `ask_assistant` | — |
| 6 | **Image generation**    | `tools/image_gen.py`         | provider | `generate_image` tool call          | — (emits `ImageGenerated`)   |
| 7 | **Memory aggregator**   | `memory.py` (AG2 `WorkingMemoryAggregate`) | aggregate | every N turns / on end | — (markdown profile) |
| 8 | **CLI single-shot**     | `agent.py` (`ask`)           | main  | `ag2-assistant agent "…"`             | —                            |

A task run is not a distinct agent — it's the same universal agent (row 1),
optionally pinned to the task's own model via a cached per-model agent
(`Gateway._agent_for`, §5.1), given a "you're running unattended" system
surface and the task's recent run outcomes (§5.5).

Onboarding (`onboarding.py`) is **not** an LLM call — it's a fixed 4-question HITL
sequence that seeds the profile. The **reload** mechanism (§5.1) reference-swaps
the shared agent(s) without dropping streams.

---

## 7. HTTP & WebSocket endpoints

All under `create_app()` (`gateway/app.py`); `/api/*` is origin-guarded. Two
WebSockets: `/api/stream` (event spine) and `/api/voice` (audio).

### Chats

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET  | `/api/health` | gateway status |
| GET  | `/api/chats` | list resumable chats (newest first) |
| GET  | `/api/chats/{chat_id}` | display transcript for a chat |
| POST | `/api/message` | send a message, blocking → `{reply, chat_id}` |
| WS   | `/api/stream?chat=&surface=` | **event stream**: replay + live (frames below) |

### Tasks / runs / inquiries

A task is standing config; a run is one execution of it, and its own chat is
what `/api/stream?chat=task-run:{run_id}` opens.

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET    | `/api/tasks` | list tasks |
| POST   | `/api/tasks` | create a task (`422` with `{error}` on a bad schedule/model) |
| GET    | `/api/tasks/{id}` | task detail incl. its runs (`404`; `500` on corrupt record) |
| PATCH  | `/api/tasks/{id}` | edit any subset of fields (name/prompt/model/schedule/paused) |
| DELETE | `/api/tasks/{id}` | delete the task, its runs, and their chat streams — irreversible |
| POST   | `/api/tasks/{id}/run` | run now — start a run immediately, schedule unchanged |
| GET    | `/api/tasks/{id}/runs` | the task's run history (newest first) |
| GET    | `/api/runs/{id}` | one run's status/summary/task name |
| POST   | `/api/runs/{id}/stop` | stop a live run (keeps what it already produced) |
| POST   | `/api/runs/{id}/seen` | clear a finished run's unread highlight |
| GET    | `/api/inquiries/pending?task_id=` | open durable HITL inquiries (clarifications/approvals) |
| POST   | `/api/inquiries/{id}/answer` | answer a durable inquiry (out-of-band) |

### HITL (chat-turn prompts)

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET  | `/api/hitl/pending` | open chat-turn questions |
| GET  | `/hitl/{req_id}` | styled browser answer page |
| POST | `/hitl/{req_id}/answer` | submit an answer to that page |

### Settings / keys / MCP / folders

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET    | `/api/settings` | full snapshot (keys set, providers, voice, MCP, onboarded, fs roots) |
| POST   | `/api/settings/key` | set/clear an API key |
| POST   | `/api/settings/llm` | set provider + model |
| POST   | `/api/settings/voice_provider` | set voice provider |
| POST   | `/api/settings/onboarded` | mark onboarding done (per install) |
| POST   | `/api/settings/mcp` | add/upsert an MCP server |
| DELETE | `/api/settings/mcp/{name}` | remove an MCP server |
| POST   | `/api/settings/mcp/{name}/health` | health-check (lists tools) |
| GET    | `/api/fs/list?path=` | list subdirectories (folder picker) |

Folders + Grants (install-wide, ADR 0006) — snapshot `{folders:[{id,name,path,exists,grants}]}`:

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET    | `/api/folders` | every Folder with its path-exists badge + Grants |
| POST   | `/api/folders` | register a directory (400 non-dir, 409 duplicate path) |
| POST   | `/api/folders/{fid}` | rename / repoint a Folder |
| DELETE | `/api/folders/{fid}` | delete a Folder (revokes all its Grants) |
| POST   | `/api/folders/{fid}/grants` | upsert a Grant `(profile, chat_id) → mode` |
| DELETE | `/api/folders/{fid}/grants` | revoke one Grant |

### Files / memory / usage

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET    | `/api/files` | list workspace files |
| GET    | `/api/files/raw?path=&download=` | serve one workspace file (sandboxed) |
| DELETE | `/api/files/raw?path=` | delete a workspace file |
| GET    | `/api/memory` | read learned profile |
| POST   | `/api/memory` | overwrite learned profile |
| GET    | `/api/usage` | today's token + cost totals |

### Voice

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET  | `/api/voice/voices` | available voices + current + input rate |
| POST | `/api/voice/select` | persist voice selection |
| POST | `/api/voice/preview` | TTS preview (wav) |
| GET  | `/voices/{name}.wav` | pre-recorded sample (falls back to TTS) |
| WS   | `/api/voice?task=&chat=` | full-duplex audio + transcript/event frames |

### Google

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET  | `/api/google/status` | configured / signed-in / email |
| POST | `/api/google/credentials` | upload OAuth client JSON |
| POST | `/api/google/login_url` | build consent URL |
| GET  | `/api/google/callback` | OAuth redirect → styled status page |
| POST | `/api/google/logout` | clear token |

### App / static

`GET /` → `/app/`; `GET /app/{path}` serves the SPA (index fallback for deep
links); favicon/`*.svg`; catch-all routes unknown `/api/*` → 404, else → `/app/`.

### WebSocket frames

**`/api/stream`** — server sends `{event:{type,data}}` (replay then live),
`{type:"ready"|"turn_end"|"error", …}`. Client sends `{text, attachments?}`,
`{type:"answer", id, answer}`, `{type:"feedback", target_id, target_kind, sentiment,
reason, content?, request?}`.

**`/api/voice`** — server sends `{type:"ready"|"transcript"|"turn_end"|"error"}`,
`{event:{type,data}}` (delegated agent events), and binary 24 kHz PCM. Client sends
binary 16 kHz mono PCM mic frames.

---

## 8. Event taxonomy

### 8.1 Custom app events — `src/assistant/events.py`

All extend `AssistantEvent(BaseEvent)` and serialize as
`{"type":"ag2assistant.events.<Class>","data":{…}}`.

| Event | Key fields | Rendered as |
| ----- | ---------- | ----------- |
| `TaskCreated` | `task_id, title, kind` | task card in the thread, linking to the task page |
| `Attachment` | `path, name, media_type` | uploaded-file thumbnail / chip |
| `ImageGenerated` | `path, prompt, media_type` | inline clickable thumbnail |
| `SubagentTrace` | `subagent_id, inner{type,data}` | nested event under a subagent card |
| `InquiryRaised` | `inquiry_id, task_id, question, detail, options, kind` | durable HITL prompt |
| `InquiryAnswered` | `inquiry_id, answer` | resolves the HITL item |
| `FeedbackGiven` | `target_id, target_kind, sentiment, reason, content, request` | folds 👍/👎 onto the target item |

### 8.2 AG2-native events rendered — `web/src/lib/ag2map.js`, `web/src/project.js`

Model: `ModelRequest`, `ModelMessageChunk` (streaming), `ModelResponse`. Tools:
`ToolCallsEvent` (the rendered aggregate; `ToolCallEvent`/`GeminiToolCallEvent`/etc.
fold into it). Subagent lifecycle: `TaskStarted/Completed/Failed/Cancelled`. HITL:
`HumanInputRequest`. Observer: `ObserverAlert`. Memory/usage/voice events
(`Aggregation*`, `Compaction*`, `UsageEvent`, audio/transcription) are tracked
server-side and not rendered in the thread. `ag2map.js` colour-codes events into
subsystems (model/tool/memory/subagent/HITL/observer/voice/usage) for the AG2
Inspector.

### 8.3 Wire contract

`gateway/wire.py` `to_wire(event)` → `{type: fully-qualified-name, data:
event.to_dict()}`; `is_binary_event()` flags audio (sent as raw frames). The same
shape persists, replays, and live-streams — so replay and live are one path.

---

## 9. End-to-end data flow

```
User (web / CLI / voice / channel)
      │  WS /api/stream {text} (or REST /api/message, or channel inbound)
      ▼
StreamBridge ── replay history ──► client     (gateway/stream_bridge.py)
      │  subscribe(live)
      ▼
Gateway.send_message()                          (gateway/core.py)
   • resolve/hydrate chat Stream
   • inject surface context, permission mgr + HITL hook
      ▼
Universal Agent.ask(stream, prompt)             (agent.py)
   • model call (provider config)
   • tool calls (web/code/files/image/google/skills/MCP)
   • system tools → TaskCreated / InquiryRaised
   • observers → ObserverAlert
      ▼
Stream emits events ──► EventLogWriter ──► chats.db   (persist)
                   └──► subscribers ──► to_wire() ──► WS {event:{type,data}}  (live)
      ▼
Frontend foldEvent(items, wire) → thread items → Svelte components  (web/src/project.js)

Side flows:
   • Tasks: Scheduler._fire()/"Run now" → TaskService.start_run() → the same
     Gateway.send_message() turn above, on the run's own task-run:<run_id>
     stream; a cheap-model summary feeds the next run and (for a channel-
     origin task) the origin chat.
   • Memory: aggregator every N turns + feedback.learn() on 👍/👎 → profile.db.
   • Resume: stream_for() hydrates from chats.db; replay == live (same wire).
```

---

## 10. Frontend projection

`web/src/project.js` `foldEvent(items, wire)` is a pure reducer mapping each
`{type,data}` to renderable thread items (`user`, `agent` (streaming-aware),
`tools`, `taskcard`, `genimage`, `attachment`, `inquiry`, `note`, subagent
cards). It stamps `item.at ??= data.created_at` (AG2's auto timestamp) and folds
`FeedbackGiven` retroactively onto its target by stable key (message → `at`,
image → `path`). `web/src/lib/ag2map.js` maps event type → subsystem;
`web/src/transport/` holds the WS client (`/api/stream`) and the REST client
(`api.js`). The GUI never holds parallel state — it is a projection of the log.

---

## 11. On-disk state

Under `~/.ag2assistant/`:

| File | Holds |
| ---- | ----- |
| `chats.db` | event log for all chats (via `EventLogWriter`) + display transcripts |
| `tasks.db` | task configs (name/prompt/model/schedule) + run records (status/summary) |
| `inquiries.db` | durable HITL inquiries (clarifications/permissions) |
| `profile.db` | learned user profile (`/memory/working.md` inside) |
| `config.yaml` (global) | Config overrides + the `llm_configs:` section (named models + active); env still wins |
| `profiles/<id>/config.yaml` | per-profile Config overlay + non-secret UI prefs (voice, focuses, MCP servers) |
| `secrets.json` | API keys (chmod 0600), loaded into env at startup/reload |
| `permissions.json` | command grants (install-wide) |
| `folders.json` | Folder registry + per-profile/per-chat Grants (install-wide, ADR 0006) |
| `usage.json` / `pricing.json` | daily token+cost ledger / price overrides |
| `ag2assistant.log` | rotating application log (2 MB × 3) |
| `debug/<ts>-<chat>.json` | failure snapshots (error + traceback + event tail) |

Generated artifacts (images, uploads) live in the **workspace**
(`~/Documents/AG2 Assistant/` by default), in shared `images/`, `uploads/`
folders. A run's own output lives in its chat transcript, not the workspace.

---

## 12. Security model

- **Origin guard** on every `/api/*` request and a per-socket origin check on both
  WebSockets — the only access control. Same-origin browser traffic and non-browser
  callers pass; cross-origin browser traffic is rejected (HTTP) / closed 1008 (WS).
  Allowlist via `AG2ASSISTANT_ALLOWED_ORIGINS`.
- **Single-user, local-first.** No auth headers; a remote deployment must front the
  gateway with its own auth.
- **Filesystem sandboxing.** Workspace reads/writes go through `workspace.resolve()`
  (no traversal escape). Access to paths outside the workspace is governed by the
  Folder registry + Grants (ADR 0006): `read_file`/`list_folder` need a `read` Grant,
  `write_file` a `read_write` Grant, minted via the first-touch HITL prompt.
  `/api/fs/list` (the folder picker) is acceptable only because the gateway is local +
  single-user behind the origin guard.
- **Permissions & HITL.** Shell/code/file actions are approval-gated through
  `PermissionManager` + the HITL hook; grants persist in `permissions.json`.
- **Secrets** live in `secrets.json` (0600) and are surfaced to the UI only as
  set/hint, never echoed back.

---

## 13. Profiles (multi-profile runtime)

One install hosts several **isolated profiles** (e.g. *Work* / *Personal*) — each a
named, colour-coded runtime. A profile is a **directory** on disk and, at runtime,
**one `Gateway` + one `TaskService`**, all alive at once inside the single process
(channels sit beside them at install level, not within them). The web client is a viewer pointed at exactly one profile.

- **Registry & layout.** `profiles.json` (`src/assistant/profiles.py`) is the
  registry: `{active_default, onboarded, profiles:[{id, name, palette, workspace,
  archived}]}`. Each profile owns `~/.ag2assistant/profiles/<id>/` holding its
  `config.yaml` (overlay + UI prefs), `chats.db`, `tasks.db`, `inquiries.db`,
  `profile.db`, `permissions.json`, `usage.json`, `skills/`, `debug/`. `id` is an
  immutable slug;
  `palette` is unique while ≤6 profiles exist (the palette *is* the profile's
  identity).
- **ProfileManager** (`gateway/profile_manager.py`) boots **all unarchived**
  profiles at server start (so a non-viewed profile's scheduled tasks still fire),
  and owns `create` / `archive` / `reload` / channel start-stop. `get(pid)` is
  registry-first: unknown → 404, archived → 410, registered-but-not-running → 500
  (never lazy-boot). Zero profiles is a legal state (fresh install → browser
  onboarding creates the first, which boots live).
- **Derived config.** Each runtime is built from `Config.with_profile(meta)`, which
  overrides **every** path field (`data_dir`, `workspace_dir`, `skills_dir`) onto the
  profile dir (`root_dir` still points at `~/.ag2assistant`) and then overlays that
  profile's `config.yaml` Config sections (env still wins). A per-profile
  `config_factory(pid)` re-reads the registry entry on every call — so `reload()`
  (workspace/config edit) rebuilds against the right profile, not the global root.
- **Global vs per-profile split.** Per-profile: `config.yaml` (overlay + UI prefs),
  chats, tasks, memory, usage, skills, permissions, inquiries, and the HITL store
  (on the runtime). Global: the global `config.yaml`, `secrets.json` (keys load into
  one process-wide `os.environ`),
  `pricing.json`, `ag2assistant.log` (records tagged `[profile]` via a
  `LoggerAdapter`), and Google OAuth. The global-singleton audit removed every
  module-level path default so nothing silently leaks across profiles (pinned by
  `tests/test_profiles.py::test_no_global_path_defaults`).
- **Routing.** Profile-scoped routes are prefixed `/api/p/{pid}/…`, resolved by a
  `get_runtime` FastAPI dependency; global routes stay unprefixed (`/api/profiles`,
  `/api/secrets/key`, `/api/onboarded`, `/api/google/*`, `/api/status`,
  `/hitl/{id}`). No unprefixed aliases — a clean cutover.
- **Channels — install-global, never owned by a profile (ADR 0019).** A bot token
  serves one live connection, so each platform (Telegram / Discord / Slack) starts
  **once for the whole install**, on the `ProfileManager` rather than inside any
  runtime, as soon as its tokens are present. Every adapter is handed the one shared
  `ChannelRouter`, which resolves the runtime **per inbound message** — so nothing
  about a live adapter depends on a profile. What the registry holds is a per-Channel
  **default profile** (`profiles.json` `channel_defaults: {platform: pid|null}`):
  where that platform's conversations land when nothing else has been chosen. The
  global `GET /api/channels` returns `{platform: {default_profile, token_present,
  active, error}}`; `POST /api/channels/default` `{platform, profile}` sets or clears
  it, taking effect on the next message with no restart. A Channel with no default (or
  one whose profile has been archived) stays connected and refuses messages with
  `NO_PROFILE` rather than routing them somewhere unintended. A start failure is
  recorded per Channel (`active:false` with `error`, a missing token included) and
  never crashes boot. Bot tokens are stored in the global
  secrets store (like provider keys) and are editable inline in Settings → Channels via
  `POST /api/channels/token` `{platform, tokens:{ENV: val}}` (empty value clears;
  saving re-applies the live channel; values are never echoed). The Settings → Channels
  section is one default-profile picker per platform, plus its token field(s)
  underneath.
- **Archive.** Refuses the last profile; archiving the `active_default` requires a
  replacement. It cancels in-flight tasks (→ CANCELLED), closes WS with code `4001`,
  drops the runtime, and marks `archived: true` — durable across restarts (not
  booted, hidden from `GET /api/profiles`, folder kept on disk).
- **Client model.** The URL carries the profile (`/app/{pid}/…`, `router.js`);
  switching is a full-page navigation, so boot re-resolves and applies the profile's
  palette (`App.svelte`). Chips + `⌘1..9` shortcuts (`Drawer.svelte`, App-level
  keydown) switch; day/night theme stays a global preference.

> Diagram: [`architecture.svg`](architecture.svg) predates profiles and shows the
> single-runtime spine; conceptually every gateway/task/store box below the clients
> is now instantiated once **per profile**, with secrets/pricing/log/Google shared
> globally. The SVG is a hand-laid coordinate diagram — regenerating it for the
> per-profile fan-out is deferred rather than risk breaking the layout.

---

## Key design principles

1. **AG2 primitives are the spine** — streams/events/observers/persistence; don't
   build parallel architecture (`ag2-primitives-drive-architecture`).
2. **The GUI is a projection of the event stream** — `wire = log = {type,data}`;
   history and live are one path.
3. **UI-agnostic** — the gateway API is the only contract; the Svelte app is one
   client among several (channels, voice, CLI).
4. **One universal agent, many resumable per-chat streams** — isolation via
   streams, not separate agents.
5. **Build on main; migrate to native as it lands** — the durable task/inquiry layer
   is the seam, kept thin and speaking AG2's event vocabulary (`ag2-build-on-main`).

# AGClaw - Project Plan

## Backlog / Ideas

- **Task Manager — a maintained running summary per task (non-scheduled first).**
  A lightweight per-task "manager" that keeps a human-readable **summary** so when
  the user returns to a task they immediately know *what it is, what's happened,
  and its current status* — without reading the whole tree/chat. The summary is
  refreshed **periodically**, not on every event: e.g. **when the task completes**,
  and **~10 minutes after the last activity** (debounced). Scope it to
  non-scheduled tasks for now (scheduled tasks spawn per-occurrence runs).
  - *Role:* purely a summariser — outline the objective, progress so far
    (subtasks done/failed, key produced outputs), and where it stands. No control.
  - *Mechanism (sketch):* a cheap-model pass over the task tree + progress log +
    task-chat history → a stored `summary` field on the task, surfaced at the top
    of the task view and in `get_task`/`list_tasks`. Trigger on terminal status and
    on an idle timer (reset on any task activity); coalesce so a burst of activity
    yields one summary. Reuse the AG2 aggregate/summarise notions and the cheap
    `cheap_model`. Deterministic scheduling of *when* to summarise; LLM only for
    the prose. Feeds the universal agent's surface context (it can read the summary
    instead of re-deriving state).

- **Supervisor / watchdog — persistent, deterministic (no LLM).** An always-on
  control loop, separate from the agent, that health-checks every service
  (gateway, each channel, the scheduler, the task runner) on an interval and
  **restarts** anything that's down or wedged. Also supports **hot-reload**: when
  the agent's config/code changes, rebuild/swap the agent without a full restart.
  - *Mechanism:* a process/asyncio supervisor with per-service health probes +
    restart policy (backoff, max retries); a reload hook that re-runs
    `create_agent` and swaps it into the live `Gateway`. Deterministic and
    observable (status endpoint / log), never calls the LLM.
  - *Why:* keep a long-running personal assistant up unattended; pick up agent
    updates safely. Pairs with `agclaw run` (it would own the channel/gateway
    lifecycles) and could also watch the task runner/scheduler.

- **Onboarding — get to know the user (seeded question set).** A first-run flow that proactively asks a short set of high-value questions instead of waiting to learn them passively. Captures things the agent keeps needing: **location/timezone, working hours, name, preferred tone, channels, do-not-disturb**, etc.
  - *Mechanism:* reuse the HITL `Asker` (buttons/free-text) so onboarding runs on whichever surface started it (desktop popup, or a chat). A `agclaw onboard` command + auto-trigger on first interaction when the profile is empty.
  - *Storage:* answers seed the profile memory (`/memory/working.md`) and, where structured, config (e.g. write `AGCLAW_LOCATION`). The passive observer then keeps refining over time.
  - *Why:* the location gap (agent didn't know the city) is exactly the kind of fact this fixes up-front. Ties together observer memory + HITL + config.
  - *Keep it light:* 4–6 questions max, skippable, re-runnable; don't interrogate.

## Progress

| Phase | Status | Summary |
|-------|--------|---------|
| Phase 1: Core Agent Runtime | 🟢 Mostly Done | Agent + CLI + Config + 4 custom tools working on AG2 0.13.4 / Gemini. 22 unit tests pass |
| Phase 2: Gateway & API | 🟢 Core Done | FastAPI facade (`agclaw gateway`): REST `/api/message` + WS `/api/ws` + health. Per-session isolated multi-turn, tool use over HTTP — all verified. Distributed Hub option spiked (`examples/network_gateway_spike.py`) |
| Phase 3: Channel Integrations | 🟢 Telegram + Discord + Slack Live | Three adapters live-verified (DM + @mention gating, memory, tools, per-channel formatting, live date/time/location). Slack adds 👀 reaction while working. WhatsApp next |
| Phase 4: Skills & Plugins | 🟢 Core Done | Agent has AG2's `SkillSearchToolkit` — searches skills.sh registry, installs, and runs skills (progressive disclosure). Live-verified registry search. Skills install to `~/.agclaw/skills` |
| Phase 5: Memory & Intelligence | 🟡 In Progress | User-profile observer memory built (SQLite, passive, platform-tagged). Works end-to-end across processes |
| Phase 6: UI | 🟢 Reference UI Done | Self-contained web chat client served by the gateway at `/` (vanilla JS over REST/WS), ag2.ai-styled: streaming replies + inline HITL permission cards. Live-verified end-to-end with Chrome DevTools (chat round-trip, permission card → Allow once → file read). Richer/framework UI still optional |
| Phase 7: Advanced Features | ⬜ Not Started | AG2 0.13 network/distributed now available — reshapes this phase |
| HITL & Permissions | 🟡 In Progress | Pluggable `Asker` seam (via AG2 `hitl_hook`) + styled desktop `/hitl/{id}` pages (concurrent, AG2-branded). Core done; channel askers + permission store + attachments next |

### HITL & Permissions Detail

Goal: permission-gated resource access (Claude-Code-style) and channel-routed human questions.

- [x] `Asker` protocol + `build_hitl_hook()` adapter (`agclaw/hitl/base.py`) — wires to AG2 `context.input()`/`hitl_hook`
- [x] `DesktopAsker` + `HitlServer` — concurrent `/hitl/{id}` pages, per-request future registry, browser auto-open, styled to ag2.ai (Playfair Display + Open Sauce + coral #F95339). Verified visually + 5 unit tests
- [x] `create_agent(asker=...)` wiring + `agclaw agent` defaults to DesktopAsker
- [x] `PermissionStore` (persistent `~/.agclaw/permissions.json`) — per-folder grants, ancestor coverage, Once / Always / Deny via `request_access()`
- [x] `read_file` tool — permission-gated, returns PDFs/images as `DocumentInput`/`ImageInput` for **vision** reading (works on scanned docs), text files as text. Live-verified reading a real scanned PDF end-to-end (permission gate → vision → correct summary)
- [x] **Centralised `PermissionManager`** — single injected authority (store + asker + `check()` decision); tools call one method. `request_access` kept as a shim
- [x] Telegram channel asker — inline-keyboard buttons, free-text routing (next message = answer), `concurrent_updates(True)` to avoid the await-the-tap deadlock, transient prompt (deleted after answer). **Live-verified**
- [x] Discord channel asker — `discord.ui.View` buttons, free-text routing, transient prompt (events dispatched concurrently → no deadlock). **Live-verified**
- [x] Slack channel asker — Block Kit buttons + `@app.action` handler, free-text routing, transient prompt (chat_delete). **Live-verified**
- [x] **Closed the shell/code bypass** — `SandboxShellTool`/`SandboxCodeTool` now carry `approval_required()` middleware routed through the HITL asker, so the agent can't re-route a denied `read_file` via `cat`/code. Verified: Deny now yields `CANNOT_ACCESS` across all 3 file-access paths. (Fixed a `build_hitl_hook` forward-ref annotation bug surfaced by first real hitl_hook use.)
- [x] **Button-based command approval** (`tools/approval.py`, `require_command_approval`) — shell/code now prompt with the same Allow once / Always allow / Deny **buttons** as folder permissions (replaces AG2's free-text `approval_required`). "Always allow" is per-conversation (`context.variables`). Verified deny→`CANNOT_ACCESS`, allow→proceeds, across the button path
- [x] **Unified turn-level permission management** — the per-turn `PermissionManager` is now the single authority for **both** folder access (`read_file`) and command execution (shell/code via `check_command`). One shared turn-scoped decision context: persistent folder grants + turn-scoped allows/denies + a shared "user denied something this turn → stop asking" stance. Command approval no longer uses `context.variables` (removed the fragile path that caused the intermittent "Sorry, something went wrong"). Denial results tell the model not to retry. Verified: deny → `ASKED=1` → `CANNOT_ACCESS`, no escalation, no crash. Button UX consistent across folders + commands.
- [x] **Fixed observer-memory poisoning permissions** — the profile aggregator was recording permission *denials* as a durable "dislike" ("denies access to Downloads/shell"), which `WorkingMemoryPolicy` injected every turn → the agent preemptively refused file access **without prompting** (and it survived restarts via `profile.db`). Fix: aggregation prompt now explicitly **never records permission/security decisions** (they're transient, not preferences); cleaned the bad entry from the live profile. Lesson: keep operational state (permissions) out of the durable preference memory.
- [x] **HITL server hardening** — `HitlServer` now binds an ephemeral port (`port=0`, OS-assigned) so a lingering/cancelled run can never block a new one with "address already in use".
- [x] **`agclaw permissions` CLI** — `list` / `allow` / `revoke` / `block` / `unblock`. Persistent **block** (permanent deny) added to `PermissionStore` and honored by `PermissionManager.check` (never even prompts for a blocked folder).
- [x] **Onboarding (seeded questions)** — `agclaw onboard` command + auto-trigger on first interaction when the profile is empty. Asks name / location / working hours / answer-style through the same pluggable `Asker` (desktop popup or chat buttons/free-text), all skippable. Seeds the profile (`/memory/working.md`, preserving any existing) and writes `AGCLAW_LOCATION` to `.env`; one-time marker at `~/.agclaw/onboarded`. (`agclaw/onboarding.py`, gateway `_maybe_onboard`, 8 + 2 tests)
- [x] **Chat attachments** — files dropped into Telegram/Discord/Slack are downloaded and turned into AG2 multimodal `Input`s (`agclaw/attachments.py`: images→Image, PDFs/unknown→Document, audio/video→native, text→inlined) and threaded through `Gateway.send_message(attachments=...)` as positional inputs. Slack uses the bot token to fetch private file URLs (`files:read`). (8 tests; channel normalizers now accept caption-only/attachment-only messages)
- [x] **Docker sandbox for shell/code** — custom `DockerEnvironment`/`DockerSandbox` (`agclaw/tools/docker_sandbox.py`) implementing AG2's `Sandbox` protocol (AG2 ships no Docker backend yet). Runs shell/code in a throwaway container with **no host FS mount**, so the approval gate is dropped when active (the container *is* the boundary); `read_file` stays host-permission-gated. Falls back to local+approval with a warning if Docker is unavailable. Selectable via `--sandbox docker` or `AGCLAW_SANDBOX`/`AGCLAW_DOCKER_IMAGE`/`AGCLAW_DOCKER_NETWORK`. Live-verified end-to-end (agent computed Fibonacci in-container with no prompt; containers cleaned up). (6 unit + 2 real-Docker integration tests)
- [x] **Combined `agclaw run`** — one process serving the REST/WS gateway + every channel whose token is configured, all sharing one `Gateway`/agent/profile (`create_app(gateway=...)` reuse). `--no-rest`, `--port`, `--sandbox` flags. (3 tests)
- [x] **Mount HITL routes on the gateway** — the running gateway now serves the styled `/hitl/{id}` pages + `/hitl/{id}/answer` (shared `HitlServer` registry via `add_hitl_routes`), plus `GET /api/hitl/pending`. `GatewayAsker` registers questions there; REST clients poll+answer, WS clients get a pushed `question` frame and answer over the same socket (`_drive_turn` reads answer frames concurrently while the turn is blocked). Request timeout → safe deny (default 5 min). (`agclaw/hitl/gateway.py`, `hitl/desktop.py` refactor; 4 tests incl. full WS Q&A flow)
- [x] **Sandbox skill-script execution (Docker)** — `DockerMountSandbox` + `build_docker_skill_runtime` (`agclaw/tools/docker_sandbox.py`): when `--sandbox docker`, each skill script runs in a one-shot `docker run --rm` container that bind-mounts **only that skill's `scripts/` dir** (so untrusted skill code can't reach the user's files); storage/discovery stay local. Wired into `build_skills_toolkit`. (2 unit + 2 real-Docker integration tests)
- [x] **Tuned aggregation cadence** — profile distillation now batches `every_n_turns` (default 4, `AGCLAW_AGGREGATE_EVERY_N`) instead of firing `on_end` every message; single-shot `agclaw agent` still aggregates `on_end` so its one turn is captured (`create_agent(single_shot=...)`). (3 tests)
- [x] **Interactive `agclaw chat`** — multi-turn terminal REPL over a single `Gateway` session (memory, permissions popup, onboarding all apply); non-blocking input via `asyncio.to_thread`. Live-verified cross-turn recall.

### Phase 1 Detail

- [x] Project scaffolding (pyproject.toml, src layout)
- [x] AG2 Beta Agent setup with system prompt
- [x] Tools — native AG2 built-ins (`DuckDuckSearchTool`, `SandboxShellTool`, `SandboxCodeTool`) + custom `web_fetch` fallback, selected per provider via `build_agent_tools()`
- [x] CLI interface (`agclaw agent "message"` working)
- [x] Basic config system (Pydantic, .env, Gemini default)
- [x] Unit tests (config, agent, tools, memory — 22 passing)
- [x] Integration tests (Gemini round-trip, tool use, memory learning)
- [x] Session/profile persistence (SQLite via AG2 KnowledgeStore)
- [x] Multi-turn interactive conversation via CLI (`agclaw chat`)

### Phase 5 Detail — Observer Memory (in progress)

- [x] Persistent user-profile store (`SqliteKnowledgeStore`, `~/.agclaw/profile.db`)
- [x] Passive learning via `WorkingMemoryAggregate` with custom 4-dimension prompt
      (how / when / dislikes / writing style), platform-tagged
- [x] Profile injection via `WorkingMemoryPolicy` assembly
- [x] CLI: `agclaw profile show` / `agclaw profile clear`, `--memory/--no-memory`, `--platform`
- [x] Unit + integration tests; verified recall across separate processes
- [ ] Per-platform nuance once channels land (platform flows from channel adapter)
- [x] Tune aggregation cadence — batches `every_n_turns` (default 4) for sessions; `on_end` only for single-shot CLI

### Phase 2 Detail — Gateway (core done)

- [x] `Gateway` session manager — per-session isolated multi-turn via `AgentReply.ask()` chains
- [x] FastAPI facade — `GET /api/health`, `POST /api/message`, `WS /api/ws`
- [x] `agclaw gateway` CLI (uvicorn)
- [x] Unit tests (fake-agent) + integration test (real multi-turn + isolation); live REST verified (multi-turn, isolation, tool use)
- [x] Distributed Hub spike (`serve_ws`) — agent reachable cross-process over WebSocket
- [x] Channel-session mapping — each surface maps to a `session_id` (e.g. `telegram:<chat>`); the gateway keys a persistent stream per session
- [x] **Resumable conversation history** — each session is a persistent AG2 `Stream`; events are written to `~/.agclaw/sessions.db` after every turn via `EventLogWriter` and reloaded into a fresh stream on demand, so conversations survive a restart with full context (all surfaces). Web UI gained a History drawer + transcript restore. REST: `/api/sessions`, `/api/sessions/{id}`. Live-verified (reload restores; real cross-restart recall). (5 tests + 1 integration)
- [ ] Optional: put the gateway agent on a Hub for multi-agent/cross-machine
- **Finding:** one shared agent across AG2 network conversation channels leaked history between sessions; direct `AgentReply.ask()` chains are isolated and the right primitive for the single-agent facade. Hub retained for distributed/multi-agent.

### Phase 3 Detail — Channels (in progress)

- [x] Channel layer (`agclaw.channels`) — `Channel` ABC, `InboundMessage`, `should_respond` gating, `get_channel` factory
- [x] Telegram adapter — long-polling, DM + group @mention/reply gating, mention stripping, "⏳ Working on it…" placeholder edited into the final reply (typing action isn't reliably rendered for bots on Desktop/Web)
- [x] Session mapping — one session per chat (`telegram:<chat_id>`); gateway created with `platform="telegram"` so profile observations are tagged
- [x] `agclaw telegram` CLI
- [x] Per-channel outbound formatting — agent stays format-neutral; `Channel.format_outbound()` hook; Telegram converts Markdown → clean plain text (`channels/formatting.py`)
- [x] Unit tests (gating + normalization + formatting, no network)
- [x] **Live test — DM verified end-to-end on @ag2claw_bot** (reply, memory write, tool use); plain-text formatting fix applied after raw-Markdown was observed
- [x] Discord adapter (`discord.py`) — DM + @mention gating, native typing indicator, Markdown passthrough, 2000-char chunking; 9 unit tests. **Live-verified on a test server** (needs Message Content Intent enabled; `login()`+`connect()` lifecycle, not `start()`-in-task)
- [x] Live environment context — agent knows current date/time (system clock) + location (`AGCLAW_LOCATION`), injected per turn via `prompt=[persona, env]` (refreshes; constructor prompts are evaluated once)
- [x] Slack adapter (`slack-bolt`, Socket Mode) — DM + @mention gating, Markdown→Slack-mrkdwn conversion, 3500-char chunking, 👀 reaction added while working / removed on reply; 19 unit tests. **Live-verified** (needs `message.im` event + `im:history`/`reactions:write` scopes + reinstall; Messages Tab must be enabled in App Home for DMs)
- [x] Combined runner (`agclaw run` — REST + all configured channels, one shared agent) and media/attachments (images/PDFs/audio/video/text → AG2 multimodal inputs; live-verified on Telegram/Slack/Discord)
- [ ] WhatsApp

### Phase 4 Detail — Skills (core done)

- [x] `SkillSearchToolkit` wired via `build_skills_toolkit()` — search/install/remove (skills.sh registry) + list/load/read/run (local), progressive disclosure
- [x] Skills install to `config.skills_dir` (`~/.agclaw/skills`); `LocalRuntime` with blocked-command safety list
- [x] `create_agent(skills=True)` (on by default; flows through CLI/gateway/channels)
- [x] Unit tests (toolkit build, tool surface, agent wiring) + live registry-search test (found real skills, e.g. `pdftk-server`)
- [ ] Optional: `GITHUB_TOKEN` for higher registry rate limits (60/hr unauth)
- [x] Sandbox skill script execution (Docker) — one-shot container per script, mounts only the skill's own dir
- [x] AGClaw-bundled skills — first-party `web-research` / `pdf-tools` / `email-drafting` shipped in-package, discoverable on first run via `extra_paths` (no install). Live-verified the agent lists/uses them. (1 test)
- [x] **Google integration (Gmail / Calendar / Drive)** — custom OAuth (`agclaw[google]` extra) with desktop consent flow (`agclaw google login/logout/status`), token cached + auto-refreshed at `~/.agclaw/google_token.json`. Tools auto-appear when signed in: `gmail_search/read/create_draft` (read), `gmail_send` + `calendar_create_event` (write — **HITL-gated**), `calendar_list_events`, `drive_search/read`. Mocked tests (no real OAuth/sends); live verification pending user's Google Cloud OAuth client. (6 tests)

### Docs

- [x] `docs/research-openclaw.md`
- [x] `docs/research-ag2-beta.md`
- [x] `docs/plan.md` (this file)
- [x] `docs/architecture.md`
- [x] `docs/architecture.svg`
- [x] `docs/usage.md`
- [x] `docs/memory.md` (observer/profile design)
- [x] `CLAUDE.md`

## Vision

AGClaw is an OpenClaw alternative built using AG2's Beta framework. It reimagines OpenClaw's core capabilities (gateway, channels, agent runtime, tools, skills) in Python using AG2 Beta's event-driven architecture instead of Pi agent + TypeScript.

## AG2 Beta Availability (as of May 2026, ag2 0.12.3)

**Major update:** Most of the previously feature-branch-only functionality now ships on main. AGClaw can use AG2's native capabilities for nearly everything.

### Available on main (use directly)

- `Agent` (with integrated `KnowledgeConfig` and `TaskConfig`) + `AgentReply`
- `@tool` decorator + expanded built-in tools (Shell, WebSearch, WebFetch, CodeExecution, ImageGen, Memory, Skills, Subagents)
- Middleware pipeline (logging, retry, token limiter, history limiter, telemetry, approval)
- Event/Stream system (MemoryStream, event types, conditions)
- Response Schema (structured outputs with validation/retry)
- HITL hooks
- LLM configs (OpenAI, Anthropic, Gemini, Ollama, DashScope)
- **Assembly Policies** — conversation, working_memory, episodic_memory, sliding_window, token_budget, alert
- **KnowledgeStore** — Memory, Disk, SQLite, Redis, Locked backends
- **Compaction strategies** — TailWindowCompact, SummarizeCompact
- **Aggregation strategies** — ConversationSummaryAggregate
- **Watch system** — EventWatch, CronWatch, IntervalWatch, BatchWatch, DelayWatch, WindowWatch
- **Built-in observers** — LoopDetector, TokenMonitor
- **Subagents** — SubagentTool for spawning sub-conversations
- **Skills runtime** — local_skills, runtime, skill_search
- **Docker extension** — containerized tool execution
- **Daytona extension** — cloud sandbox integration
- **AG-UI integration** — UI protocol support
- **AgentSpec** — declarative agent definitions
- **FilesAPI** — agent file output management

### Still NOT on main

- **Hub / Network module** — distributed agent networking. Only referenced in docstrings. AGClaw will not depend on this.
- **Full MCP client** — `MCPServerTool` exists as schema-only stub; full implementation still in progress on the roadmap.

### Impact on AGClaw

Build directly against AG2 main. We no longer need to build custom implementations of:
- Session/knowledge persistence → use `DiskKnowledgeStore` or `SQLiteKnowledgeStore`
- Context assembly → use `ConversationPolicy`, `WorkingMemoryPolicy`, etc.
- Compaction → use `TailWindowCompact` / `SummarizeCompact`
- Observers → use `LoopDetector`, `TokenMonitor`
- Cron/scheduling → use `CronWatch` / `IntervalWatch`
- Skills → use AG2's skills runtime
- Subagents → use `SubagentTool`
- Docker sandboxing → use `extensions/docker`

What AGClaw still owns (not framework concerns):
- Gateway (FastAPI + WebSocket)
- Channel adapters (Telegram, Discord, Slack, WhatsApp)
- CLI
- Application-level config (Pydantic models wrapping AG2 configs)
- Multi-session orchestration across users/channels

### AG2 Beta Roadmap (as of April 2026)

Source: https://docs.ag2.ai/latest/docs/beta/roadmap/

| Roadmap Item | Status | Relevance to AGClaw |
|---|---|---|
| **History management** | 🔄 In Progress | **High** — custom session persistence (Phase 1). |
| **Built-in Tools** | 🔄 In Progress | **High** — Shell, WebFetch, CodeExecution being finalized. |
| **MCP** | 🔄 In Progress | **High** — MCPServerTool is currently a stub. Full implementation incoming — wait for this rather than building our own MCP client (Phase 7) |
| **Shell Tool** | 📋 Next | **Medium** — already exists on main, being enhanced |
| **Skills support** | 📋 Next | **High** — directly aligns with Phase 4. Design our skill format to be compatible with whatever AG2 ships |
| **A2A** | 📋 Next | **Low for now** — Agent-to-Agent protocol, relevant if we add multi-agent in Phase 7 |
| **Orchestration** | 📋 Next | **Medium** — could replace custom agent coordination in Phase 7 |
| **Subagents** | 🔮 Future | **Medium** — Sub Tasks and/or Dynamic Agents |
| **Checkpoints and snapshots** | ��� Future | **Medium** — would complement session persistence |
| **Prometheus metrics** | 🔮 Future | **Low** — OpenTelemetry already available on main |
| **TUI runtime** | 🔮 Future | **Low** — nice to have alternative CLI |

**Key takeaway:** History management, MCP, and Skills are all in progress or next — three items we flagged as gaps. For MCP specifically, we should wait for the full implementation rather than building our own. For History and Skills, build custom now but keep interfaces compatible.

**Not on the public roadmap:** Actor, Assembly Policies, KnowledgeStore, Compaction/Aggregation, Watch system, Network/Hub. These remain feature-branch-only with no announced timeline.

## Component Mapping: OpenClaw -> AGClaw

| OpenClaw Component | AGClaw Equivalent | AG2 Beta Status |
|--------------------|-------------------|-----------------|
| Pi Agent runtime | AG2 Beta `Agent` + custom session layer | ✅ Available |
| Gateway (WebSocket) | FastAPI + websockets | Custom (not AG2) |
| Sessions (JSONL) | Custom persistence (SQLite/file) | ⚠️ KnowledgeStore unreleased |
| Bootstrap files (SOUL.md) | System prompts + custom context injection | ⚠️ Policies unreleased |
| Channel plugins | Channel adapters (python libs) | Custom (not AG2) |
| Tools (60+) | `@tool` decorator + built-in tools | ✅ Available |
| Skills (ClawHub) | Plugin/skill loader + tool registration | ✅ Available (SkillsTool) |
| Canvas | Web UI (FastAPI serving HTML/JS) | Custom (not AG2) |
| Config (TypeBox/Zod) | Pydantic models | Custom + AG2 LLM configs |
| CLI (Commander) | Typer CLI | Custom (not AG2) |
| Cron/tasks | Custom scheduler (APScheduler) | ⚠️ CronWatch unreleased |
| Observers/alerts | Custom on AG2 Observer protocol | ⚠️ Built-ins unreleased |
| Memory/compaction | Custom compaction logic | ⚠️ Strategies unreleased |
| Model failover | AG2 multi-provider config | ✅ Available |
| Device nodes | Future: REST API | Custom (not AG2) |

## Architecture

**Language split:** Python for all backend. UI is separate and UI-agnostic (any client that speaks REST/WebSocket).

```
  Any UI Client                   Messaging Channels
  (Web, Desktop,              (Telegram, Discord, Slack,
   Mobile, CLI)                WhatsApp, ...)
       |                              |
       |    REST + WebSocket API      |
       +------------+  +--------------+
                    |  |
          +----------------------------+
          |      AGClaw Gateway        |
          |    (FastAPI + WebSocket)   |
          |                            |
          |  - REST API (send/recv)    |
          |  - WebSocket (streaming)   |
          |  - Channel manager         |
          |  - Session router          |
          |  - Event bus               |
          +----------------------------+
                      |
          +----------------------------+
          |    Channel Adapter Layer   |
          |  (common message protocol) |
          |                            |
          |  Inbound:                  |
          |    Platform msg -> Message |
          |  Outbound:                 |
          |    Response -> Platform fmt|
          +----------------------------+
                      |
          +----------------------------+
          |      AG2 Beta Agent        |
          |                            |
          |  - System prompt (SOUL)    |
          |  - Middleware pipeline      |
          |  - Tools                   |
          |  - Response schema         |
          |  - HITL hooks              |
          +----------------------------+
                      |
          +----------------------------+
          |    Session & Memory Layer   |
          |  (custom, KnowledgeStore-  |
          |   compatible interface)    |
          +----------------------------+
```

### Message Flow

1. Message arrives from **any source** (Telegram, Slack, CLI, Web UI, etc.)
2. **Channel adapter** normalizes it to a common `Message` format (text, media, metadata, origin)
3. **Gateway** routes to the correct session (by user + channel identity)
4. **AG2 Agent** processes via `ask()` with session history
5. **Response** flows back through gateway -> channel adapter -> platform-specific format
6. **UI clients** connected via WebSocket receive real-time event stream

### Key Principle: UI-Agnostic API

The gateway exposes a clean REST + WebSocket API. Any client can consume it:
- Web app (React, Vue, Svelte, etc.)
- Desktop app (Electron, Tauri)
- Mobile app
- CLI (`agclaw` command)
- Another agent or service

The API contract is the boundary — UI choices are deferred and independent.

## Implementation Phases

Legend:
- **AG2 ✅** = Use existing AG2 Beta feature on main
- **AG2 🔮** = Needs unreleased AG2 Beta feature (build custom now, migrate later)
- **AGClaw** = Application-level, not suitable for an agentic framework

### Phase 1: Core Agent Runtime

| Item | Where | Notes |
|------|-------|-------|
| Project scaffolding (pyproject.toml, src layout) | **AGClaw** | Standard Python project setup, nothing framework-level |
| Agent setup with system prompt (SOUL equivalent) | **AG2 ✅** | `Agent` class with `prompt` param covers this directly |
| Tool registration (shell, web_fetch, code_execution) | **AG2 ✅** | Built-in ShellTool, WebFetchTool, CodeExecutionTool all on main |
| CLI interface (`agclaw agent "message"`) | **AGClaw** | App-level CLI (Typer). Not framework concern |
| Config system (Pydantic models for agent/channel/provider settings) | **AGClaw** | App-level config. AG2 has LLM configs (`OpenAIConfig` etc ✅) but overall app config is ours |
| Session persistence (save/restore conversation history) | **AG2 🔮** | KnowledgeStore is unreleased. Build custom SQLite/JSONL persistence. Design interface compatible with KnowledgeStore for migration |

### Phase 2: Gateway & API

**Direction decision (spiked):** A distributed Hub-based gateway prototype works
(`examples/network_gateway_spike.py`) — an AGClaw agent registered on an AG2
`Hub`, served over WebSocket via `serve_ws`, reachable from a separate client
process, with tools running server-side. The Hub already provides routing,
durable per-channel WAL, audit log, auth (`ApiKeyAuth`), and governance — much of
what we'd otherwise hand-build. **Leaning toward building the gateway on the Hub**
rather than raw FastAPI, with a thin REST/WebSocket facade for non-AG2 UI clients.

| Item | Where | Notes |
|------|-------|-------|
| Gateway core (routing, sessions, audit) | **AG2 Hub** 🔵 | Spiked working. Hub = message bus + WAL + audit + governance |
| Distributed transport | **AG2 `serve_ws`/`WsLink`** ✅ | Cross-process/cross-machine, proven in spike |
| REST/WebSocket facade for UI clients | **AGClaw** | Thin layer so non-AG2 UIs (web/desktop) can connect without speaking the Hub protocol. Likely FastAPI in front of the Hub |
| Session management (per user+channel) | **AGClaw + Hub channels** | Map sessions to Hub channels; AGClaw owns user↔channel identity |
| Health/status endpoints | **AGClaw** | App-level ops |

### Phase 3: Channel Integrations

| Item | Where | Notes |
|------|-------|-------|
| Channel adapter interface (protocol/ABC) | **AGClaw** | Defines how messaging platforms plug in. Not framework-level — this is OpenClaw-style app architecture |
| Telegram channel | **AGClaw** | python-telegram-bot integration. App-level adapter |
| Discord channel | **AGClaw** | discord.py integration. App-level adapter |
| Slack channel | **AGClaw** | slack-bolt integration. App-level adapter |
| WhatsApp channel | **AGClaw** | Webhook/API integration. App-level adapter |
| Mention-gating and group message routing | **AGClaw** | App-level message filtering logic before invoking agent |

### Phase 4: Skills & Plugins

| Item | Where | Notes |
|------|-------|-------|
| Skill definition format (SKILL.md equivalent) | **AGClaw** | App-level skill packaging convention. Not framework concern |
| Skill loader and registry | **AG2 ✅** (partial) | AG2 has SkillsTool + LocalSkillsTool on main. May need app-level registry on top for discovery/management |
| Dynamic tool registration from skills | **AG2 ✅** | `@tool` decorator and Tool protocol support dynamic registration |
| Bundled skills (web search, code execution, etc.) | **AG2 ✅** | WebSearchTool, CodeExecutionTool, ShellTool already built-in |

### Phase 5: Memory & Intelligence

| Item | Where | Notes |
|------|-------|-------|
| Context assembly (conversation history + working memory injection) | **AG2 🔮** | Assembly Policies (ConversationPolicy, WorkingMemoryPolicy, etc.) are unreleased. Build custom middleware that prepends context before LLM calls. Use AG2 Middleware ✅ hook points |
| Compaction for long conversations | **AG2 🔮** | CompactStrategy (TailWindowCompact, SummarizeCompact) unreleased. Build custom: either truncate tail or LLM-summarize old events. Plug into middleware pipeline ✅ |
| Knowledge extraction and persistence | **AG2 🔮** | AggregateStrategy unreleased. Build custom: extract key facts from conversations, persist to our session store |
| Observers (loop detection, token monitoring) | **AG2 🔮** (partial) | Observer protocol ✅ exists on main but LoopDetector/TokenMonitor implementations are unreleased. Implement custom observers using the base protocol |
| Migration path design | **AGClaw** | Design our custom interfaces to align with AG2's unreleased APIs. App-level architecture decision |

### Phase 6: UI (deferred, language TBD)

| Item | Where | Notes |
|------|-------|-------|
| Chat interface | **Separate project** | Not Python. Could be web (React/Vue/Svelte), desktop (Electron/Tauri), or both. Consumes gateway REST + WebSocket API |
| Canvas workspace | **Separate project** | Rich agent output rendering. Part of whatever UI is chosen |
| Session browser and management | **Separate project** | Admin/management UI. Same API consumer |

UI is deliberately decoupled. The gateway API is the contract — UI technology is chosen independently.

### Phase 7: Advanced Features

| Item | Where | Notes |
|------|-------|-------|
| Cron/scheduled tasks | **AG2 🔮** | CronWatch/IntervalWatch unreleased. Use APScheduler or similar Python scheduler. App-level scheduling |
| HITL approval flows | **AG2 ✅** | HumanHook, ApprovalMiddleware on main. Wire into gateway for user-facing approval UI |
| MCP server integration | **AG2 🔄** (in progress on roadmap) | MCPServerTool is currently a stub. Full MCP implementation is on the AG2 roadmap as in-progress — wait for this rather than building our own MCP client |
| Multi-agent networking | **AG2 🔮** | Hub/Network entirely unreleased. Build custom agent coordination if needed, or defer until Hub lands on main |
| Realtime voice | **AG2 ✅ (shipped in AGClaw)** | LiveAgent + Gemini Live. Browser mic→16 kHz PCM over `/api/voice` WS → `RecordedAudioEvent`; `SynthesizedAudioEvent`→24 kHz playback; transcripts as bubbles. Voice agent has a basic tool subset + `ask_assistant` that delegates heavy work to the universal agent. Follow-ups: HITL/permission prompts spoken over voice; barge-in tuning; persist voice transcripts to the session |

## Tech Stack

**Backend (Python):**
- **Runtime:** Python 3.12+
- **Agent Framework:** AG2 Beta (`autogen.beta`)
- **Web Framework:** FastAPI + uvicorn
- **WebSocket:** websockets / Starlette
- **CLI:** Typer
- **Config:** Pydantic v2
- **Channels:** python-telegram-bot, discord.py, slack-bolt
- **Storage:** SQLite (via aiosqlite)
- **Testing:** pytest + pytest-asyncio

**UI (separate, deferred):**
- Language/framework TBD (not Python)
- Consumes gateway REST + WebSocket API
- Could be web, desktop, or mobile

## Testing Strategy

Tests are built progressively alongside each phase, not bolted on at the end.

### Per-Phase Testing

**Phase 1: Core Agent Runtime**
- Unit tests: config loading/validation, session persistence CRUD, message model serialization
- Integration tests: Agent.ask() round-trip with a real LLM (mock-free), tool execution end-to-end
- CLI smoke tests: `agclaw agent "hello"` produces a response

**Phase 2: Gateway & API**
- Unit tests: session routing logic, event bus pub/sub, message normalization
- Integration tests: FastAPI TestClient for REST endpoints, WebSocket connect/send/receive lifecycle
- Multi-client test: two WebSocket clients receive the same event stream

**Phase 3: Channel Integrations**
- Unit tests: each adapter's normalize-inbound and format-outbound with fixture messages
- Integration tests: adapter + gateway end-to-end with mock platform webhooks (httpx mock server)
- Mention-gating tests: group messages without mention are ignored, with mention are processed

**Phase 4: Skills & Plugins**
- Unit tests: skill loader discovers and parses skill definitions, tool registration from skill
- Integration tests: load a test skill, invoke its tool through the agent, verify result

**Phase 5: Memory & Intelligence**
- Unit tests: context assembly produces correct prompt, compaction reduces history correctly
- Integration tests: multi-turn conversation with compaction trigger, verify agent retains key context after compaction
- Observer tests: loop detector fires after N repeated messages, token monitor tracks cumulative usage

**Phase 6: UI**
- **Chrome DevTools MCP** for all web UI testing: navigate, screenshot, click, fill forms, verify DOM state
- WebSocket streaming tests via Chrome DevTools: verify real-time message rendering
- Cross-browser/viewport testing via Chrome DevTools emulation
- Accessibility audit via Chrome DevTools Lighthouse

**Phase 7: Advanced Features**
- Integration tests: cron task fires on schedule, HITL approval blocks then resumes agent
- MCP integration test: agent connects to an MCP server, lists tools, calls a tool

### Test Infrastructure

- **Framework:** pytest + pytest-asyncio
- **API testing:** httpx (FastAPI TestClient) for REST, websockets client for WS
- **Web UI testing:** Chrome DevTools MCP (screenshot, click, fill, evaluate_script, navigate)
- **Fixtures:** shared AG2 Agent config, test session store, mock channel adapters
- **CI:** tests run on every PR; integration tests that need LLM keys run with secrets in CI or are marked `@pytest.mark.integration`
- **Coverage:** aim for high unit coverage on core (message model, session, config), integration coverage on critical paths (message flow end-to-end)

### Testing Principles

- **Mock-free where feasible:** Prefer real integrations (real LLM calls, real SQLite, real WebSocket) over mocks. Mocks hide bugs at boundaries.
- **Progressive:** Each phase adds tests for its own layer. No phase is "done" without tests passing.
- **Integration over unit for agent behavior:** Unit tests verify data structures and logic. Integration tests verify the agent actually works — processes a message, uses a tool, returns a response.

## Key Design Decisions

1. **Python for all backend**: Gateway, agent, channels, CLI, config — all Python. UI is separate and non-Python
2. **UI-agnostic API**: Gateway exposes REST + WebSocket. Any UI client can connect. No tight coupling to a specific frontend
3. **AG2 Beta Agent as core**: Use `Agent` (Actor not yet on main) with custom session/memory layer
4. **Common message protocol**: Channel adapters normalize platform messages to/from a shared `Message` format. Agent is platform-unaware
5. **Compatible interfaces**: Design custom context/memory/observer implementations to align with AG2's unreleased APIs for future migration
6. **Channel adapters as plugins**: Clean interface for adding new channels
7. **Pydantic config**: Type-safe configuration with validation
8. **Leverage what's available**: Maximize use of AG2's tools, middleware, HITL, response schema rather than rebuilding

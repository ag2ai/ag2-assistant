# OpenClaw Research

## Overview

OpenClaw is a free, open-source, autonomous AI agent that runs locally on your devices and uses LLMs (Claude, GPT, DeepSeek, etc.) as its "brain." It acts as a personal AI assistant accessed primarily through messaging platforms.

- **License:** MIT
- **Language:** TypeScript + Swift (companion apps)
- **Creator:** Peter Steinberger (Austrian developer, now at OpenAI)
- **History:** Released Nov 2025 as "Clawdbot" -> "Moltbot" -> "OpenClaw" (Jan 2026)
- **Popularity:** 247K+ GitHub stars, 47.7K forks (as of March 2026)
- **GitHub:** https://github.com/openclaw/openclaw
- **Docs:** https://docs.openclaw.ai

## Architecture

### Core Components

```
Messaging Channels (WhatsApp, Telegram, Slack, Discord, 20+ more)
                    |
    +-------------------------------+
    |   Gateway (WS Control Plane)  |  (ws://127.0.0.1:18789)
    |   - Channel connections       |
    |   - Session management        |
    |   - Event broadcasting        |
    |   - Canvas host               |
    +-------------------------------+
         |         |         |         |
      Pi Agent   CLI     macOS App   iOS/Android
     (Embedded)  Client  (Menu bar)   (Nodes)
```

1. **Gateway** (`src/gateway/`) - WebSocket control plane
   - Central hub coordinating agent runtimes, clients, tools, channels
   - Session management & persistence
   - Event broadcasting, authentication, health monitoring

2. **Agent Runtime** (`src/agents/`) - Built on Pi agent framework (`@mariozechner/pi-agent-core@0.66.1`)
   - Single embedded agent per gateway
   - Session-based conversation threading
   - Model selection & failover with auth profile rotation
   - 60+ tools (browser, canvas, web-fetch, cron, nodes, etc.)

3. **Channels** (`src/channels/`) - 20+ messaging platform integrations
   - WhatsApp (Baileys), Telegram (grammY), Slack (Bolt), Discord (discord.js)
   - Google Chat, Signal, iMessage/BlueBubbles, IRC, Teams, Matrix, LINE, etc.
   - Plugin-based architecture with mention-gating, media pipeline, reply chunking

4. **Configuration** (`src/config/`) - JSON Schema + TypeBox + Zod
   - Runtime config at `~/.openclaw/openclaw.json`
   - Hot-reload support, migration paths

5. **CLI & Commands** (`src/cli/` + `src/commands/`)
   - `openclaw gateway` - Start gateway
   - `openclaw agent` - Run agent
   - `openclaw onboard` - Setup wizard
   - `openclaw doctor` - Diagnostics

### Agent Execution Flow

```
User message (DM/channel/CLI)
  -> Gateway receives message
  -> Session resolution (main or sandboxed)
  -> Auth profile selection (OAuth/API key rotation)
  -> Model selection & resolution
  -> Pi Agent core execution:
     - Load session transcript
     - Inject bootstrap files (AGENTS.md, SOUL.md, TOOLS.md, etc.)
     - Build prompt with context
     - Stream LLM response
     - Execute tools, handle results
  -> Session persistence (JSONL transcript)
  -> Reply delivery via channel plugin
```

### Key Concepts

- **Sessions**: Isolated conversation contexts with activation modes
- **Skills (ClawHub)**: Dynamic capability registry (bundled, managed, workspace tiers)
- **Nodes**: Device connections (macOS/iOS/Android) advertising local capabilities
- **Bootstrap Files**: AGENTS.md, SOUL.md, TOOLS.md, IDENTITY.md, USER.md injected into agent context
- **Canvas**: HTML/CSS/JS agent-editable workspace for rich outputs

### LLM Providers

OpenAI, Anthropic (Claude), Google (Gemini), AWS Bedrock, Mistral, Moonshot, OpenRouter, and more. Supports provider/model refs with fallback chains.

## Tool Implementations

OpenClaw registers 15+ core tools via `createOpenClawTools()` in `src/agents/openclaw-tools.ts`. Each tool is an `AnyAgentTool` with a schema, label, and execution function.

### Web Search

**Files:** `src/agents/tools/web-search.ts`, `src/web-search/runtime.ts`, `src/plugins/web-search-providers.runtime.ts`

**How it works:**
- Tool name: `web_search`
- Delegates to a **pluggable search provider** — not tied to any single API
- Available providers: Brave, Tavily, DuckDuckGo, Moonshot, Perplexity, xAI, Exa, Firecrawl, Google
- Each provider loaded via plugin system with contract tests

**Provider resolution:**
1. Explicit config (user chose a provider)
2. Runtime-selected (provider with valid credentials)
3. Auto-detected (checks which API keys are present)
4. Fallback to keyless providers (DuckDuckGo)

This means web search works out of the box with zero config (DuckDuckGo fallback) but improves with API keys.

**AGClaw comparison:** We currently use Gemini's native `GoogleSearch` grounding, which is simpler but provider-specific. A pluggable provider system like OpenClaw's would make search provider-agnostic.

### Web Fetch

**Files:** `src/agents/tools/web-fetch.ts`, `src/agents/tools/web-fetch-utils.ts`, `src/agents/tools/web-guarded-fetch.ts`

**How it works:**
- Tool name: `web_fetch`
- Parameters: `url`, `extractMode` ("markdown" or "text"), `maxChars`
- Multi-layered content extraction:

**Extraction pipeline:**
1. Fetch URL with SSRF protection (`fetchWithSsrFGuard()` — blocks private network access)
2. Content-type routing:
   - `text/html` → Readability extraction
   - `text/markdown` → use directly (Cloudflare pre-rendered)
   - `application/json` → pretty-print
   - Other → raw text
3. HTML extraction via `@mozilla/readability` + `linkedom` (DOM parser)
   - Safety limits: max 1MB HTML, max 3000 nesting depth (DoS protection)
   - Falls back to regex-based tag stripping if Readability fails
4. Custom `htmlToMarkdown()` — converts `<a>`, `<h1-6>`, `<li>` etc. to markdown
5. Output truncated to `maxChars`, wrapped in content markers

**Caching:** 60-minute TTL (configurable). **Timeout:** 30 seconds.

**AGClaw comparison:** We currently use Gemini's native `UrlContext` tool. OpenClaw's approach gives more control (extract modes, caching, SSRF protection) but requires more code.

### Browser Control

**Files:** `src/agents/tools/canvas-tool.ts`, `src/agents/sandbox/browser.ts`, `src/plugin-sdk/browser-*.ts` (20+ files)

**How it works:**
- Browser runs in a **Docker container** (sandboxed), not the local machine
- Uses `playwright-core@1.59.1` via Chrome DevTools Protocol (CDP)
- CDP connection to containerized Chromium on configurable port range

**Tool interface (canvas-tool):**
- `canvas.navigate` — navigate to URL
- `canvas.snapshot` — take screenshot (PNG/JPEG)
- `canvas.eval` — execute JavaScript, return result
- `canvas.present` — display canvas at coordinates
- `canvas.hide` — hide canvas

**Browser lifecycle:**
- `ensureSandboxBrowser()` manages Docker container start/stop
- Hot browser window: 5 minutes (reuses existing browser instance for speed)
- NoVNC support for remote browser observation
- Browser profiles with separate CDP ports

**Docker integration:**
- Isolated network (bridge or custom)
- Configurable headless/headed modes
- Environment variable sanitization

**AGClaw comparison:** We don't have browser control yet. When we add it, we should consider a similar Docker-sandboxed approach for security. The CDP/playwright pattern is proven.

### Code Execution

**Files:** `src/agents/bash-tools.process.ts`, `src/agents/bash-process-registry.ts`, `src/agents/bash-tools.schemas.ts`

**How it works:**
- Tool name: `process`
- **Not sandboxed by default** — runs locally via `getProcessSupervisor()`
- Optional Docker sandbox when `sandboxRoot` + `sandboxFsBridge` provided

**Process management:**
- Actions: `list`, `poll`, `log`, `write`, `send-keys`, `submit`, `paste`, `kill`, `clear`, `remove`
- Each command runs in a PTY session (`@lydell/node-pty`) for terminal emulation
- Sessions tracked in a registry with PID, command, working directory, TTY buffer, exit code

**Interactive support:**
- Send keystrokes (`send-keys`), paste text (`paste`), write stdin (`write`)
- ANSI escape handling with bracketed paste mode
- Live output polling with configurable tail (default: 200 lines)

**Safety:**
- Environment variable sanitization via `sanitizeEnvVars()`
- Process tree killing on cleanup (`killProcessTree()`)
- Path safety checks via `SandboxFsBridge` when sandboxed
- Max poll wait: 120 seconds

**AGClaw comparison:** We currently use Gemini's native `ToolCodeExecution` (runs in Gemini's cloud sandbox). OpenClaw's approach gives local execution with interactive PTY support — more powerful but less secure without Docker. For AGClaw, we should support both: Gemini-native for simple code, and a local/Docker sandbox for complex interactive tasks.

### Tool Composition Summary

All tools registered via `createOpenClawTools(options)`:

| Tool | Name | Key Libraries |
|------|------|---------------|
| Web Search | `web_search` | Pluggable providers (Brave, Tavily, DuckDuckGo, etc.) |
| Web Fetch | `web_fetch` | `@mozilla/readability`, `linkedom`, SSRF guard |
| Browser | canvas actions | `playwright-core`, Docker, CDP |
| Code Execution | `process` | `@lydell/node-pty`, process supervisor |
| Canvas | `canvas` | HTML/CSS/JS workspace |
| Message | `message` | Send to any connected channel |
| Cron | `cron` | Scheduled tasks |
| Nodes | `nodes` | Device hardware (camera, mic, screen) |
| TTS | `tts` | `node-edge-tts`, `opusscript` |
| Image Gen | `image_generation` | Provider-based |
| PDF | `pdf` | `pdfjs-dist` |
| Sessions | `sessions_*` | List, history, spawn, yield |
| Subagents | `subagents` | Delegate to sub-conversations |
| Gateway | `gateway` | Call external agents |

## Benchmarks

1. **PinchBench** (kilo.ai) - Real tasks: scheduling, coding, email triage, research, file management
2. **WildClawBench** (InternLM) - 60 hand-crafted in-the-wild tasks in Docker containers
3. **EvoClaw** - Continuous development scenarios (success drops from >80% to ~13%)

## Key Dependencies

- `@mariozechner/pi-agent-core` - Agent runtime
- `openai`, `@anthropic-ai/vertex-sdk`, `@google/genai` - LLM clients
- `ws`, `express`, `hono` - Gateway/HTTP
- `@modelcontextprotocol/sdk` - MCP support
- `playwright-core` - Browser control
- `sqlite-vec`, `@lancedb/lancedb` - Storage/vectors
- Node 22.16+, pnpm, TypeScript 6+

## Security Concerns

- Unvetted third-party skills can perform data exfiltration and prompt injection (Cisco research)
- Notable incident: agent creating dating profiles without authorization
- Chinese authorities restricted OpenClaw on government computers (March 2026)

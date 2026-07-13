# AG2 Assistant

An open-source personal AI assistant powered by [AG2](https://ag2.ai)'s Beta framework, built in Python.

> Public product name: **AG2 Assistant**. `ag2-assistant` remains the internal package, CLI command, and data-dir name.

AG2 Assistant is a web app (with optional messaging-channel and CLI front-ends) that acts as your personal AI agent — it searches the web, runs code, generates images, reads your project files, and runs scheduled tasks, all while showing its work in the open. Every reply is a projection of the underlying AG2 event stream.

## Quick Start

### 1. Install

Clone for a development checkout:

```bash
git clone https://github.com/ag2ai/ag2-assistant.git
cd ag2-assistant
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Or just install the CLI globally, no clone (installed from git — not yet on PyPI):

```bash
uv tool install "git+https://github.com/ag2ai/ag2-assistant.git"   # or: pipx install "git+https://github.com/ag2ai/ag2-assistant.git"
```

Prefer to run it as an always-on container? See [Deployment](#deployment).

### 2. Run

```bash
ag2-assistant run        # gateway + web UI (+ any configured channels), one process
```

Then open **http://localhost:8800/** and complete the first-run onboarding: choose a theme, paste a provider API key (Gemini by default — get one at [aistudio.google.com](https://aistudio.google.com/apikey)), and pick a project folder the assistant can read. Everything else is configurable later in **Settings**.

> Prefer to set the key up front? Create a `.env` with `GEMINI_API_KEY=your-key` before running. Keys can also come from the environment; onboarding only needs to gate on first run.

To serve the API/UI without any messaging channels, use `ag2-assistant gateway` instead of `run`.

## The web app

The primary interface is the Svelte web UI served at `/` (→ `/app`). It includes:

- **Chat** — multi-turn conversations with live, streamed agent events (tool calls, code runs, web searches) rendered inline.
- **Steer or stop a turn while it runs** — send a message mid-turn and the agent folds it into the work it's already doing or hit **Stop** to end the turn — keeping whatever it produced up to that point, so the conversation carries on with that context.
- **Tasks** — one-shot and recurring scheduled tasks with deliverables, timestamps, re-run, and cancel/archive.
- **Image generation** — generated images are saved to the shared workspace and shown as clickable inline thumbnails.
- **Memory** — the assistant passively learns your preferences; 👍/👎 feedback (with a reason) feeds a memory-aware learner that dedupes and prunes conflicting notes.
- **Project folder** — a read-only `repo-files` MCP scoped to a folder you choose, so the assistant can read your code/notes (browse + search, never write).
- **Voice** — talk to the assistant (Gemini Live / OpenAI realtime) over a browser audio bridge.
- **Settings** — API keys, model, voice, MCP servers, Google connection, project folder, and re-run setup.

## CLI

The web app is the main experience, but the CLI is handy for quick one-shots and scripting:

```bash
ag2-assistant run                       # everything in one process (gateway + UI + channels)
ag2-assistant gateway                   # REST + WebSocket API + web UI only
ag2-assistant chat                      # interactive multi-turn chat in the terminal
ag2-assistant agent "message"           # single-shot prompt → reply
ag2-assistant onboard                   # first-run interview (name, location, hours, style)
ag2-assistant telegram                  # run on Telegram   (needs TELEGRAM_BOT_TOKEN)
ag2-assistant discord                   # run on Discord    (needs DISCORD_BOT_TOKEN)
ag2-assistant slack                     # run on Slack      (needs SLACK_BOT_TOKEN + SLACK_APP_TOKEN)
ag2-assistant version                   # show version
```

### Examples

```bash
ag2-assistant agent "What's the current AG2 version? Search the web."
ag2-assistant agent "Calculate the first 20 Fibonacci numbers"
ag2-assistant agent "Compare FastAPI vs Flask. Search the web for current benchmarks."
```

## Deployment

The Quick Start above is the developer setup. For other ways to run it — see
[docs/deployment.md](docs/deployment.md) for the full guide:

| Tier | For | How |
|------|-----|-----|
| **Contributor** | hacking on the code | `git clone` + `pip install -e ".[dev]"` |
| **CLI user** | running locally, no clone | `uv tool install "git+https://github.com/ag2ai/ag2-assistant.git"` |
| **Self-hosted** | an always-on instance | `docker compose up -d` |
| **PyPI** | `pip install ag2-assistant` | not yet (blocked on AG2's PyPI release; targeted for 1.0) |

### Docker (self-hosted)

```bash
cp .env.example .env         # add a provider key (e.g. GEMINI_API_KEY) — optional
docker compose up -d         # build + run; open http://localhost:8800/
```

State and the agent's file workspace persist in named volumes. Tagged releases also publish
a prebuilt image to `ghcr.io/ag2ai/ag2-assistant`. Code execution runs in-container by
default (no host Docker socket); see [docs/deployment.md](docs/deployment.md) for the
docker-out-of-docker option and channel setup.

> The gateway has no built-in auth. Only expose port 8800 beyond localhost behind a reverse
> proxy that handles TLS and authentication.

## Running Tests

```bash
# Unit tests (no API key needed)
pytest tests/ -v -m "not integration"

# All tests (requires GEMINI_API_KEY)
pytest tests/ -v
```

## Architecture

**[docs/architecture.md](docs/architecture.md)** is the full system design — every service, agent, endpoint, event type, data flow, and on-disk store — with a companion diagram, [docs/architecture.svg](docs/architecture.svg):

[![AG2 Assistant architecture](docs/architecture.svg)](docs/architecture.md)

A text sketch of the same shape:

```
  Web UI (Svelte)   ·   Messaging channels   ·   CLI
         |  REST + WebSocket (event stream)        |
    +-----------+
    |  Gateway  |  (FastAPI: /api/message, /api/stream, /api/health, /app)
    +-----------+
         |  per-session isolated multi-turn; events streamed to every client
    +-----------+
    |   Agent   |  (AG2 + Gemini / OpenAI / Anthropic / Ollama)
    |   Tools   |  (web search, shell, code exec, web fetch, image gen,
    |           |   tasks/scheduling, repo-files MCP, Google, skills, MCP servers)
    +-----------+
         |
    +-----------+
    |  Memory   |  (preferences learned passively + from 👍/👎 feedback → SQLite)
    +-----------+
```

State lives under `~/.ag2assistant/` (settings, sessions, memory, tasks); generated files live in the workspace (`~/Documents/AG2 Assistant/` by default).

## Project Status

- [x] Core agent with multi-provider support (Gemini, OpenAI, Anthropic, Ollama)
- [x] CLI interface (agent, chat, onboard, run)
- [x] Tools (native AG2: web search, shell, code execution; + web fetch, image generation)
- [x] Memory — passively learns preferences and from 👍/👎 feedback; persists across sessions
- [x] Multi-turn conversations (per-session isolation)
- [x] Gateway (REST + WebSocket event-stream API)
- [x] Web UI — chat, tasks/scheduling, image gen, voice, memory, onboarding, settings
- [x] Tasks & scheduling with deliverables (one-shot + recurring)
- [x] Voice (Gemini Live / OpenAI realtime over a browser audio bridge)
- [x] Project folder — read-only `repo-files` MCP; user-extensible MCP servers
- [x] Channels: Telegram, Discord, Slack (DM + group @mention gating)
- [x] Skills — searches & installs from the skills.sh registry, runs them
- [ ] More channels (WhatsApp)
- [ ] Desktop / mobile clients

## Documentation

- [Architecture](docs/architecture.md) — full system design: services, agents, endpoints, event model, data flow ([diagram](docs/architecture.svg))
- [Usage Guide](docs/usage.md) — CLI commands, configuration, channels
- [Deployment](docs/deployment.md) — install tiers, Docker/Compose, self-hosting

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) to get set up
and [AGENTS.md](AGENTS.md) for the development guidelines. To report a security
issue, follow [.github/SECURITY.md](.github/SECURITY.md) (please don't open a
public issue).

## License

[Apache License 2.0](LICENSE)

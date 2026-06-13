# AGClaw

An open-source personal AI assistant built on [AG2](https://ag2.ai)'s Beta framework. Inspired by [OpenClaw](https://github.com/openclaw/openclaw), reimagined in Python.

AGClaw connects to your messaging platforms (Telegram, Discord, Slack) and acts as your personal AI agent — searching the web, running code, managing tasks, and more.

## Quick Start

### 1. Install

```bash
git clone https://github.com/agclaw/agclaw.git
cd agclaw
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure

Create a `.env` file:

```env
GEMINI_API_KEY=your-gemini-api-key
```

Get a Gemini API key at [aistudio.google.com](https://aistudio.google.com/apikey).

### 3. Run

```bash
agclaw agent "Hello, what can you do?"
```

## Examples

### General conversation

```bash
agclaw agent "Explain what AG2 is in one sentence"
agclaw agent "Write a haiku about Python programming"
```

### Web search (grounded, real-time)

The agent can search the web for current information:

```bash
agclaw agent "What's the current AG2 version? Search the web."
agclaw agent "What happened in the news today?"
agclaw agent "Find the latest Python release notes"
```

### Code execution

The agent can write and run code:

```bash
agclaw agent "Calculate the first 20 Fibonacci numbers"
agclaw agent "Write a Python function that checks if a string is a palindrome, then test it"
```

### Research tasks

Combine web search with reasoning:

```bash
agclaw agent "Compare FastAPI vs Flask for building REST APIs. Search the web for current benchmarks."
agclaw agent "What are the top 3 trending Python libraries this month?"
```

### CLI

```bash
agclaw version          # Show version
agclaw agent "message"  # Talk to your agent
```

## Running Tests

```bash
# Unit tests
pytest tests/ -v -m "not integration"

# All tests (requires GEMINI_API_KEY)
pytest tests/ -v
```

## Architecture

See [docs/architecture.md](docs/architecture.md) and [docs/architecture.svg](docs/architecture.svg) for the full system design.

```
  UI clients (web / desktop / mobile / CLI)
         |  REST + WebSocket
    +---------+
    | Gateway |  (FastAPI: /api/message, /api/ws, /api/health)
    +---------+
         |  per-session isolated multi-turn
    +---------+
    |  Agent  |  (AG2 Beta + Gemini)
    |  Tools  |  (Web Search, Shell, Code Exec, Web Fetch)
    +---------+
         |
    +---------+
    | Memory  |  (profile learned passively → SQLite)
    +---------+
```

## Project Status

AGClaw is in early development. See [docs/plan.md](docs/plan.md) for the full roadmap.

- [x] Core agent with Gemini integration
- [x] CLI interface
- [x] Tools (native AG2: web search, shell, code execution; + web fetch)
- [x] Observer memory (passively learns your preferences, persists across sessions)
- [x] Multi-turn conversations (per-session isolation)
- [x] Gateway (REST + WebSocket API; distributed Hub spike)
- [x] Channels: Telegram, Discord, Slack (DM + group @mention gating)
- [x] Skills — searches & installs from the skills.sh registry, runs them
- [ ] More channels (WhatsApp), web UI
- [ ] Memory & context management
- [ ] Web UI

## Documentation

- [Architecture](docs/architecture.md) — system design, concurrency model, message flows
- [Usage Guide](docs/usage.md) — CLI commands, configuration, channels
- [Plan](docs/plan.md) — implementation roadmap with progress tracking
- [OpenClaw Research](docs/research-openclaw.md) — analysis of the project that inspired AGClaw
- [AG2 Beta Research](docs/research-ag2-beta.md) — AG2 Beta capabilities and availability

## License

MIT

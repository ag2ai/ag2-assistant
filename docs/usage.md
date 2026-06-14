# AGClaw Usage Guide

## Quick Start

### 1. Install

```bash
pip install agclaw
```

Or from source:

```bash
git clone https://github.com/agclaw/agclaw.git
cd agclaw
pip install -e ".[dev]"
```

### 2. Configure

Create a `.env` file in your project root:

```env
GEMINI_API_KEY=your-key-here
```

Other providers:

```env
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Talk to your agent

```bash
agclaw agent "What's on my calendar today?"
```

## CLI Commands

### `agclaw agent <message>`

Send a message to your personal AI agent.

```bash
agclaw agent "Summarize the latest news about AI"
agclaw agent "Write a Python function that sorts a list"
agclaw agent "What did I ask you about yesterday?"
```

### `agclaw chat`

Start an interactive, multi-turn conversation in your terminal (the single-shot
`agclaw agent` is better for scripting/one-offs).

```bash
agclaw chat                     # talk back and forth; type 'exit' or Ctrl-D to quit
agclaw chat --sandbox docker    # run shell/code in a container during the chat
agclaw chat --no-memory         # don't learn from this session
```

It keeps one session, so AGClaw remembers earlier turns, asks permissions via the
desktop popup, and learns your profile as you go.

### `agclaw version`

Show the installed version.

```bash
agclaw version
# agclaw 0.1.0
```

### `agclaw gateway`

Start the AGClaw gateway — a REST + WebSocket API any UI client (web, desktop, mobile) can drive.

```bash
agclaw gateway                 # http://127.0.0.1:8800
agclaw gateway --port 9000 --no-memory
```

**Built-in web UI.** Open `http://127.0.0.1:8800/` in a browser for a ready-made
chat client (also served by `agclaw run`). It's a single self-contained page
(vanilla JS over the WebSocket) styled to match ag2.ai: streaming replies, and
permission/HITL prompts render inline as cards you click to answer. Use it as-is
or as a reference for building your own front-end against the API below.

Endpoints:

```
GET  /api/health                      -> {status, model, sessions, ...}
GET  /api/hitl/pending                -> {pending: [{id, text, options, path, ...}]}
POST /api/message   {text, session_id} -> {reply, session_id}
POST /hitl/{id}/answer  {answer}       -> {ok: true}   (answer a permission prompt)
GET  /hitl/{id}                        -> styled HTML question page
WS   /api/ws                           -> send {text, session_id};
                                          receive {type: thinking|question|reply|error};
                                          answer a question with {type:"answer", id, answer}
```

**Human-in-the-loop on the gateway.** When the agent needs permission (or asks a
question) mid-turn, the gateway surfaces it instead of blocking silently:
- **WebSocket clients** receive a `{type:"question", id, text, options, path}` frame
  and answer over the same socket with `{type:"answer", id, answer}` — the turn
  resumes and streams its `reply`.
- **REST clients** can poll `GET /api/hitl/pending` and `POST /hitl/{id}/answer`,
  or just open the styled `GET /hitl/{id}` page in a browser.

Unanswered prompts time out (default 5 min) and are treated as a denial, so a turn
never hangs forever.

Example:

```bash
curl -X POST http://127.0.0.1:8800/api/message \
  -H 'Content-Type: application/json' \
  -d '{"text":"What is the capital of Japan?","session_id":"u1"}'
# {"reply":"The capital of Japan is Tokyo.","session_id":"u1"}
```

Each `session_id` keeps its own isolated multi-turn conversation. For
distributed/multi-agent deployments, the agent can also be served over WebSocket
through an AG2 Hub — see `examples/network_gateway_spike.py`.

### `agclaw run`

Run **everything in one process** — the REST/WebSocket gateway plus every channel
whose token is configured (Telegram/Discord/Slack), all sharing one agent and one
learned profile.

```bash
agclaw run                      # REST/WS on :8800 + all configured channels
agclaw run --no-rest            # channels only, no HTTP API
agclaw run --port 9000          # REST/WS on a different port
agclaw run --sandbox docker     # run shell/code in an isolated container
```

AGClaw starts only the channels it finds tokens for, so set whichever of
`TELEGRAM_BOT_TOKEN` / `DISCORD_BOT_TOKEN` / `SLACK_BOT_TOKEN`+`SLACK_APP_TOKEN`
you want live. Press Ctrl+C to stop everything cleanly.

### `agclaw onboard`

Run the first-run interview — AGClaw asks your name, location, working hours, and
preferred answer style (all skippable), then seeds its profile and `AGCLAW_LOCATION`
so it starts out knowing the basics.

```bash
agclaw onboard            # ask the questions (desktop popup)
agclaw onboard --force    # re-run even if already onboarded
```

This runs **automatically the first time** you talk to AGClaw (on the CLI or any
channel) when there's no profile yet — answered through the same surface you're on
(the desktop popup, or buttons/free-text in your chat). It's asked once; the marker
lives at `~/.agclaw/onboarded`.

### `agclaw setup` (coming soon)

Interactive setup wizard for first-time configuration.

```bash
agclaw setup
# ? Which LLM provider? (gemini / openai / anthropic)
# ? API key: ****
# ? Enable Telegram? (y/n)
# ? Telegram bot token: ****
# Configuration saved to ~/.agclaw/config.json
```

### `agclaw status` (coming soon)

Check the status of the gateway, connected channels, and active sessions.

```bash
agclaw status
# Gateway: running (ws://127.0.0.1:8789)
# Channels:
#   telegram: connected (2 active sessions)
#   discord:  connected (1 active session)
#   slack:    not configured
# Agent: idle
# Uptime: 3h 42m
```

## Messaging Channels

AGClaw connects to your favorite messaging platforms. Your agent responds to direct messages and can be mentioned in group chats.

### Attachments

Drop a file into any chat (Telegram, Discord, or Slack) and AGClaw reads it:
images and PDFs are understood visually, audio/video go to the model natively
(provider permitting), and text/code files are inlined. Add a caption to ask
something specific ("summarise this PDF"), or send the file on its own. Slack
needs the `files:read` scope for this to work.

### Telegram

1. Create a bot via [@BotFather](https://t.me/BotFather) (`/newbot`) and copy the token.
2. Add it to your `.env`:

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
```

3. Run AGClaw on Telegram:

```bash
agclaw telegram
# AGClaw is live on Telegram. Press Ctrl+C to stop.
```

4. Message your bot on Telegram.

**In DMs:** the agent responds to every message.

**In groups:** the agent only responds when you `@mention` it (or reply to one of its
messages). Add the bot to a group, then `@youragent what's the weather?`.

Each chat is its own isolated conversation, and AGClaw tags what it learns about
you with the `telegram` platform.

### Discord

1. Create an application + bot at the [Discord Developer Portal](https://discord.com/developers/applications).
2. Under **Bot**, enable the **Message Content Intent** (required to read message text).
3. Copy the bot token into `.env`:

```env
DISCORD_BOT_TOKEN=your-token
```

4. Invite the bot to your server (OAuth2 → URL Generator → scopes `bot`, permissions: Send Messages, Read Message History).
5. Run it:

```bash
agclaw discord
# AGClaw is live on Discord. Press Ctrl+C to stop.
```

**In DMs:** responds to every message.

**In servers:** responds only when you `@mention` it. Discord renders Markdown,
so replies keep their formatting; long replies are split across messages.

### Slack

AGClaw uses **Socket Mode**, so no public URL is needed.

1. Create a Slack app at [api.slack.com/apps](https://api.slack.com/apps) → **From scratch**.
2. **Socket Mode** (left nav) → enable it. This creates an **App-Level Token** (`xapp-…`) with `connections:write` — copy it.
3. **OAuth & Permissions** → add Bot Token Scopes: `app_mentions:read`, `chat:write`, `im:history`, `im:read`, `reactions:write`. Install to your workspace and copy the **Bot User OAuth Token** (`xoxb-…`).
4. **Event Subscriptions** → enable → **Subscribe to bot events**: `app_mention` and `message.im`. Save.
4b. **App Home** → enable the **Messages Tab** and check "Allow users to send messages" (otherwise DMs are turned off).

> After changing scopes/events you must **reinstall** the app (Slack shows a banner). The bot token may change on reinstall — update `.env` if so.
5. Add both tokens to `.env`:

```env
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

6. Run it:

```bash
agclaw slack
# AGClaw is live on Slack. Press Ctrl+C to stop.
```

**In DMs:** responds to every message.

**In channels:** responds only when you `@mention` it (invite the bot to the channel first). Replies use Slack mrkdwn formatting.

While the agent is working it adds a 👀 reaction to your message, and removes it once it replies.

### WhatsApp (coming later)

WhatsApp integration via webhook/API. Details TBD.

## Configuration

### Environment context (date, time, location)

AGClaw automatically knows the **current date and time** from your system clock
(refreshed every turn). To also tell it **where you are**, set a location in `.env`:

```env
AGCLAW_LOCATION=Sydney, Australia
```

Now it can answer "what's the time here?" and reason about your local context.

### Config file & precedence

Configuration resolves in this order (later wins):

1. Built-in defaults
2. `~/.agclaw/config.json`
3. `AGCLAW_*` environment variables (from `.env` or the shell)

So you can keep a base `config.json` and override per-run with an env var.

### `~/.agclaw/config.json`

```json
{
  "llm": {
    "provider": "gemini",
    "model": "gemini-3.5-flash",
    "api_key_env": "GEMINI_API_KEY",
    "aggregate_model": "gemini-2.5-flash"
  },
  "agent": {
    "name": "agclaw",
    "system_prompt": "You are AGClaw, a helpful personal AI assistant."
  },
  "tools": { "sandbox": "local" },
  "memory": { "aggregate_every_n_turns": 4 }
}
```

`aggregate_model` (optional) runs the passive memory-distillation pass on a
cheaper model than your main one — handy on long sessions. Omit it to reuse the
main model.

### Environment variable overrides

Every key above also has an env override (these win over `config.json`):

| Env var | Overrides |
|---|---|
| `AGCLAW_LLM_PROVIDER` | `llm.provider` (`gemini` / `anthropic` / `openai`) |
| `AGCLAW_MODEL` | `llm.model` |
| `AGCLAW_API_KEY_ENV` | `llm.api_key_env` |
| `AGCLAW_AGGREGATE_MODEL` | `llm.aggregate_model` |
| `AGCLAW_LOCATION` | `agent.location` |
| `AGCLAW_SANDBOX` | `tools.sandbox` (`local` / `docker`) |
| `AGCLAW_DOCKER_IMAGE` / `AGCLAW_DOCKER_NETWORK` | Docker sandbox |
| `AGCLAW_AGGREGATE_EVERY_N` | `memory.aggregate_every_n_turns` |

### Switching LLM providers

Set the provider + model + the env var holding its key, and install that
provider's extra (`pip install "ag2[anthropic]"` / `ag2[openai]`):

```json
{ "llm": { "provider": "openai", "model": "gpt-4o", "api_key_env": "OPENAI_API_KEY" } }
```
```json
{ "llm": { "provider": "anthropic", "model": "claude-sonnet-4-6", "api_key_env": "ANTHROPIC_API_KEY" } }
```

Or quickly, without a file:

```bash
AGCLAW_LLM_PROVIDER=anthropic AGCLAW_MODEL=claude-sonnet-4-6 \
  AGCLAW_API_KEY_ENV=ANTHROPIC_API_KEY agclaw chat
```

## Personalizing Your Agent

### System prompt

Customize your agent's personality by editing the system prompt in `~/.agclaw/config.json`:

```json
{
  "agent": {
    "system_prompt": "You are Jarvis, a witty personal assistant. You speak concisely and always suggest actionable next steps."
  }
}
```

### Agent name

```json
{
  "agent": {
    "name": "jarvis"
  }
}
```

## Skills — AGClaw can extend itself

AGClaw can search a public skills registry ([skills.sh](https://skills.sh)),
install skills, and use them — all on its own, mid-conversation. A skill is a
`SKILL.md` instruction package (optionally with scripts/resources).

**Bundled skills.** AGClaw ships with a few first-party skills available from the
first run (no install needed): `web-research` (thorough multi-source research),
`pdf-tools` (read/extract/split/merge PDFs), and `email-drafting` (drafts in your
voice). It uses them automatically when relevant, or you can ask for one by name.

Just ask:

```
"Find a skill for working with PDFs, install it, and use it to summarise report.pdf."
"What skills do you have installed?"
```

The agent searches, installs to `~/.agclaw/skills`, and loads instructions only
when needed (progressive disclosure, so it stays token-cheap). Optional: set
`GITHUB_TOKEN` in `.env` for higher registry rate limits.

> Skills can ship runnable scripts. AGClaw blocks obviously dangerous commands,
> and with `--sandbox docker` each skill script runs inside a one-shot container
> that can see **only that skill's own folder** — not your files. Without Docker,
> scripts run on the host (blocklist only), so install skills you trust.

## Memory — AGClaw learns about you

AGClaw passively builds a profile of how you like to work and remembers it across conversations and platforms. It tracks how you like things done, when you like them done, what you dislike, and how you write.

```bash
# Just talk — it learns passively
agclaw agent "I prefer short bulleted answers and I dislike emojis at work"

# See what it's learned
agclaw profile show

# Clear it
agclaw profile clear

# One-off without memory
agclaw agent "..." --no-memory
```

The profile is stored locally at `~/.agclaw/profile.db`. See [memory.md](memory.md) for the full design.

AGClaw distils the profile every few turns (an LLM call each time) rather than after
every message, so long chats stay cheap. Single-shot `agclaw agent` runs always
distil their one turn. Tune the cadence with `AGCLAW_AGGREGATE_EVERY_N` (default 4).

## Permissions — control what AGClaw can access

The first time AGClaw wants to read a folder (or run a shell/code command), it asks
you: **Allow once / Always allow / Deny** — as buttons in chat, or a styled page
on the desktop. "Always allow" is remembered; denials apply for that turn only.

Manage grants from the CLI:

```bash
agclaw permissions list                       # show allowed + blocked folders
agclaw permissions allow ~/Documents          # pre-grant a folder
agclaw permissions revoke ~/Documents         # undo a grant
agclaw permissions block ~/private            # permanently deny (never asks)
agclaw permissions unblock ~/private          # remove the block
```

Grants/blocks persist in `~/.agclaw/permissions.json`.

## Sandbox — where shell/code runs

AGClaw can run shell commands and execute code. Two backends:

| Backend | Isolation | Prompting |
|---|---|---|
| `local` (default) | Runs on your host; a blocklist filters obvious damage | Asks approval (Allow once / Always / Deny) before each command |
| `docker` | Runs in a throwaway container with **no access to your files** | No prompt — the container is the boundary |

```bash
agclaw agent "run my tests" --sandbox docker
agclaw run --sandbox docker
# or set it once:
echo "AGCLAW_SANDBOX=docker" >> .env
```

The Docker backend starts a container per session (default image
`python:3.12-slim`), runs every shell/code call inside it, and removes it on exit —
nothing the model runs can touch your filesystem. Tune it via `.env`:

```env
AGCLAW_SANDBOX=docker
AGCLAW_DOCKER_IMAGE=python:3.12-slim
AGCLAW_DOCKER_NETWORK=bridge   # "none" for the strictest (offline) isolation
```

If `--sandbox docker` is requested but Docker isn't running, AGClaw falls back to
the local backend (with approval prompts) and warns. `read_file` always stays
permission-gated regardless of backend, since it reads your **host** files.

## Sessions

AGClaw maintains conversation history per user per channel. When you message your agent on Telegram, it remembers your previous conversations on Telegram. Discord conversations are separate sessions.

Sessions are persisted locally in `~/.agclaw/sessions/`.

## How It Works

1. You send a message on any connected platform (or CLI)
2. The channel adapter normalizes it to a common format
3. The gateway routes it to your session
4. The AG2 agent processes it with your conversation history
5. The response is sent back through the same channel

Your agent runs locally on your machine. Your messages and data stay on your device. API keys are used only to call the LLM provider.

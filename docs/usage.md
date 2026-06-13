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

Endpoints:

```
GET  /api/health                      -> {status, model, sessions, ...}
POST /api/message   {text, session_id} -> {reply, session_id}
WS   /api/ws                           -> send {text, session_id};
                                          receive {type: thinking|reply|error}
```

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

### Config file

AGClaw looks for configuration in:

1. `.env` file in the current directory
2. `~/.agclaw/config.json`
3. Environment variables

### `~/.agclaw/config.json`

```json
{
  "llm": {
    "provider": "gemini",
    "model": "gemini-2.5-flash",
    "api_key_env": "GEMINI_API_KEY"
  },
  "agent": {
    "name": "agclaw",
    "system_prompt": "You are AGClaw, a helpful personal AI assistant."
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "bot_token_env": "TELEGRAM_BOT_TOKEN"
    },
    "discord": {
      "enabled": false,
      "bot_token_env": "DISCORD_BOT_TOKEN"
    }
  },
  "gateway": {
    "host": "127.0.0.1",
    "port": 8789
  }
}
```

### Switching LLM providers

```json
{
  "llm": {
    "provider": "openai",
    "model": "gpt-4o",
    "api_key_env": "OPENAI_API_KEY"
  }
}
```

```json
{
  "llm": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-20250514",
    "api_key_env": "ANTHROPIC_API_KEY"
  }
}
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

Just ask:

```
"Find a skill for working with PDFs, install it, and use it to summarise report.pdf."
"What skills do you have installed?"
```

The agent searches, installs to `~/.agclaw/skills`, and loads instructions only
when needed (progressive disclosure, so it stays token-cheap). Optional: set
`GITHUB_TOKEN` in `.env` for higher registry rate limits.

> Skills can ship runnable scripts. AGClaw blocks obviously dangerous commands,
> but only install skills you trust. Stronger sandboxing (Docker) is planned.

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

# AG2 Assistant Usage Guide

## Quick Start

### 1. Install

```bash
pip install ag2assistant
```

Or from source:

```bash
git clone https://github.com/marklysze/ag2-assistant.git
cd ag2-assistant
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
ag2assistant agent "What's on my calendar today?"
```

## CLI Commands

### `ag2assistant agent <message>`

Send a message to your personal AI agent.

```bash
ag2assistant agent "Summarize the latest news about AI"
ag2assistant agent "Write a Python function that sorts a list"
ag2assistant agent "What did I ask you about yesterday?"
```

### `ag2assistant chat`

Start an interactive, multi-turn conversation in your terminal (the single-shot
`ag2assistant agent` is better for scripting/one-offs).

```bash
ag2assistant chat                     # talk back and forth; type 'exit' or Ctrl-D to quit
ag2assistant chat --sandbox docker    # run shell/code in a container during the chat
ag2assistant chat --no-memory         # don't learn from this session
```

It keeps one session, so AG2 Assistant remembers earlier turns, asks permissions via the
desktop popup, and learns your profile as you go.

### `ag2assistant version`

Show the installed version.

```bash
ag2assistant version
# ag2assistant 0.1.0
```

### `ag2assistant gateway`

Start the AG2 Assistant gateway — a REST + WebSocket API any UI client (web, desktop, mobile) can drive.

```bash
ag2assistant gateway                 # http://127.0.0.1:8800
ag2assistant gateway --port 9000 --no-memory
```

**Built-in web UI.** Open `http://127.0.0.1:8800/` in a browser for a ready-made
chat client (also served by `ag2assistant run`). It's a single self-contained page
(vanilla JS over the WebSocket) styled to match ag2.ai: streaming markdown replies,
file attachments, a stop button, light/dark following your system, and
permission/HITL prompts rendered inline as cards. The **History** button lists
past conversations and lets you resume any of them. Use it as-is or as a reference
for building your own front-end against the API below.

**Resumable conversations.** Each session's full event history is persisted to
`~/.ag2assistant/sessions.db` after every turn (via AG2's event log) and reloaded into a
fresh stream on demand — so conversations survive a gateway restart with complete
context, not just a text transcript. This applies to **every** surface (web and
all chat channels), keyed by session id.

Endpoints:

```
GET  /api/health                      -> {status, model, sessions, ...}
GET  /api/sessions                    -> {sessions: [{session_id, updated, preview, turns}]}
GET  /api/sessions/{id}               -> {session_id, messages: [{role, text}]}
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

### `ag2assistant run`

Run **everything in one process** — the REST/WebSocket gateway plus every channel
whose token is configured (Telegram/Discord/Slack), all sharing one agent and one
learned profile.

```bash
ag2assistant run                      # REST/WS on :8800 + all configured channels
ag2assistant run --no-rest            # channels only, no HTTP API
ag2assistant run --port 9000          # REST/WS on a different port
ag2assistant run --sandbox docker     # run shell/code in an isolated container
```

AG2 Assistant starts only the channels it finds tokens for, so set whichever of
`TELEGRAM_BOT_TOKEN` / `DISCORD_BOT_TOKEN` / `SLACK_BOT_TOKEN`+`SLACK_APP_TOKEN`
you want live. Press Ctrl+C to stop everything cleanly.

### `ag2assistant onboard`

Run the first-run interview — AG2 Assistant asks your name, location, working hours, and
preferred answer style (all skippable), then seeds its profile and `AG2ASSISTANT_LOCATION`
so it starts out knowing the basics.

```bash
ag2assistant onboard            # ask the questions (desktop popup)
ag2assistant onboard --force    # re-run even if already onboarded
```

This runs **automatically the first time** you talk to AG2 Assistant (on the CLI or any
channel) when there's no profile yet — answered through the same surface you're on
(the desktop popup, or buttons/free-text in your chat). It's asked once; the marker
lives at `~/.ag2assistant/onboarded`.

### `ag2assistant setup` (coming soon)

Interactive setup wizard for first-time configuration.

```bash
ag2assistant setup
# ? Which LLM provider? (gemini / openai / anthropic)
# ? API key: ****
# ? Enable Telegram? (y/n)
# ? Telegram bot token: ****
# Configuration saved to ~/.ag2assistant/config.json
```

### `ag2assistant status` (coming soon)

Check the status of the gateway, connected channels, and active sessions.

```bash
ag2assistant status
# Gateway: running (ws://127.0.0.1:8789)
# Channels:
#   telegram: connected (2 active sessions)
#   discord:  connected (1 active session)
#   slack:    not configured
# Agent: idle
# Uptime: 3h 42m
```

## Messaging Channels

AG2 Assistant connects to your favorite messaging platforms. Your agent responds to direct messages and can be mentioned in group chats.

### Attachments

Drop a file into any chat (Telegram, Discord, or Slack) and AG2 Assistant reads it:
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

3. Run AG2 Assistant on Telegram:

```bash
ag2assistant telegram
# AG2 Assistant is live on Telegram. Press Ctrl+C to stop.
```

4. Message your bot on Telegram.

**In DMs:** the agent responds to every message.

**In groups:** the agent only responds when you `@mention` it (or reply to one of its
messages). Add the bot to a group, then `@youragent what's the weather?`.

Each chat is its own isolated conversation, and AG2 Assistant tags what it learns about
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
ag2assistant discord
# AG2 Assistant is live on Discord. Press Ctrl+C to stop.
```

**In DMs:** responds to every message.

**In servers:** responds only when you `@mention` it. Discord renders Markdown,
so replies keep their formatting; long replies are split across messages.

### Slack

AG2 Assistant uses **Socket Mode**, so no public URL is needed.

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
ag2assistant slack
# AG2 Assistant is live on Slack. Press Ctrl+C to stop.
```

**In DMs:** responds to every message.

**In channels:** responds only when you `@mention` it (invite the bot to the channel first). Replies use Slack mrkdwn formatting.

While the agent is working it adds a 👀 reaction to your message, and removes it once it replies.

### WhatsApp (coming later)

WhatsApp integration via webhook/API. Details TBD.

## Configuration

### Environment context (date, time, location)

AG2 Assistant automatically knows the **current date and time** from your system clock
(refreshed every turn). To also tell it **where you are**, set a location in `.env`:

```env
AG2ASSISTANT_LOCATION=Sydney, Australia
```

Now it can answer "what's the time here?" and reason about your local context.

### Config file & precedence

Configuration resolves in this order (later wins):

1. Built-in defaults
2. `~/.ag2assistant/config.json`
3. `AG2ASSISTANT_*` environment variables (from `.env` or the shell)

So you can keep a base `config.json` and override per-run with an env var.

### `~/.ag2assistant/config.json`

```json
{
  "llm": {
    "provider": "gemini",
    "model": "gemini-3.5-flash",
    "api_key_env": "GEMINI_API_KEY",
    "aggregate_model": "gemini-2.5-flash"
  },
  "agent": {
    "name": "ag2assistant",
    "system_prompt": "You are AG2 Assistant, a helpful personal AI assistant."
  },
  "tools": { "sandbox": "local" },
  "memory": { "aggregate_every_n_turns": 4 }
}
```

`aggregate_model` (optional) runs the passive memory-distillation pass on a
cheaper model than your main one — handy on long sessions. On Gemini this
defaults to `gemini-2.5-flash-lite`; set it explicitly to override, or set it to
your main model to disable the saving.

### Environment variable overrides

Every key above also has an env override (these win over `config.json`):

| Env var | Overrides |
|---|---|
| `AG2ASSISTANT_LLM_PROVIDER` | `llm.provider` (`gemini` / `anthropic` / `openai`) |
| `AG2ASSISTANT_MODEL` | `llm.model` |
| `AG2ASSISTANT_API_KEY_ENV` | `llm.api_key_env` |
| `AG2ASSISTANT_AGGREGATE_MODEL` | `llm.aggregate_model` |
| `AG2ASSISTANT_LOCATION` | `agent.location` |
| `AG2ASSISTANT_SANDBOX` | `tools.sandbox` (`local` / `docker`) |
| `AG2ASSISTANT_DOCKER_IMAGE` / `AG2ASSISTANT_DOCKER_NETWORK` | Docker sandbox |
| `AG2ASSISTANT_AGGREGATE_EVERY_N` | `memory.aggregate_every_n_turns` |

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
AG2ASSISTANT_LLM_PROVIDER=anthropic AG2ASSISTANT_MODEL=claude-sonnet-4-6 \
  AG2ASSISTANT_API_KEY_ENV=ANTHROPIC_API_KEY ag2assistant chat
```

## Personalizing Your Agent

### System prompt

Customize your agent's personality by editing the system prompt in `~/.ag2assistant/config.json`:

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

## Skills — AG2 Assistant can extend itself

AG2 Assistant can search a public skills registry ([skills.sh](https://skills.sh)),
install skills, and use them — all on its own, mid-conversation. A skill is a
`SKILL.md` instruction package (optionally with scripts/resources).

**Bundled skills.** AG2 Assistant ships with a few first-party skills available from the
first run (no install needed): `web-research` (thorough multi-source research),
`pdf-tools` (read/extract/split/merge PDFs), and `email-drafting` (drafts in your
voice). It uses them automatically when relevant, or you can ask for one by name.

Just ask:

```
"Find a skill for working with PDFs, install it, and use it to summarise report.pdf."
"What skills do you have installed?"
```

The agent searches, installs to `~/.ag2assistant/skills`, and loads instructions only
when needed (progressive disclosure, so it stays token-cheap). Optional: set
`GITHUB_TOKEN` in `.env` for higher registry rate limits.

> Skills can ship runnable scripts. AG2 Assistant blocks obviously dangerous commands,
> and with `--sandbox docker` each skill script runs inside a one-shot container
> that can see **only that skill's own folder** — not your files. Without Docker,
> scripts run on the host (blocklist only), so install skills you trust.

## Memory — AG2 Assistant learns about you

AG2 Assistant passively builds a profile of how you like to work and remembers it across conversations and platforms. It tracks how you like things done, when you like them done, what you dislike, and how you write.

```bash
# Just talk — it learns passively
ag2assistant agent "I prefer short bulleted answers and I dislike emojis at work"

# See what it's learned
ag2assistant profile show

# Clear it
ag2assistant profile clear

# One-off without memory
ag2assistant agent "..." --no-memory
```

The profile is stored locally at `~/.ag2assistant/profile.db`. See [memory.md](memory.md) for the full design.

AG2 Assistant distils the profile every few turns (an LLM call each time) rather than after
every message, so long chats stay cheap. Single-shot `ag2assistant agent` runs always
distil their one turn. Tune the cadence with `AG2ASSISTANT_AGGREGATE_EVERY_N` (default 4).

## Permissions — control what AG2 Assistant can access

The first time AG2 Assistant wants to read a folder (or run a shell/code command), it asks
you: **Allow once / Always allow / Deny** — as buttons in chat, or a styled page
on the desktop. "Always allow" is remembered; denials apply for that turn only.

Manage grants from the CLI:

```bash
ag2assistant permissions list                       # show allowed + blocked folders
ag2assistant permissions allow ~/Documents          # pre-grant a folder
ag2assistant permissions revoke ~/Documents         # undo a grant
ag2assistant permissions block ~/private            # permanently deny (never asks)
ag2assistant permissions unblock ~/private          # remove the block
```

Grants/blocks persist in `~/.ag2assistant/permissions.json`.

## Sandbox — where shell/code runs

AG2 Assistant can run shell commands and execute code. Two backends:

| Backend | Isolation | Prompting |
|---|---|---|
| `local` (default) | Runs on your host; a blocklist filters obvious damage | Asks approval (Allow once / Always / Deny) before each command |
| `docker` | Runs in a throwaway container with **no access to your files** | No prompt — the container is the boundary |

```bash
ag2assistant agent "run my tests" --sandbox docker
ag2assistant run --sandbox docker
# or set it once:
echo "AG2ASSISTANT_SANDBOX=docker" >> .env
```

The Docker backend starts a container per session (default image
`python:3.12-slim`), runs every shell/code call inside it, and removes it on exit —
nothing the model runs can touch your filesystem. Tune it via `.env`:

```env
AG2ASSISTANT_SANDBOX=docker
AG2ASSISTANT_DOCKER_IMAGE=python:3.12-slim
AG2ASSISTANT_DOCKER_NETWORK=bridge   # "none" for the strictest (offline) isolation
```

If `--sandbox docker` is requested but Docker isn't running, AG2 Assistant falls back to
the local backend (with approval prompts) and warns. `read_file` always stays
permission-gated regardless of backend, since it reads your **host** files.

## Google (Gmail / Calendar / Drive)

AG2 Assistant can read and search your Gmail, manage your Calendar, and read your Drive.
**Writes are gated** — sending an email or creating an event always shows a HITL
approval card first and is denied if there's no one to ask.

### One-time setup

```bash
pip install "ag2assistant[google]"
```

1. In [Google Cloud Console](https://console.cloud.google.com): create a project
   and **enable** the Gmail API, Google Calendar API, and Google Drive API.
2. Configure the **OAuth consent screen** (External) and add yourself as a **Test user**.
3. **Credentials → Create OAuth client ID → Desktop app**, download the JSON, and
   save it to `~/.ag2assistant/google_credentials.json`.
4. Sign in (opens a browser once):

```bash
ag2assistant google login      # → "Signed in to Google as you@gmail.com."
ag2assistant google login --no-browser   # print the consent URL instead of opening it
ag2assistant google status     # show configured / signed-in state
ag2assistant google logout     # remove the stored token
```

The token is cached at `~/.ag2assistant/google_token.json` and refreshed automatically.

**From the web UI instead.** The gateway's web client has a **Google** button:
it shows the connection status, lets you **upload the OAuth client JSON** (so you
can skip the filesystem step), and a **"Open Google consent →"** button that opens
Google's page and finishes sign-in automatically. The consent redirect returns to
the gateway's `/api/google/callback`.

> The redirect completes on the **machine running the gateway** (it returns to
> `localhost:<port>`). Open the consent link there. To finish sign-in from another
> device (e.g. a phone via a chat channel), expose the gateway at a public URL and
> set `AG2ASSISTANT_PUBLIC_URL=https://your-host` so the redirect is reachable.

### What the agent can do

Once signed in, these tools appear automatically:

| Tool | Action | Gated? |
|---|---|---|
| `gmail_search` / `gmail_read` | search + read mail | no |
| `gmail_create_draft` | save a draft (never sends) | no |
| `gmail_send` | send an email | **yes — approval** |
| `calendar_list_events` | list events | no |
| `calendar_create_event` | create an event | **yes — approval** |
| `drive_search` / `drive_read` | find + read Drive files | no |

Just ask: *"summarise my unread emails from this week"*, *"what's on my calendar
tomorrow?"*, *"draft a reply to Alice's last email"*, *"find the Q3 budget doc and
summarise it"*. To actually send, the agent will ask you to approve first.

Scopes requested: `gmail.modify` + `gmail.send`, `calendar`, `drive.readonly`.

## Sessions

AG2 Assistant maintains conversation history per user per channel. When you message your agent on Telegram, it remembers your previous conversations on Telegram. Discord conversations are separate sessions.

Sessions are persisted locally in `~/.ag2assistant/sessions/`.

## How It Works

1. You send a message on any connected platform (or CLI)
2. The channel adapter normalizes it to a common format
3. The gateway routes it to your session
4. The AG2 agent processes it with your conversation history
5. The response is sent back through the same channel

Your agent runs locally on your machine. Your messages and data stay on your device. API keys are used only to call the LLM provider.

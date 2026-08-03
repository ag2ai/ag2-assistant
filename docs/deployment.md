# Deployment

AG2 Assistant runs as a single Python process (the gateway) that serves the REST/WebSocket
API and the web UI, optionally alongside messaging channels. Pick the tier that matches how
you want to run it.

| Tier | For | How |
|------|-----|-----|
| [Contributor](#contributor-clone--editable) | developing the code | clone + `pip install -e ".[dev]"` |
| [CLI user](#cli-user-install-script--pipx--uv-tool) | running it locally without cloning | `uv tool install` / `pipx install` from git |
| [Self-hosted](#self-hosted-docker) | an always-on instance / server | Docker + Compose |
| [PyPI](#pypi) | released versions | `uv tool install ag2-assistant` (from the first release) |

All tiers store state under a data directory (`~/.ag2assistant` by default, `/data` in the
container) and give the agent a file workspace (`~/Documents/AG2 Assistant` by default,
`/workspace` in the container). Provider API keys can be supplied up front (env / `.env`) or
pasted during the first-run onboarding; either way they persist in the data directory.

> **Security.** The gateway has no authentication of its own and binds `127.0.0.1` by default
> (all tiers except Docker). Only expose it beyond localhost behind a reverse proxy that
> terminates TLS and handles auth.

---

## Contributor (clone + editable)

The development setup — a live, editable checkout.

```bash
git clone https://github.com/ag2ai/ag2-assistant.git
cd ag2-assistant
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ag2-assistant run        # gateway + web UI (+ any configured channels)
```

Open <http://localhost:8800/> and complete onboarding. See the main
[README](../README.md) for the full CLI.

---

## CLI user (install script / pipx / uv tool)

Install the CLI globally in its own isolated environment without cloning. The install
script is the easiest path — it installs uv first if needed (and uv downloads a compatible
Python, so no system Python 3.12 is required):

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/ag2ai/ag2-assistant/main/scripts/install.sh | sh

# Windows
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/ag2ai/ag2-assistant/main/scripts/install.ps1 | iex"

ag2-assistant run
```

Re-running the script upgrades to the latest commit (as does
`uv tool upgrade ag2-assistant`). The script takes environment overrides:
`AG2_ASSISTANT_REF` (branch/tag), `AG2_ASSISTANT_EXTRAS` (e.g. `google`), and
`AG2_ASSISTANT_REPO` (a fork URL).

If you already have uv or pipx, the direct equivalent (installing from git gets the latest
commit; released versions come from [PyPI](#pypi) instead):

```bash
# uv
uv tool install "git+https://github.com/ag2ai/ag2-assistant.git"

# …or pipx
pipx install "git+https://github.com/ag2ai/ag2-assistant.git"
```

Add extras like Google integration with `"git+https://github.com/ag2ai/ag2-assistant.git#egg=ag2-assistant[google]"`.

---

## Self-hosted (Docker)

The recommended way to run an always-on instance. The image is pure Python on
`python:3.14-slim` — the web UI bundle is committed to the repo, so no Node build is
involved. Published tags are multi-arch (`linux/amd64` + `linux/arm64`), so Apple Silicon
and ARM servers pull a native image.

### With Compose (recommended)

```bash
cp .env.example .env         # add GEMINI_API_KEY (or another provider key) — optional
                             # and set TZ (see "Timezone" below)
docker compose up -d         # build + run
```

Open <http://localhost:8800/>. State (`ag2_data`) and the agent workspace (`ag2_workspace`)
live in named volumes, so they survive `docker compose down` and restarts.

#### Anthropic / Ollama model types

Nothing to do: their provider libraries are baked into the image — both the local build
and the prebuilt GHCR one — because a `pip install` inside a running container is lost
the moment it's recreated (and the runtime is non-root with a pip-less venv, so it
wouldn't even succeed). Settings → Models flags any type whose library is missing; in
Docker none should be flagged.

Ollama still needs a reachable server, and `localhost` inside the container is the
container itself — point its host at the Docker host instead
(`http://host.docker.internal:11434`; on Linux that name needs the `extra_hosts` entry
noted below).

### Prebuilt image

Tagged releases publish an image to GHCR, so you can skip the local build:

```bash
docker run -d --name ag2-assistant \
  -p 8800:8800 \
  -e GEMINI_API_KEY=your-key \
  -v ag2_data:/data \
  -v ag2_workspace:/workspace \
  ghcr.io/ag2ai/ag2-assistant:latest
```

(Or in `docker-compose.yml`, comment out `build: .` and keep the `image:` line.)

### Timezone

Scheduled tasks are **wall-clock in the container's local timezone**, and container base
images default to UTC. So on a UTC container, "remind me at 6am" is booked for 6am UTC —
which is the wrong hour for anyone not on UTC. Nothing looks broken when this happens: the
task is created and confirmed normally, and only the reminder fires late or early.

Set `TZ` to an IANA zone name, via `.env` (Compose reads it) or `-e` on `docker run`:

```bash
TZ=Australia/Sydney
```

The startup banner prints the resolved local time, so you can confirm it:

```
AG2 Assistant gateway starting on http://0.0.0.0:8800
  Time    2026-07-27 06:00 AEST (UTC+1000)
```

If `TZ` is unset in a container, the banner says so explicitly. Non-Docker installs already
follow your system timezone and need nothing.

### Messaging channels

The default command is `gateway` (API + web UI only). To also run Telegram/Discord/Slack,
override the command to `run` and supply the channel tokens:

```yaml
    command: ["run", "--host", "0.0.0.0", "--port", "8800"]
```

with `TELEGRAM_BOT_TOKEN` / `DISCORD_BOT_TOKEN` / `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` in
`.env`.

### Code execution: local vs. docker-out-of-docker

The agent can run code. In the container it defaults to `AG2ASSISTANT_SANDBOX=local`: code
runs **inside the app container**, which is itself the isolation boundary. No host Docker
socket is mounted, so the host stays untouched.

If you want the agent to spawn **real sibling sandbox containers** instead (stronger per-run
isolation), mount the host Docker socket and switch the sandbox to `docker` — see the
commented block in `docker-compose.yml`. Note the trade-off: mounting `/var/run/docker.sock`
grants the container control of the host Docker daemon. Enable it only if you accept that.

### Coding agents from Docker (ACP host bridge)

The assistant can hand real coding tasks to a locally installed CLI coding agent
(Claude Code / Codex / OpenCode) via `code_with_cli_agent`. Those CLIs — and their
on-disk logins — live on the **host**, so a container cannot see them. The opt-in
**ACP host bridge** closes that gap: a small daemon on the host spawns the agent and
relays its ACP stdio to the container over TCP.

```
[HOST]  claude-agent-acp / codex-acp / opencode    (installed + logged in)
          ^ spawned by
        ag2-assistant acp-bridge          <--TCP+token--   [CONTAINER]
        (loopback:8801)                                    AG2ASSISTANT_ACP_BRIDGE=
                                                           host.docker.internal:8801
```

Default off: with `AG2ASSISTANT_ACP_BRIDGE` unset the container simply reports "no
coding agents". Nothing listens unless you start the daemon yourself.

**1. Generate a shared token** (gates every bridge connection) and give it to both
sides via `.env`:

```bash
echo "AG2ASSISTANT_ACP_BRIDGE_TOKEN=$(openssl rand -hex 16)" >> .env
echo "AG2ASSISTANT_ACP_BRIDGE=host.docker.internal:8801" >> .env
```

**2. Start the bridge on the host** (where the coding CLIs are installed and logged
in — install the CLI per the [CLI user](#cli-user-install-script--pipx--uv-tool)
tier, or use your dev checkout):

```bash
ag2-assistant acp-bridge --port 8801 \
  --token "$(grep AG2ASSISTANT_ACP_BRIDGE_TOKEN .env | cut -d= -f2)"
```

Keep it running; it prints the coding agents it can see on this host.

**3. Bind-mount the repo you want edited at the SAME absolute path** it has on the
host, so folder approval, the daemon's `cwd` check, and diffs all line up. In
`docker-compose.yml`:

```yaml
    volumes:
      - ag2_data:/data
      - ag2_workspace:/workspace
      - /Users/me/code/myrepo:/Users/me/code/myrepo   # host path == container path
```

Then `docker compose up -d` and ask the assistant to write code in that folder — it
asks you to approve the folder first, and the agent's plan + working-tree diff stream
into the chat.

Notes:

- On Linux, `host.docker.internal` needs `extra_hosts: ["host.docker.internal:host-gateway"]`
  (Docker Desktop provides it automatically), and the daemon must bind a
  host-reachable interface (`--host`) since `host-gateway` traffic does not reach a
  loopback-bound listener there.
- Coding runs are **not sandboxed**: the CLI agent edits real files in the approved
  folder through the bind mount — that is the point. The daemon only ever works
  inside the `cwd` the (token-authenticated) client names, spawns agents with a
  minimal env whitelist, and never receives provider API keys — auth is the CLI's
  own on-disk login.

### Configuration reference

| Env var | Purpose | Container default |
|---------|---------|-------------------|
| `AG2ASSISTANT_DATA_DIR` | install root: secrets, profiles, config, memory | `/data` |
| `AG2ASSISTANT_WORKSPACE` | the agent's readable/writable file space | `/workspace` |
| `AG2ASSISTANT_SANDBOX` | `local` (in-container) or `docker` (sibling containers) | `local` |
| `TZ` | IANA timezone for scheduled tasks (wall-clock local) — **set this** | unset → `UTC` |
| `AG2ASSISTANT_ACP_BRIDGE` | `host:port` of the ACP host bridge; unset = spawn coding agents locally | — |
| `AG2ASSISTANT_ACP_BRIDGE_TOKEN` | shared secret for the bridge (must match `acp-bridge --token`) | — |
| `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | provider keys | — |

See `.env.example` for the full list.

---

## PyPI

Publishing a GitHub release (created from a `vX.Y.Z` tag) uploads the sdist + wheel to PyPI
(`.github/workflows/pypi-publish.yml`, PyPI trusted publishing — same pattern as
ag2-classic's `python-package.yml`). From the first release onwards:

```bash
uv tool install ag2-assistant        # or: pipx install ag2-assistant / pip install ag2-assistant
```

Release flow: pushing the tag publishes the Docker image; publishing the GitHub release from
that tag publishes to PyPI. The workflow refuses to publish if the tag doesn't match
`pyproject.toml`'s version.

Every install path resolves the released `ag2` from PyPI (`>=1.0.0`): the published wheel via
its metadata, and dev installs, CI and the Docker image via the pinned version in `uv.lock`.
So a contributor's checkout, CI, and the shipped image all run the same AG2.

To test against unreleased AG2, add a `[tool.uv.sources]` entry pointing `ag2` at a git ref and
run `uv lock`. That overlay is uv-only — it never reaches the published wheel's metadata — but
the Docker builder will need `git` installed to clone it.

One-time setup before the first publish: on pypi.org, add a pending trusted publisher under
the **ag2ai organization** (project `ag2-assistant`, this repository, workflow
`pypi-publish.yml`, environment `package`), and create the `package` environment in the
repo settings.

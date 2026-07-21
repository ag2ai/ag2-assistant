# Deployment

AG2 Assistant runs as a single Python process (the gateway) that serves the REST/WebSocket
API and the web UI, optionally alongside messaging channels. Pick the tier that matches how
you want to run it.

| Tier | For | How |
|------|-----|-----|
| [Contributor](#contributor-clone--editable) | developing the code | clone + `pip install -e ".[dev]"` |
| [CLI user](#cli-user-pipx--uv-tool) | running it locally without cloning | `uv tool install` / `pipx install` from git |
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
`python:3.12-slim` — the web UI bundle is committed to the repo, so no Node build is
involved.

### With Compose (recommended)

```bash
cp .env.example .env         # add GEMINI_API_KEY (or another provider key) — optional
docker compose up -d         # build + run
```

Open <http://localhost:8800/>. State (`ag2_data`) and the agent workspace (`ag2_workspace`)
live in named volumes, so they survive `docker compose down` and restarts.

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

### Configuration reference

| Env var | Purpose | Container default |
|---------|---------|-------------------|
| `AG2ASSISTANT_DATA_DIR` | install root: secrets, profiles, config, memory | `/data` |
| `AG2ASSISTANT_WORKSPACE` | the agent's readable/writable file space | `/workspace` |
| `AG2ASSISTANT_SANDBOX` | `local` (in-container) or `docker` (sibling containers) | `local` |
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

The published package depends on the released `ag2` from PyPI (`>=1.0.0b0`). Development
installs are different on purpose: `[tool.uv.sources]` in `pyproject.toml` points `uv sync`
(and the Docker build) at AG2's git `main`, and that overlay never reaches the built wheel's
metadata. So contributors track AG2 main; PyPI users get reproducible releases.

One-time setup before the first publish: on pypi.org, add a pending trusted publisher under
the **ag2ai organization** (project `ag2-assistant`, this repository, workflow
`pypi-publish.yml`, environment `package`), and create the `package` environment in the
repo settings.

# syntax=docker/dockerfile:1

# AG2 Assistant — single distributable image (web UI + gateway + channels).
#
# Two stages, Python-only: the Svelte SPA is already built and committed under
# src/assistant/gateway/static/app/ and ships with the package, so no Node stage
# is needed. The builder resolves/installs everything into a self-contained
# virtualenv with uv; the slim runtime just copies that venv and adds the Docker
# CLI (client only) for the optional code-execution sandbox.

# ---------------------------------------------------------------------------
# Stage 1: builder — slim base + git (needed: ag2 is a git+https dependency, and
# uv shells out to git to clone it) + the uv installer.
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS builder

# uv (fast resolver/installer) — copied from its official image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# git is required to fetch the ag2 git+https dependency.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    # Use the base image's 3.14 interpreter, don't let uv fetch its own.
    UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy

# Isolated venv we copy wholesale into the runtime stage. `uv venv` does NOT
# install pip/setuptools into it — the venv is minimal from the start.
RUN uv venv /opt/venv

WORKDIR /app
COPY . .

# Install the project plus the optional Google integration (Gmail/Calendar/Drive).
# Channels (Telegram/Discord/Slack) and voice are already in the base deps.
RUN uv pip install ".[google]"

# Slim the venv (~350MB saved) — all removals are verified safe for this app:
#   1. Vertex AI / Google Cloud SDK: ag2[gemini] declares google-cloud-aiplatform
#      but never imports it — the Gemini path runs through google-genai. Drop it
#      and its heavy transitive deps (bigquery, storage, ...).
#   2. Bundled API discovery docs: google-api-python-client ships ~580 service
#      descriptors (~99MB); the app only build()s gmail/calendar/drive. Keep those
#      plus auth (oauth2/people); build() falls back to a network fetch for any
#      other service, so pruning them never crashes.
#   3. All *.pyc / __pycache__ — regenerated at runtime, not needed in the image.
RUN uv pip uninstall \
        google-cloud-aiplatform google-cloud-bigquery google-cloud-storage \
        google-cloud-resource-manager google-cloud-core google-resumable-media \
        grpc-google-iam-v1 \
    && DOCS=$(echo /opt/venv/lib/python*/site-packages/googleapiclient/discovery_cache/documents) \
    && if [ -d "$DOCS" ]; then \
         find "$DOCS" -type f -name '*.json' \
           ! -name 'gmail.*' ! -name 'calendar.*' ! -name 'drive.*' \
           ! -name 'oauth2.*' ! -name 'people.*' -delete; \
       fi \
    && find /opt/venv -name '__pycache__' -type d -prune -exec rm -rf {} + \
    && find /opt/venv -name '*.pyc' -delete \
    && find /opt/venv -name '*.pyo' -delete

# ---------------------------------------------------------------------------
# Stage 2: runtime — slim base, no git, no build tools, no Node.
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

# Docker CLI (client only, no daemon) so the assistant can drive a Docker daemon
# when the host's /var/run/docker.sock is bind-mounted. Without that mount,
# `docker info` fails and docker_available() falls back to local execution.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && apt-get purge -y --auto-remove gnupg \
    && rm -rf /var/lib/apt/lists/*

# Bring in the fully-built virtualenv from the builder.
COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    HOME=/root \
    # Relocate the workspace onto a mountable volume (state dir ~/.ag2assistant
    # has no env override and lives under HOME=/root).
    AG2ASSISTANT_WORKSPACE=/workspace \
    PYTHONUNBUFFERED=1

# Persist state and generated files across restarts / image upgrades.
VOLUME ["/root/.ag2assistant", "/workspace"]
RUN mkdir -p /root/.ag2assistant /workspace

EXPOSE 8800

# Liveness: the gateway's health endpoint (uses stdlib, no extra tools).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8800/api/health', timeout=4).status==200 else 1)"

# Gateway + web UI + any configured channels, bound to all interfaces so the
# published port is reachable from the host.
CMD ["ag2-assistant", "run", "--host", "0.0.0.0", "--port", "8800"]

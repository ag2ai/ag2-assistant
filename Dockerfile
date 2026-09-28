# syntax=docker/dockerfile:1

# AG2 Assistant — self-hosted image (web UI + gateway + channels).
#
# Two stages, Python-only: the Svelte SPA bundle is committed under
# src/assistant/gateway/static/app and ships via package-data, so there's no Node
# stage. The builder resolves/installs into a venv with uv; the slim runtime copies
# that venv, adds the Docker CLI (for the optional docker-out-of-docker sandbox),
# and runs as a non-root user.

# ---------------------------------------------------------------------------
# Stage 1: builder — slim base + the uv installer, nothing else.
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS builder

# uv (fast resolver/installer) — copied from its official image. Pinned: an
# unpinned `:latest` would silently change the resolver between builds, which
# defeats the point of installing from a committed lockfile.
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/

# uv.lock pins `ag2` to a git ref (see [tool.uv.sources]), and uv shells out to
# the git executable to fetch it — the slim base ships without one.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy \
    # `uv sync` targets this env instead of the default .venv, so we can copy it
    # wholesale into the runtime stage.
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY . .

# Reproducible install from the committed uv.lock: exact pinned versions, no
# re-resolution. --no-editable installs the project as a wheel (nothing depends on
# the /app source at runtime). Every optional extra is baked in: google adds
# Gmail/Calendar/Drive, anthropic/ollama add the provider client libraries. Those
# two are opt-in for pip installs, but NOT here — the runtime is non-root with a
# pip-less venv, so a user who hits "needs the anthropic extra" inside the
# container has no way to fix it short of rebuilding. They cost 3 packages with
# no transitive fan-out (anthropic, ollama, fix-busted-json — every other dep is
# already pulled in), which is cheaper than the dead end. Channels
# (Telegram/Discord/Slack) and
# voice are already in the base deps. `uv sync` creates /opt/venv with no
# pip/setuptools — minimal from the start.
RUN uv sync --frozen --no-editable --extra google --extra anthropic --extra ollama

# Slim the venv (~280MB saved) — all removals verified safe by booting the image:
#   1. Vertex AI / Google Cloud SDK: ag2[gemini] declares google-cloud-aiplatform
#      but never imports it — the Gemini path runs through google-genai.
#   2. Bundled API discovery docs: google-api-python-client ships ~580 service
#      descriptors (~99MB); the app only build()s gmail/calendar/drive. Keep those
#      + auth (oauth2/people); build() falls back to a network fetch otherwise.
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
# Stage 2: runtime — slim base, no git, no build tools, no Node. Non-root.
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

# Docker CLI (client only, no daemon): lets AG2ASSISTANT_SANDBOX=docker actually
# engage when the host's /var/run/docker.sock is bind-mounted — docker_available()
# runs `docker info`. Without the mount it stays False and code runs in the
# in-container "local" sandbox.
#
# Copied from the official pinned image rather than installed from Docker's apt
# repo: it's a single static Go binary, so this needs no keyring, no gnupg, and
# no curl in the runtime image, and the version is pinned rather than "whatever
# the repo serves today".
COPY --from=docker:28-cli /usr/local/bin/docker /usr/local/bin/docker

# Bring in the fully-built virtualenv from the builder.
COPY --from=builder /opt/venv /opt/venv

# Non-root runtime user. Named volumes mounted onto /data and /workspace inherit
# this ownership from the image, so the app can write to them.
RUN useradd --create-home --uid 10001 app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    # Persistent state (secrets, profiles, config, memory, tasks) and the agent's
    # file workspace — mounted as volumes in compose so they survive `docker rm`.
    AG2ASSISTANT_DATA_DIR=/data \
    AG2ASSISTANT_WORKSPACE=/workspace \
    # In-container code execution by default (no host Docker socket). Override to
    # "docker" only when the socket is mounted (docker-out-of-docker).
    AG2ASSISTANT_SANDBOX=local

RUN mkdir -p /data /workspace && chown -R app:app /data /workspace
VOLUME ["/data", "/workspace"]

USER app
EXPOSE 8800

# Liveness: the gateway's own health endpoint. Pure stdlib — no curl needed.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8800/api/health', timeout=4).status==200 else 1)"

# `gateway` serves the API + web UI without messaging channels; override the CMD
# with `run` (and channel tokens) to also start Telegram/Discord/Slack.
ENTRYPOINT ["ag2-assistant"]
CMD ["gateway", "--host", "0.0.0.0", "--port", "8800"]

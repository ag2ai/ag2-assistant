# AG2 Assistant — self-hosted image.
#
# Single stage: the Svelte SPA bundle is committed under
# src/assistant/gateway/static/app (CI keeps it fresh), so no Node build is needed —
# only Python. `git` is required because the AG2 dependency is a direct git reference
# (see pyproject.toml), which pip clones at build time.
#
# Code execution defaults to the in-container "local" sandbox: the container IS the
# isolation boundary, so no host Docker socket is mounted. To instead let the agent
# spawn sibling sandbox containers, see the commented block in docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Persistent state (secrets.json, profiles/, config) and the agent's file
    # workspace — mounted as volumes in compose so they survive `docker rm`.
    AG2ASSISTANT_DATA_DIR=/data \
    AG2ASSISTANT_WORKSPACE=/workspace \
    # In-container code execution (no host Docker socket). Override to "docker" only
    # when the host socket is mounted (docker-out-of-docker).
    AG2ASSISTANT_SANDBOX=local

# git: clone the ag2 @ git+... dependency. No build-essential — deps ship wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user. Named volumes first mounted onto /data and /workspace
# inherit this ownership from the image, so the app can write to them.
RUN useradd --create-home --uid 10001 app

WORKDIR /app
COPY . /app

# Editable install: the package imports from /app/src, so the committed SPA bundle
# and all package data are served straight from the source tree (no package-data
# globbing gaps). The dynamic version (assistant.__version__) resolves here too.
RUN pip install -e . \
    && mkdir -p /data /workspace \
    && chown -R app:app /data /workspace /app

USER app
EXPOSE 8800

# Liveness: the gateway's own health endpoint. Pure stdlib — no curl in the image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8800/api/health').status==200 else 1)"

# Bind 0.0.0.0 so the port is reachable across the container boundary. `gateway`
# serves the API + web UI without messaging channels; override the CMD with
# `run` (and channel tokens) to also start Telegram/Discord/Slack.
ENTRYPOINT ["ag2-assistant"]
CMD ["gateway", "--host", "0.0.0.0", "--port", "8800"]

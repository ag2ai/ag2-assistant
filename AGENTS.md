# AG2 Assistant — Development Guidelines

AG2 Assistant (package `ag2-assistant`) is an open-source personal AI assistant
built on [AG2](https://github.com/ag2ai/ag2)'s framework.
This file is the contributor guide for humans and AI coding agents alike. For a
product overview see [README.md](README.md); for the system design see
[docs/architecture.md](docs/architecture.md).

## AI-assisted contribution policy

We welcome AI-assisted contributions — but **you remain responsible for
everything you submit**: code, tests, issues, and PR descriptions. Before
opening a PR, read [`.github/AI_POLICY.md`](.github/AI_POLICY.md). In short:
understand and test what you submit, make the PR description reflect the real
diff, and be ready to explain the change in your own words. Fill in
[`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md) and only
tick a checklist box once it is actually true.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"     # add ",google" for the Gmail/Calendar/Drive extra
pre-commit install          # run the lint/format/safety hooks on every commit
```

## Checks (run these before you push)

These are the same checks CI runs; a PR that fails them will not merge.

```bash
ruff check .                       # lint
ruff format .                      # auto-format (use --check to verify only)
pytest -m "not integration" -q     # unit tests — no API key needed
npm --prefix web run build         # rebuild the SPA bundle if you touched web/
```

Integration tests (`pytest -m integration`) hit a real LLM/network/Docker and
need API keys; they are excluded from the default run and from CI.

## Code style

Ruff owns formatting and import order — line length 100, double quotes, rule set
`E,F,I` (see `pyproject.toml`). Beyond that:

- **Prefer `pathlib.Path`** for filesystem paths; accept `str | os.PathLike[str]`
  in public signatures.
- **Import at module top level**, not inside functions.
- **Avoid `from __future__ import annotations`** in new code. A few
  existing files still use it; leave them unless you're already touching them.
- **The event stream is the spine.** The agent loop communicates through AG2's
  typed event stream; the web UI, channels, and CLI are all *projections* of that
  stream. Add new behaviour as events/observers — don't build a parallel data
  path that bypasses the stream.
- **Comments say what, in ≤2 lines.** A comment or docstring describes what the
  code does — not why it exists, what it replaced, or how it was decided. Keep
  each one to two lines at most.
- **No compat shims.** This is a fast-moving prototype: when you change or remove
  something, delete the obsolete code and its guards outright rather than keeping
  backward-compat fallbacks.
- **Surface config, don't guess it.** Prefer exposing a choice in onboarding or
  Settings over silent auto-detection or a hardcoded magic default.
- **Actions are buttons, never arrow text.** A clickable action in the UI must be
  a real, visually-distinct button (e.g. the `.open` pill) — never a bare text
  affordance, and never a label decorated with `→` ("Add manually →",
  "Change →"). Don't embed the action as trailing text inside an info row or
  text box; place the button beside it (see `.setrowwrap` in `web/src/app.css`).
  Arrows are fine in comments and in *data* displays (step indicators, answered
  prompts) — the rule is about action affordances.

## Repository layout

| Path | What lives there |
|------|------------------|
| `src/assistant/` | The Python package: `agent.py` (agent construction), `gateway/` (FastAPI REST + WebSocket + static hosting), `tools/` (web search, shell, code exec, image gen, files, Google, MCP), `channels/` (Telegram/Discord/Slack), `tasks/` (scheduling + execution), `hitl/` (human-in-the-loop), `memory.py`, `integrations/`, `skills/`. |
| `web/` | Svelte + Vite source for the web UI. **This is the source of truth for the front-end.** |
| `src/assistant/gateway/static/app/` | The **generated, committed** SPA bundle (see below). |
| `tests/` | Pytest suite (`integration` marker for the slow, key-requiring ones). |
| `docs/` | Architecture, usage, deployment. |

## The committed SPA bundle (important gotcha)

The web UI is built from `web/` but the compiled bundle is committed under
`src/assistant/gateway/static/app/` so that deploying the package stays
Python-only. **Never hand-edit files under `static/app/`** — they are build
output. Change the source in `web/src/`, then rebuild:

```bash
npm --prefix web run build
```

Both a pre-commit hook and the CI `bundle-fresh` job rebuild the bundle and fail
if the committed copy has drifted from `web/` source. If CI flags a stale bundle,
run the build and commit `src/assistant/gateway/static/app/`.

## Testing

- Unit tests run without any API key: `pytest -m "not integration"`.
- Mark tests that call a real LLM, network, or Docker with `@pytest.mark.integration`.
- **Front-end changes need a live browser pass**, not just `web/diag.mjs`: load
  the app in Chrome and check the DevTools console and network tab for errors.
  The headless `diag.mjs` smoke test alone is not sufficient to catch UI
  regressions.

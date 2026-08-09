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
npm --prefix web run check         # typecheck the SPA (svelte-check, strict)
npm --prefix web test              # SPA unit tests (node:test over web/src/**/*.test.ts)
npm --prefix web run build         # rebuild the SPA bundle if you touched web/
```

`npm --prefix web run build` runs `check` first, so a bundle can never be built —
or committed — with type errors.

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
- **Breaking changes land backward-compatibly.** Installed data outlives the code
  that wrote it, so a change to a persisted shape ships the adoption that carries an
  existing install across it, and the code it replaced is deleted rather than
  branched on both shapes forever. Adoption comes in two shapes, with different rules:
  - A **migration** rewrites what was authored. Make it self-contained, run it once,
    record that it ran, and resume cleanly on a second start.
  - An **inference rule** re-derives a field that was derived anyway. Re-run it on
    every start, record nothing, and make sure it can name nobody its source could not
    already name — it must not be able to grant what its source does not already grant.

  Which shape you have is decided by the source of truth: if it still exists at read
  time, re-derive it as an inference rule; if the old shape is gone, migrate.

  In-process shims that only ease a refactor still go: this is a fast-moving
  prototype, and only *persisted* state earns either.
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
| `web/` | Svelte + Vite source for the web UI, TypeScript throughout. **This is the source of truth for the front-end.** |
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

## Front-end types (`web/`)

The whole SPA is TypeScript under `strict` — every `.ts` module and every
`<script lang="ts">`. There are no `.js` files and no `.d.ts` files in `web/src/`.

**The source of truth for API shapes is `web/src/schemas/` — zod schemas, not
`.d.ts` declarations.** Each schema is declared once and the type is derived from
it (`export type Task = z.infer<typeof Task>`), so a response shape and its type
cannot drift apart. Every response goes through `transport/validate.ts::parse()`:
in dev a mismatch throws `SchemaError`, in prod it logs `[schema] …` and passes
the data through. Request bodies are typed but not validated at runtime.

The gateway declares the same shapes as Pydantic response models in
`src/assistant/gateway/schemas/` (one module per domain, mirroring
`web/src/schemas/` file for file), and `docs/openapi.json` is the committed
artifact tying the two together. For a route listed in `ROUTES`
(`web/src/schemas/routes.ts`), CI fails if the zod schema and the gateway
disagree on field names, requiredness or enum members — so you cannot forget.

When you change a response body in `gateway/`:

1. update the Pydantic model in `gateway/schemas/`;
2. run `python3 scripts/dump_openapi.py` and commit `docs/openapi.json`;
3. update the zod schema in `web/src/schemas/` — for a route still in `PENDING`
   nothing checks this yet, so it is still on you.

Every route sits in exactly one bucket of `routes.ts` (`ROUTES`, `UNMAPPED` or
`PENDING`); adding a route without deciding which is a test failure.

## Testing

- Unit tests run without any API key: `pytest -m "not integration"`.
- Mark tests that call a real LLM, network, or Docker with `@pytest.mark.integration`.
- **`monkeypatch` is not used in tests.** Dependencies arrive as parameters: `Paths`
  for the on-disk layout, an explicit `env: Mapping` for the environment,
  `search_path` for external binaries, `extras` for the optional provider libraries
  (`llm_configs.deps_status` / `LlmConfigStore.usable`), `agent_factory`/
  `channel_factory`/`title_factory`/`summary_factory` for collaborators, an injected
  `httpx.Client` for the network. `os.environ` and `Path.home()` are read only in `cli.py`,
  `paths.Paths.from_env` and `config.load_config` — `tests/test_no_global_defaults.py`
  is the gate for that, and its deferred list may only shrink.
- **Stubs of external programs are real files.** `tests/support/stubs.py::write_stub`
  writes an executable script; the test hands its directory over as `search_path`. A
  stub standing in for a protocol peer must read each request before answering and
  stay alive afterwards — one that answers blindly and exits closes the pipe under
  the prober's next write, which shows up as a load-dependent flake.
- **The network is real httpx.** `tests/support/http.py::client` / `async_client`
  build an `httpx.Client` over `httpx.MockTransport`, so all of httpx really runs;
  archives, tarballs and token files are real too.
- **Don't assert that a method was called.** Instead of spying on an internal, check
  the observable effect through the public API or the stream event — a spy passes
  even when the call does nothing.
- Test helpers live in `tests/support/{fakes,apps,http,stubs}.py`. `tests/conftest.py`
  holds fixtures only (`paths`, `config`, `profile_app`, `profile_app_factory`) and is
  never imported from; `HOME` is not patched, so every test takes `paths` or passes
  `env` explicitly.
- **Front-end changes need a live browser pass**, not just `web/diag.mjs`: load
  the app in Chrome and check the DevTools console and network tab for errors.
  The headless `diag.mjs` smoke test alone is not sufficient to catch UI
  regressions.

## Agent skills

### Issue tracker

Issues and specs live as markdown files under `.scratch/<feature-slug>/` in this repo. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, each label string equal to its name, recorded on the `Status:` line of each issue file. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

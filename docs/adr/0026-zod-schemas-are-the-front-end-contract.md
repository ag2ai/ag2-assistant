# zod schemas are the front-end's contract with the gateway

`web/` was 86 `.svelte` files and 47 `.js` modules with no `tsconfig.json`, no
typecheck in CI, and exactly two `@typedef` in the whole tree (both in
`transport/api.js`). It is now TypeScript under `strict` end to end — **0 `.js`
files, 0 `.svelte` without `lang="ts"`, 0 `any`, 0 `@ts-expect-error`** — and every
response the gateway sends is described by a **zod schema in `web/src/schemas/`**,
from which the TypeScript type is derived. The schema is checked against the real
response at the transport boundary, not just at compile time.

## Context

The gateway has 117 REST routes and 2 WebSockets, and `response_model` on exactly
one of them (`POST /message`). Every other body is a dict assembled by hand in
`app.py` or a service helper. There is no machine-readable contract, so nothing
connected the shape a route returns to the shape a component reads.

Untyped, the front end could only discover a mismatch by rendering it. A renamed
key showed up as a blank cell; a value that stopped being sent showed up as a
fallback that looked deliberate. That is not a theoretical cost — writing the
schemas surfaced four defects that had been shipping:

- **`task_name` was silently dropped from the run header.** Both run routes answer
  through `tasks_service.get_run`, which adds the task's name to the run view; the
  schema didn't have the field, so the run banner showed the fallback `'Task'`.
- **`Grant.mode` rejected `none`.** `folders.py` defines a third mode — an
  override that blocks an inherited folder for one chat or task — so any install
  with a per-chat block would have failed validation of `GET /api/folders`.
- **`KeySource` rejected `cli_login`.** `llm_configs.py` returns it for
  `claude_code` / `codex` when an ACP adapter is found; any install with either
  configured would have lost the whole `GET /llm-configs` list — Models page and
  the model switcher blank.
- **`attachments` were typed as `string[]` but sent as objects.** The composer has
  always sent `{name, mime, data}`, and `_decode_attachments` has always expected
  that; only the declaration disagreed.

Three of the four are shapes a compiler alone can never check: they live on the
wire, and only a runtime comparison against the real response can catch them.

## Decision

- **`web/src/schemas/*.ts` is the source of truth for API shapes** — hand-written
  zod schemas, ~1.1k lines across 13 domain files. A schema is declared once and
  the type comes from it (`export type Task = z.infer<typeof Task>`), so a shape
  and its type cannot drift apart. **No `.d.ts` anywhere in `web/src/`.**
- **Responses are validated, requests are not.** Every response passes through
  `transport/validate.ts::parse()`. A mismatch **throws `SchemaError` in dev** and
  **logs `[schema] <label>` and passes the data through in prod** — a schema
  mistake must not take the UI down in front of a user. Request bodies are typed
  by the compiler; validating data the front end just built is bundle weight
  without a reader.
- **`api.js` was split into `transport/api/` by domain, and its public contract did
  not change.** Components still call `api.listTasks()`; the recomposed key set was
  diffed against the deleted `api.js` and came out empty. Envelope unwrapping
  (`.then(d => d.tasks)`) moved across as-is, method names included — the split was
  not an occasion to rename anything.
- **WebSocket events are a discriminated union on `type` plus a catch-all.** The
  reducer's `switch` is made exhaustive by an `isHandled()` gate over
  `HANDLED_EVENTS` and an `assertNever()` default, so a handled event name that
  loses its branch is a compile error. An unknown event from a newer backend is
  ignored, not a crash.
- **`strict` + `noUnusedLocals`, and null holes get fixed rather than silenced.**
  No `!`, no `as` to get past the compiler. Where `strict` found a real hole it
  became an explicit guard or an explicit `throw` (`#app` missing, a text glyph
  with no bounding box) — loud, not swallowed.
- **Imports carry the real `.ts` extension.** Verified empirically: under Node's
  native type stripping, `./lib.js` is *not* remapped to an existing `lib.ts`, so
  the usual TS convention is incompatible with `node --test`. Vite resolves the
  explicit form directly; `tsc` needs `allowImportingTsExtensions` (which requires
  `noEmit` — correct here, Vite emits).
- **Three gates, so a type error cannot reach `main`:** the CI job `web-typecheck`,
  the `web-typecheck` pre-commit hook, and `"build": "npm run check && vite build"`.
  The last one matters most: the bundle is committed, the hook that rebuilds it
  calls `build`, so a bundle **cannot be committed with type errors**.

## Considered options

- **Generate types from the backend's OpenAPI.** The right long-term answer and
  explicitly *not* rejected — but it needs `response_model` on 117 routes first,
  which is a backend project of its own. Hand-written schemas plus a dev-mode
  throw buy the same protection now without touching the gateway. *That backend
  project is [ADR 0027](0027-openapi-is-the-machine-checked-contract.md); the
  schemas stay hand-written, and generating from them is now a choice rather
  than a prerequisite.*
- **Types only, no runtime validation.** Rejected by the evidence above: three of
  the four defects were front-end declarations disagreeing with the wire, and a
  compiler that checks the front end against itself is exactly blind to that.
- **JSDoc + `checkJs` instead of a migration.** Rejected: it gives weaker
  inference, no discriminated-union narrowing in `.svelte`, and leaves the
  annotations detached from the values that are actually parsed.
- **Validate request bodies too.** Rejected: the front end builds them and the
  compiler already checks them; zod on the way out is bundle weight with no reader.
- **Throw on a schema mismatch in prod as well.** Rejected: schemas are
  hand-derived, so a wrong schema is likelier than a wrong backend, and a wrong
  schema would then be a white screen. Warn-and-pass keeps today's behaviour.
- **A shared package or codegen between `src/assistant/` and `web/`.** Rejected as
  scope: the gateway assembles responses as dicts, so there is nothing to generate
  from until the `response_model` work happens.

## Consequences

- **The four defects above are fixed**, and each is pinned by a test.
- **Dead code the types exposed is gone**, not annotated: a `GET /tasks/{id}` call
  fetching a `deliverables` key no route has ever returned; a profile filter on an
  `archived` field `_profile_view` doesn't emit; an `error` key a component
  injected into a status object that has none; three unread uniforms in the
  `cloudy` scene. Each was invisible while everything was `any`.
- **Schemas are now a maintenance obligation.** Change a response body in
  `gateway/` and the matching schema must change in the same commit — nothing
  generates them, and nothing on the backend side fails if you forget. The dev
  throw is the safety net, which means **it only fires if someone opens the page**.
  Recorded in `AGENTS.md`. *Superseded by
  [ADR 0027](0027-openapi-is-the-machine-checked-contract.md): every JSON route
  carries a `response_model` and CI compares each zod schema against
  `docs/openapi.json`, so forgetting now fails the build rather than the page.*
- **`web/diag.mjs` cannot validate schemas.** It answers routes with stubs, so its
  `[schema]` output is an artifact of the mock. The only real check of a schema
  against the backend is a live browser pass — which is why `AGENTS.md` already
  requires one for front-end changes.
- **zod ships in the runtime bundle.** Accepted for the dev-time throw and the
  prod-time warning; the cost is per-response `safeParse`, not per-render.
- **A11y and Svelte-5 reactivity debt got paid up front.** `--fail-on-warnings`
  turned up 50 pre-existing warnings (42 a11y, 6 reactivity, 2 CSS) on untouched
  code; the markup of 22 components changed as a result.
- **Tests moved with the code:** 17 `*.test.mjs` → 29 `*.test.ts`, 314 tests on
  `node --test`, `.nvmrc` 20 → 22. There are still no component tests — `node --test`
  cannot import `.svelte`.
- **The three.js weather scenes are typed with `@types/three` and needed zero
  `@ts-expect-error`.** The plan had budgeted suppressions for TSL; 0.185.3's
  declarations turned out to be complete enough to go without.

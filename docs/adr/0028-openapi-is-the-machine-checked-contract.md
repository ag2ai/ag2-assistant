# OpenAPI is the machine-checked contract between gateway and SPA

CI fails if a zod schema in `web/src/schemas/` disagrees with the gateway body it
claims to describe. Every JSON route the gateway serves carries a
`response_model`; every one of them sits in exactly one bucket of
`web/src/schemas/routes.ts`. The comparison runs against the gateway's OpenAPI
document, **generated from the app when the gate runs** and never committed. The
pairing that was a maintenance obligation in ADR 0026 is now a test.

## Context

ADR 0026 made `web/src/schemas/` the front end's contract with the gateway and
recorded two debts in its consequences. This ADR pays both.

The first: *"Change a response body in `gateway/` and the matching schema must
change in the same commit — nothing generates them, and nothing on the backend
side fails if you forget."* The gateway declared **140** route decorators and
exactly **one** `response_model`, so **129 of 130** JSON routes described their
success body as `{}` in the generated schema. Nothing connected the shape a route
returned to the shape a component read except a person remembering.

The second: *"Generate types from the backend's OpenAPI — the right long-term
answer and explicitly not rejected, but it needs `response_model` on 117 routes
first."* That is the work this ADR records, though generation itself is still not
done: this makes it possible, and deliberately stops there.

## Decision

- **Every JSON route declares a Pydantic response model**, in a new package
  `src/assistant/gateway/schemas/` that mirrors `web/src/schemas/` file for file.
  128 routes are mapped; 16 are `UNMAPPED` with a stated reason (raw bytes, an
  HTML OAuth landing page, the SPA shell, the bare-404 catch-all, and the two
  desktop HITL pages the SPA never calls).
- **The model IS the contract.** Models inherit `BaseModel` directly, at
  pydantic's default strictness: a key the model does not declare does not reach
  the client. There is no shared base class and no `extra="allow"` — measured on
  the phase-1 code, strict mode cost two test failures out of 1780 and bought back
  the `additionalProperties: true` that would have crippled the generation this
  work exists to enable.
- **The decorator declares the shape, not a return annotation.** `app.py` had 124
  `return JSONResponse(...)` branches; `-> Model` beside one of those is a lie, and
  the honest `-> Model | JSONResponse` raises `FastAPIError` at import. The
  decorator describes exactly what OpenAPI describes — the success body — and says
  nothing about the error branches, which stay as they were.
- **`response_model_exclude_unset=True` only where the model has a defaulted
  field.** Without it FastAPI ships an absent optional field as `null`, and a zod
  `.optional()` rejects `null`; with it, the model echoes what the handler sent
  instead of inventing keys.
- **The document is generated when the gate runs, not committed.**
  `npm --prefix web test` builds it from the live app through a `pretest` hook
  (`scripts/gen_openapi.sh` → `scripts/dump_openapi.py --out web/.openapi.json`,
  gitignored), then `web/src/schemas/routes.test.ts` compares each mapped route's
  200 body against its zod twin. The cost is Python in the web CI job — route
  declarations are all the dump needs, so `uv sync --frozen --no-dev` covers it.
  The gain is that the gate always reads the CURRENT app: there is no second copy
  of the truth, so no freshness test, no 500 KB of generated diff per route change,
  and no way to review a schema against a document someone forgot to regenerate.
- **Comparison depth is field names, requiredness and enum members** — not full
  JSON Schema equality. zod and pydantic disagree on `title`, `description`,
  `format`, integer-vs-number and the exact shape of nullable; comparing those
  would make the gate a false-positive generator. Names alone would have caught
  only two of ADR 0026's four defects, which is why enums are in.
- **zod is read in INPUT mode** (`z.toJSONSchema(schema, { io: 'input' })`). A
  field with `.default(...)` is required on the output side, because parsing always
  fills it, but optional on the wire — and the wire is what the gateway has to
  match.
- **`app.py` was decomposed into `gateway/routes/`, one module per domain**,
  mirroring the other two trees so any of the three paths finds the other two:
  `routes/folder.py` ↔ `schemas/folder.py` ↔ `web/src/schemas/folder.ts`. A module
  is chosen by its zod twin, not by its URL: `/tasks/{id}/permissions` lives in
  `routes/permission.py` because `TaskRules` is declared in `permission.ts`.
- **Route modules receive their collaborators, they do not import them.** Each
  module exposes `build_router(deps)` / `build_profile_router(deps, get_runtime)`
  and closes over a frozen `GatewayDeps`; a `create_app` *parameter* rather than a
  store (`llm_probe`, `live_probe`, `code_reader`, `skills_client`, `secret_env`)
  stays a keyword argument to the factory. Stores are built per `create_app` call —
  tests stand up dozens of apps over different `Paths`, and module-level state is
  forbidden outright (`tests/test_no_global_defaults.py`).
- **Rolled out in seven merged phases, 13–28 routes each**, with a third bucket
  `PENDING` naming the phase that would take each remaining route. CI stayed green
  throughout, and a route forgotten entirely was impossible because it would have
  belonged to no bucket. `PENDING` is now empty.

## Considered options

- **Keep `extra="allow"` and a shared `ResponseModel` base.** The first draft did.
  Rejected on measurement: the feared failure — "a strict model silently drops a
  key the front end needs" — cannot happen, because a key absent from the zod
  schema is stripped by `parse()` before any component sees it, and a key present
  in zod but missing from the model fails the gate. The cost was real:
  `additionalProperties: true` on every model weakens the code generation this work
  exists to unlock.
- **A return annotation instead of `response_model=`.** Rejected: see above, 124
  branches make it false, and the honest union does not import.
- **Generate the zod schemas from OpenAPI.** Not done, and not rejected either.
  The hand-written schemas caught four real defects and remain the front end's
  source of truth (ADR 0026 stands). This work makes generation a later choice
  rather than a prerequisite.
- **Return model objects instead of dicts.** `return FolderCreateResponse(...)` is
  the right end state and buys the contract nothing today: FastAPI validates a dict
  and an object identically, the OpenAPI document is the same, and the repo has no
  type checker to notice the difference. It also stops at the handler boundary —
  `FolderStore.list_folders() -> list[dict]` — so the model would be assembled from
  dicts anyway. A real pass at this is mypy plus typed domain layers, separately.
- **Commit the document, the same idiom as the SPA bundle.** The first draft did,
  guarded by a freshness test. Rejected, and the analogy is what was wrong with it:
  the bundle is committed because it SHIPS — `static/app/` is served out of the
  installed package — while nothing ships this document, since FastAPI builds
  `/docs` from the live app. So the committed copy was pure test input that
  guaranteed only itself, at 500 KB and 19k lines of generated diff per route
  change, plus a freshness test that failed on things the app never changed: the
  reason phrases FastAPI takes from `http.HTTPStatus` were renamed in Python 3.13
  (RFC 9110), so a document generated on 3.14 read as stale under CI's 3.12.
- **Pass the document between CI jobs** (`upload-artifact`). Rejected: it makes the
  web job wait on the whole pytest job for one file, and `npm test` still would not
  work locally. Generating it in the web job is one `uv sync --frozen --no-dev`.
- **One big decomposition PR, then the models.** Rejected: a 3000-line move is not
  reviewable, and a mistake in it costs the whole change. Domain-by-domain, each
  phase's move is a separate commit from its contract change, so the move can be
  read as a move.

## Consequences

- **`app.py` went from 4061 lines to 978, and declares no domain route.** What is
  left is the app: `GatewayDeps`, lifespan, the origin guard, the two WebSockets,
  the static/SPA handlers, the catch-all and the `include_router` calls. The
  endpoints live in twelve domain modules under `gateway/routes/` (3.8k lines with
  `common.py`, `deps.py` and the package init) beside their models in
  `gateway/schemas/` (1.8k lines).
- **The generated document is now worth generating from.** 144 operations, 180
  named component schemas, unique `operationId` on every one (a test), and a
  deterministic build across processes (another test — set iteration order is
  randomised per process and would otherwise flake CI).
- **The rollout found real divergences, which is the point.** `Schedule` was
  missing `at`/`cron` that `ScheduleField.svelte` reads; `settings.ts` declared a
  `token_present` the gateway never sent and omitted the `connection`/`name` it
  does; `HitlQuestion` declared `detail` and `options` as plain values though
  `hitl/base.py` defaults both to `None` — so every permission prompt failed
  `safeParse` and the "Needs your input" strip, which catches that and substitutes
  an empty list, had been silently showing nothing.
- **The gate itself had a bug worth recording.** A body that is one nullable model
  renders as `anyOf: [$ref, null]`; dereferencing before stripping the null member
  left the surviving `$ref` unresolved, so the body read as "no fields" and passed.
  `resolve()` is now `deref ∘ stripNull ∘ deref`. zod inlines nested objects, so
  the asymmetry never showed from the front-end side.
- **The obligation from ADR 0026 is discharged, and nothing replaced it.**
  `AGENTS.md` no longer asks anyone to remember the pairing; it points at the gate.
  Changing a response body is two steps — the model and the zod twin — with no
  artifact to regenerate and commit, because `npm test` generates it.
- **Every response documents its own `description`.** Left to FastAPI it comes from
  `http.HTTPStatus`, whose reason phrases change between Python versions, so a
  generated client would name the same status differently depending on the
  interpreter. `tests/test_openapi_schema.py` holds every documented response to a
  phrase this repo spells out.
- **`/docs` describes every response body**, which it previously did for one route.
- **The error codes are documented once, not 130 times.** `ERROR_RESPONSES` is
  attached to the app and to the `/api/p/{pid}` router; FastAPI propagates a
  router-level `responses` to every route beneath it. The side effect is cosmetic
  noise — a 409 documented on a static asset route — and is accepted.
- **Adding a route now costs a decision.** It must carry a model and land in
  `ROUTES`, or land in `UNMAPPED` with a reason. Neither is a test that can be
  quietly skipped, and that is the intended friction.
- **WebSocket frames are still hand-written zod only.** OpenAPI does not describe
  them, so `web/src/schemas/events.ts` stays outside the gate — the one part of the
  wire where ADR 0026's original obligation still applies verbatim.

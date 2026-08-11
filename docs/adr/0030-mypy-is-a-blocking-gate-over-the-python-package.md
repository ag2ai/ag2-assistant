# mypy is a blocking gate over the Python package

A type error cannot be merged, the same way a lint error or a stale SPA bundle cannot.
`mypy` runs from the project environment in a pre-commit hook and as its own CI job,
over `src/assistant`, at a curated strictness level that is **not** `--strict`. The 263
errors the first run found were fixed to a true zero rather than suppressed, so the
configuration that landed is the configuration that holds: no per-module exception list,
no recorded baseline.

## Context

The repository gated every other kind of correctness it cared about and left Python
types ungated. `ruff` blocked a merge on lint and format; `svelte-check` blocked one on
front-end types twice over (a pre-commit hook and a CI job); the committed SPA bundle was
checked for staleness; `uv.lock` for drift; the OpenAPI document was a machine-checked
contract (ADR 0028). The ~29k lines of Python that all of that surrounds had nothing
verifying that its annotations agreed with each other or with the libraries it calls.

The first run reported **263 errors in 44 of 123 modules**, and their shape mattered more
than the count: `union-attr` (99) and `attr-defined` (49) dominated — attribute access on
a value that may be `None`, and attributes that do not exist on the inferred type. That is
the class of defect a user meets as a failed turn, not as a lint complaint. Two were live
defects reaching a model provider (see ADR 0010's amendment). No command in the
contributor guide would have caught any of the 263, so each arrived through a green CI run.

## Decision

- **The level is the checker's default plus five options**, configured in `pyproject.toml`
  beside `ruff`, `pytest` and `codespell`: `warn_unused_configs`, `warn_redundant_casts`,
  `warn_unused_ignores`, `strict_equality`, `check_untyped_defs`. Chosen by measurement
  over this package, not by preference:

  | Configuration | Errors | Modules affected (of 123) |
  | --- | --- | --- |
  | Checker default | 252 | 46 |
  | **This level** | **263** | **44** |
  | `--strict` | 1,176 | 92 |

  `check_untyped_defs` is the highest-value option in the set — it extends coverage into
  code carrying no annotations at all — and costs about 15 of those errors. The module
  count *drops* below the default's because resolving the untyped third-party imports
  removes four import errors outright.

- **`--strict` is the declared destination, not a rejected option.** Its 1,176 errors are
  predominantly missing annotations rather than defects; getting there is an annotation
  project, delivered separately, together with a re-measurement of the pydantic plugin's
  value at that level. This level is a station.

- **`files = ["src/assistant"]` lives in the configuration, not on the command line**, so
  a bare `mypy` means the same thing in a terminal, in the hook and in CI, and the target
  cannot drift between the three.

- **`python_version = "3.12"`** — the lowest version the project promises, matching
  `ruff`'s existing target. Without the pin the checker follows whichever interpreter runs
  it, and this repository already spans three (the image runs 3.14, the lint job 3.13, the
  test job lets the resolver choose). Measured: 3.12 and 3.14 give identical verdicts
  today, so the pin costs nothing and exists to catch a construct newer than the floor.

- **Libraries without type information are named individually.** The YAML library has
  published stubs, so `types-PyYAML` is a dev dependency and the module that wraps YAML
  I/O is genuinely checked. Three — `cron_descriptor`, `googleapiclient`,
  `google_auth_oauthlib` — have neither stubs nor a marker and are listed by name in a
  single `overrides` entry.

- **The pydantic plugin is enabled** even though it was measured to find nothing at this
  level (263 with and without it), so the fourteen pydantic schema modules are covered as
  strictness rises rather than the plugin appearing later as an unexplained line. One
  consequence: a plugin reference is a hard failure when its package is absent, which is
  why the gate must always run with the project's dependencies installed.

- **The version is pinned to a minor range** (`mypy>=2.3,<2.4`) in the dev dependency
  group, mirroring `ruff`'s treatment and its stated reason: minor releases add checks,
  which would redden pull requests that did not touch types. Unlike `ruff` — whose version
  is duplicated in three places — there is a single source of truth, because the hook takes
  the checker from the project environment rather than provisioning its own.

- **The hook is `local` with `language: system`, `pass_filenames: false`, and no `files:`
  filter.** The third-party mirror was rejected on mechanics: it provisions an isolated
  environment without our dependencies, so it reports unresolved imports instead of a
  check, and restating the dependency list there would bypass the lockfile — a developer
  would then check against a different framework version than CI and the image install.
  Filenames are not passed because checking a subset yields different results than a full
  run, so a partial invocation would report a verdict CI contradicts. The absent path
  filter diverges from the neighbouring web-typecheck hook knowingly: 0.7s warm and up to
  13s cold, in exchange for a hook that a commit which looks unrelated cannot skip.

- **CI runs it as its own job**, provisioning from the committed lockfile with the same
  extras the test job uses. A step on the test job would have saved ~40s of installation
  and made a type failure present as a test failure; the repository already made this
  trade for the front end. The extras are not optional — the verdict was measured to
  differ (264 errors across 45 modules) without the Google extra, and a gate whose result
  depends on which extras happen to be installed is not a gate.

- **No `py.typed` marker is added.** The marker has two effects and only one is wanted:
  internally it would let a future test-suite check resolve the package's own types, but
  externally it promises consumers of the published wheel that the package's types are
  usable — a promise that is hard to withdraw without breaking their builds. The internal
  need is met by checking from source (`mypy_path = "src"`), which carries no external
  commitment.

- **The 263 were fixed, not ratcheted**, and the gate was connected last, so it went from
  absent to enforcing-at-zero and never once enforced against a known-failing tree.

## Considered options

- **Per-module ratcheting** (an `overrides` entry per dirty module, relaxed over time).
  Rejected: it is a permanent exception list, and every listed module carries a silent
  licence to accumulate new errors of the same kind.
- **A recorded error baseline** (a committed list of the 263, failing only on new ones).
  Rejected for the same reason plus a worse failure mode: the baseline needs regenerating
  on every refactor, and a regenerated baseline hides a newly introduced error inside the
  churn.
- **Per-error-code ratcheting** (disable `union-attr` and `attr-defined`, keep the rest).
  Rejected: those two codes were 148 of the 263 and are exactly the class of defect the
  gate was installed to find. Disabling them buys a green run by turning off the value.
- **Straight to `--strict`.** Rejected on cost and on signal: 1,176 errors across 92
  modules, mostly missing annotations, mixes "we found a bug" with "we did not write
  `-> None`", and a reviewer cannot tell them apart in one pull request.
- **A global `ignore_missing_imports`.** Rejected: it would also silence future unresolved
  imports nobody has evaluated. An explicit list documents where the checker is blind.

## Consequences

- **A green local run predicts a green CI run.** Both take the checker from the same
  lockfile pin and run the same configured target.
- **The gate paid for itself before any annotation work.** Two live defects, both
  attachment media types no supported provider accepts, found by running the checker once
  — the kind of failure that produces a bug report with no local reproduction. See the
  amendment appended to ADR 0010 for the routing decision and the two user-visible
  behaviour changes.
- **The codebase now uses the type escape hatches it previously had none of.** Before this
  work: five narrow error-code-scoped ignores, four `Protocol` definitions, and zero
  casts, `TypedDict`s, `TypeVar`s or overloads. It still uses **no `cast`**. It gained
  `Literal` aliases mirroring AG2's closed media-type sets plus three `TypeGuard`s that
  validate a platform-supplied type against them (`attachments.py`), and three more
  protocols where a collaborator was only ever duck-typed.
- **The lifespan guarantees that were comments are now code.** Most of the 99 `union-attr`
  errors traced to a handful of optional collaborators — `ProfileRuntime.gateway` /
  `.tasks` / `.config`, `Gateway._agent`, a channel's app and router — that the code
  assumed present because the runtime only exists after `start()`. Each now has a
  `require_*` accessor that states that guarantee once and raises in the shape
  `ProfileManager.get` already used, rather than letting a `None` travel into a library.
- **Two places the gate is knowingly blind**, both per-line and error-code-scoped, both
  upstream annotation gaps rather than defects in this code, and both self-expiring:
  `warn_unused_ignores` is on, so the gate itself demands their deletion once AG2 fixes
  the declaration.
  - `events.py` — AG2 exports `Field` as a descriptor *class*, so every event field
    declaration reads as assigning a `Field` to a `str`/`dict`/`list`. Reads return `Any`
    at runtime, so there is no defect and no way to annotate out of it.
  - `gateway/core.py` (three lines) — AG2 declares `StreamId = uuid.UUID`, but this
    project keys streams by meaningful strings (`"default"`, `"task-run:r-abc"`) that name
    the event-log files on disk, and AG2 only ever interpolates the id into a path. There
    is no honest conversion: deriving UUIDs would rename every existing log file.

  No `overrides` entry accompanies either, so neither module gains a licence to accumulate
  new errors.
- **One genuine non-typing bug was found on the way.** `SerialStore` promised in its
  docstring to serialise *all* operations on a `KnowledgeStore` but implemented five of
  the protocol's eight; `append`, `read_range` and `on_change` would have raised
  `AttributeError`. All three now delegate under the same lock.
- **The test suite and the helper scripts are still ungated** — a separate later step. 115
  test modules reported 355 errors, but the bulk of that was an artefact of the package
  lacking a type marker, so importing it from an installed distribution read as untyped.
  The source-path configuration this record lands is what makes the real cost measurable.
- **No test asserts that the configuration exists or that the checker passes.** The gate is
  self-verifying: a bare invocation depends on the configured target and fails if that
  configuration is removed, and a test asserting the presence of a configuration line
  would test a file rather than a behaviour. The repository does have prior art for
  asserting packaging invariants in tests, so this is a deliberate departure.
- **The domain glossary in `CONTEXT.md` is untouched.** It is a glossary of product domain
  language; a type checker is a development tool, not a domain term.

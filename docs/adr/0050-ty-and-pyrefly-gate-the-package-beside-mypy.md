# ty and pyrefly gate the Python package beside mypy

`ty` and `pyrefly` run over `src/assistant` next to `mypy` (ADR 0030): in a pre-commit
hook each and as two more steps of the CI `Python typecheck` job. Both land at their
default level, fixed to a true zero, with no baseline and no per-module exceptions.
Their strict levels are the declared destination, not a rejected option.

## Context

mypy was at zero, and a second and third checker still disagreed with it about 219 (ty)
and 225 (pyrefly) places. The checkers infer differently: an unannotated parameter is
`Any` to mypy but a union of every assigned value to ty and pyrefly, so an attribute
initialised from `store=None` was "maybe `None`" to them and unchecked to mypy. The
two newer checkers also follow a library's `except ImportError` fallback assignment,
which mypy skips.

## Decision

- **The level is each checker's default.** Measured over this package:

  | Checker | Default | Strict |
  | --- | --- | --- |
  | ty 0.0.97 | 219 | 1,116 (`rules.all = "error"`) |
  | pyrefly 1.4.0 | 225 | 1,786 (`preset = "strict"`) |

  Strict is mostly missing annotations — ty's `missing-type-argument` (539) and
  pyrefly's `implicit-any-type-argument` (927) and `implicit-any-parameter` (513) — the
  same mix of "bug" and "no `-> None`" that kept mypy off `--strict`. Strictness rises
  in separate pull requests, one checker or rule family at a time.

- **The configuration mirrors mypy's**: target `src/assistant`, source root `src`,
  `python-version = "3.12"`, and pyrefly's `ignore-missing-imports` names the same three
  untyped libraries mypy's `overrides` does. Both find the project's `.venv` themselves.

- **Pinned in the dev extra** — `ty>=0.0.97,<0.0.98` (pre-1.0: every release can add
  checks) and `pyrefly>=1.4,<1.5` — and taken from the project environment by the hooks
  and CI, as mypy is.

- **One CI job, three steps**, each `if: !cancelled()`, so a run reports all three
  verdicts rather than stopping at the first. `--output-format github` annotates the diff.

- **Suppressions stay per-line and code-scoped.** ty honours a `# type: ignore[...]` only
  for `ty:`-prefixed codes, and mypy's `warn_unused_ignores` rejects a `ty:` code in its
  own comment, so the known upstream gaps carry two comments:
  `# type: ignore[arg-type]  # ty: ignore[invalid-argument-type]`. pyrefly honours the
  mypy comment as-is. The sites are the ones ADR 0030 already lists (`StreamId`), plus
  the duck-typed session store in `acp/chats.py` and the ASGI factory in
  `acp/serve_ws.py`, both of which already had a mypy ignore.

## Consequences

- **Defects found on the way:**
  - `TaskStore` called `json.loads` on `read()`'s `None` when a record vanished between
    `list()`/`exists()` and `read()`, reporting it as corruption. `_read_json` now
    returns `None` and the callers skip the record.
  - `memory.py` called `ag2.annotations.Context` — an `Annotated` alias — as a
    constructor. It now builds `ConversationContext`, as `compaction.py` does.
  - `FilteredSkillRuntime` satisfied the `SkillRuntime` protocol only through
    `__getattr__`. The protocol members are now explicit delegates.
  - `_PersistentSession.call_tool` (MCP) ended its retry loop on an implicit `None`
    return, unreachable today; the loop's end now raises instead.
- **Upstream fallbacks are bypassed by importing from submodules** (`ag2.acp.agent`,
  `ag2.acp.config`, `ag2.acp.sessions`, `ag2.a2ui.events`). Those extras are base
  dependencies, so the fallback branch is never taken here.
- **Optional collaborators gained `require_*` accessors** in the ADR 0030 shape:
  `TaskService._require_store()` / `_require_inquiries()` and
  `Gateway.require_permissions()`.
- **A Google discovery `Resource` is typed `Any`** at `build_service`: its API methods
  are created at runtime, and pyrefly reads the installed source, which declares none.
- **The test suite stays ungated** by all three checkers, as ADR 0030 records.

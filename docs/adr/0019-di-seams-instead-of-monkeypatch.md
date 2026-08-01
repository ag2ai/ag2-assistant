# DI seams instead of monkeypatch: the environment is read at the boundary only

`tests/` used to hold 292 `monkeypatch` calls (178 `setattr`, 77 `setenv`, 37 `delenv`)
across 52 files. They are all gone: production code now takes its dependencies as
parameters, so a test builds the real objects instead of rewriting attributes on
imported modules. Reading `os.environ`, `Path.home()` and `PATH` happens **only** in
the entry points — `cli.py`, `paths.Paths.from_env` and `config.load_config`.

## Context

The patches were a symptom, not a habit. The code resolved its own dependencies
inside functions: `config._default_root()` read `Path.home()`, `secrets.load_into_env()`
wrote into `os.environ`, `coding/detect.py` called `shutil.which` without `path=`,
module-level dicts (`model_catalog._cache`, `_inflight`) held per-install state, and
`gateway/core.py` imported `create_agent` by name at call time. With nothing
injectable, the only way to test any of it was to reach into the module.

That cost more than tidiness:

- **Tests asserted implementation.** Nine spies on `manager.reload` checked *that a
  method was called* — they passed even when the reload rebuilt nothing (verified:
  deleting `_reload_all()` did not fail the old test).
- **State was shared.** A module-level catalog cache and a patched `os.environ` leaked
  between tests, so order mattered and autouse fixtures existed to undo the damage.
- **The developer's machine leaked in.** A real Google token on the host changed which
  tools an agent got, so assertions about the tool set failed for one person and
  passed for another. `test_profile_isolation` wrote into the real `~/.ag2assistant`.
- **Whole code paths were never executed.** Patching `asyncio.create_subprocess_exec`
  meant the ACP probe's framing, the tarball extractor, and the httpx request layer
  were tested only as call-argument assertions.

## Decision

- **`Paths` is the only source of on-disk layout**, resolved once by
  `Paths.from_env(env, home)`. It carries `root`, `workspace`, `codex_auth` and `home`;
  everything below takes the resolved value.
- **`resolve_config(env, paths)` is pure**; `load_config()` is the boundary that feeds
  it the process environment. Host facts a leaf would otherwise sniff for itself —
  `search_path`, `acp_bridge`, `secret_env`, `tz_unset_in_container` — are fields on
  `Config`, filled at that boundary.
- **Module-level function sets became classes bound to `Paths`:** `SecretStore`,
  `ProfileRegistry`, `LlmConfigStore`, `LiveConfigStore`, `ModelCatalog`, `GoogleAuth`,
  `CodexAuth`, `BridgeClient`. Instance state replaced module state, so two installs
  in one process never share a cache.
- **`secrets` never writes to `os.environ`.** `env_overlay()` / `merged_env(env)` return
  a mapping the caller layers in.
- **External binaries are found on an explicit `search_path`**, and in tests they are
  **real executable scripts** on disk (`tests/support/stubs.py::write_stub`) rather than
  a patched `shutil.which`.
- **HTTP runs through a real `httpx.Client`** over `httpx.MockTransport`
  (`tests/support/http.py`), so redirects, headers, and error mapping execute for real.
  Where a third-party client can't take a transport (ag2's `SkillsClient`), the test
  subclasses it and replaces only the transport, leaving the vendor's code running.
- **Collaborators arrive as factories:** `agent_factory`, `channel_factory`,
  `title_factory`, `summary_factory`, `connector_factory`, `environment_factory`.
  Production defaults live in the constructors, so callers that don't care pass nothing.
- **Tuning constants are parameters**, not module globals to be rewritten:
  `probe_timeout`, `cache_ttl`, `idle_close_s`, `message_limit`, `max_file_bytes`.
- **Assertions target observable effects.** Instead of spying on `manager.reload`, a test
  reads the skill catalog of the *rebuilt* agent — the same value that reaches the
  system prompt.

`tests/test_no_global_defaults.py` enforces the boundary as an AST scan (comments and
docstrings can't trip it), with a pinned list of deferred modules that may only shrink.

## Considered options

- **Keep `monkeypatch`, add discipline** — rejected: the patches were compensating for
  hidden dependency resolution, so no amount of care would make the tests exercise the
  real path. The Docker-detection and ACP-probe tests only started covering their code
  once injection replaced patching.
- **Compat shims for the old module-level API** — rejected by the repo's own rule
  (AGENTS.md: no compat shims). Old forms were deleted, not wrapped.
- **A pre-commit hook / CI step banning the word `monkeypatch`** — considered and
  dropped: the invariant that matters (nothing below the boundary reads the ambient
  world) is already a test, and a grep-based gate would police the symptom.
- **Threading `paths` through every leaf function** — rejected where a `Config` was
  already in hand; `Config.paths` avoids adding a parameter to dozens of call sites.

## Consequences

- **Public signatures changed** across `profiles.*`, `secrets.*`, `llm_configs.*`,
  `live_configs.*`, `detect.*`, `load_config`, `Config()`. There is no deprecation path.
- **Real defects surfaced and were fixed** while the seams went in, each one invisible to
  the patched tests: the ACP bridge spawned the *unresolved* adapter name (so the host's
  real `claude-agent-acp` started instead of the stub); nine `load_config()` calls inside
  `create_app` ignored the manager's layout and wrote into `$HOME`; channel tokens saved
  from the web UI never reached a restarting channel; `POST /api/live-configs/test`
  returned 500 on a cleared key; the Codex login route hung the whole suite on a real
  loopback listener.
- **Coverage rose to 80.32 % from 79.64 %** even though implementation-detail assertions
  were dropped — real subprocesses, archives and HTTP now execute.
- **The suite got slower** (26.6 s → ~33 s) because stubs are real processes and files.
  The acceptance ceiling is 50 s.
- **Test stubs have a protocol obligation.** A stub standing in for a protocol peer must
  read each request before answering and stay alive; one that answers blindly and exits
  closes the pipe under the prober's next write. That was a real, load-dependent flake
  (reproduced 12 times in 250 runs under CPU load, zero after the fix).
- **One accepted exception remains:** `codex_auth._const` reads `os.environ` once at
  import for the reverse-engineered OpenAI endpoint constants. Their override is a
  documented escape hatch (`.env.example`) and they are imported by name elsewhere, so
  passing them as values would touch every model-config builder for a debug-only knob.
  It is pinned in the gate's deferred list.
